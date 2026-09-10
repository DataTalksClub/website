"""Transactional projection of parsed course-repository curriculum sources."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import F, Max
from django.utils import timezone

from courses.models import (
    AnswerTypes,
    Cohort,
    CohortSharedModule,
    Course,
    CourseCurriculumImportRun,
    CurriculumFlowItem,
    CurriculumFormat,
    CurriculumSource,
    DeliveryMode,
    Homework,
    HomeworkState,
    Module,
    Project,
    Question,
    QuestionTypes,
    SharedCurriculum,
    SharedCurriculumAsset,
    SharedLesson,
    SharedModule,
    Submission,
    Unit,
)
from courses.models.curriculum_import import (
    REPOSITORY_BRANCH_PATTERN,
    REPOSITORY_COMPONENT_PATTERN,
    SHA1_PATTERN,
    SHA256_PATTERN,
    SOURCE_CONTENT_ID_PATTERN,
    SOURCE_STABLE_ID_PATTERN,
    SOURCE_VERSION_PATTERN,
    validate_source_path,
)
from courses.registration import render_markdown
from courses.services.curriculum_source import (
    CohortSource,
    CourseRepositorySource,
    HomeworkQuestionSource,
    HomeworkSource,
    ModuleFlowSource,
    ModuleSource,
    ProjectFlowSource,
    UnitSource,
)

_QUESTION_TYPES = {
    "multiple_choice": QuestionTypes.MULTIPLE_CHOICE.value,
    "checkboxes": QuestionTypes.CHECKBOXES.value,
    "free_form": QuestionTypes.FREE_FORM.value,
    "free_form_long": QuestionTypes.FREE_FORM_LONG.value,
}
_ANSWER_TYPES = {
    None: None,
    "any": AnswerTypes.ANY.value,
    "float": AnswerTypes.FLOAT.value,
    "integer": AnswerTypes.INTEGER.value,
    "exact_string": AnswerTypes.EXACT_STRING.value,
    "contains_string": AnswerTypes.CONTAINS_STRING.value,
}
_HOMEWORK_STATES = {
    "closed": HomeworkState.CLOSED.value,
    "open": HomeworkState.OPEN.value,
    "scored": HomeworkState.SCORED.value,
}

_ASSET_CONTENT_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ipynb": "application/x-ipynb+json",
    ".py": "text/x-python",
}


def _asset_content_type(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    return _ASSET_CONTENT_TYPES.get(suffix, "application/octet-stream")


def _rewrite_image_references(markdown: str, asset_paths: Mapping[str, str]) -> str:
    """Point relative image references at their managed public paths."""

    pattern = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")

    def replace(match: re.Match[str]) -> str:
        alt, target = match.group(1), match.group(2)
        public = asset_paths.get(target)
        if public is None:
            return match.group(0)
        return f"![{alt}]({public})"

    return pattern.sub(replace, markdown)


@dataclass(frozen=True, slots=True)
class CurriculumImportDiagnostic:
    """One bounded diagnostic safe to persist and return to a caller."""

    code: str
    source_path: str = "."
    pointer: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code[:128],
            "source_path": (self.source_path or ".")[:512],
            "pointer": self.pointer[:512],
        }


class CurriculumImportError(RuntimeError):
    """A source-safe import rejection that never reflects source values."""

    def __init__(
        self,
        code: str,
        *,
        source_path: str = ".",
        pointer: str = "",
    ) -> None:
        self.code = code[:128]
        self.diagnostics = (
            CurriculumImportDiagnostic(
                code=self.code,
                source_path=source_path,
                pointer=pointer,
            ),
        )
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class CurriculumImportCommand:
    """Validated source identity and immutable parser output for one import."""

    source: CourseRepositorySource
    source_uuid: UUID
    source_stable_id: str
    repository_owner: str
    repository_name: str
    repository_branch: str
    commit_sha: str
    source_checksums: Mapping[str, str] | None = None
    manifest_checksum: str | None = None
    preserve_existing_records: bool = False
    # Schema-2 imports carry the raw snapshot so current relative assets are
    # validated and stored into managed storage from the same commit.  It is
    # import input only: nothing on a request path ever reads it.
    snapshot: Mapping[str, bytes] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, CourseRepositorySource):
            raise CurriculumImportError("invalid_source_graph")
        if not isinstance(self.source_uuid, UUID):
            raise CurriculumImportError("invalid_source_uuid")
        if type(self.preserve_existing_records) is not bool:
            raise CurriculumImportError("invalid_preservation_mode")
        if self.source.schema_version == 2 and not isinstance(self.snapshot, Mapping):
            raise CurriculumImportError("shared_import_requires_snapshot")
        validators = (
            (self.source_stable_id, SOURCE_STABLE_ID_PATTERN, "invalid_source_stable_id"),
            (self.repository_owner, REPOSITORY_COMPONENT_PATTERN, "invalid_repository_owner"),
            (self.repository_name, REPOSITORY_COMPONENT_PATTERN, "invalid_repository_name"),
            (self.repository_branch, REPOSITORY_BRANCH_PATTERN, "invalid_repository_branch"),
            (self.commit_sha, SHA1_PATTERN, "invalid_commit_sha"),
            (self.source.parser_version, SOURCE_VERSION_PATTERN, "invalid_parser_version"),
        )
        for value, pattern, code in validators:
            if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
                raise CurriculumImportError(code)
        if self.source.commit_sha is not None and self.source.commit_sha != self.commit_sha:
            raise CurriculumImportError("source_commit_mismatch")
        if self.manifest_checksum is not None and (
            not isinstance(self.manifest_checksum, str)
            or re.fullmatch(SHA256_PATTERN, self.manifest_checksum) is None
        ):
            raise CurriculumImportError("invalid_manifest_checksum")

        checksums = dict(self.source_checksums or {})
        for path, checksum in checksums.items():
            try:
                validate_source_path(path)
            except ValidationError as error:
                raise CurriculumImportError("invalid_source_checksum_path") from error
            if not isinstance(checksum, str) or re.fullmatch(SHA256_PATTERN, checksum) is None:
                raise CurriculumImportError(
                    "invalid_source_checksum",
                    source_path=path,
                )
        object.__setattr__(self, "source_checksums", MappingProxyType(checksums))


@dataclass(frozen=True, slots=True)
class CurriculumImportResult:
    """Objects and bounded evidence produced by an accepted import."""

    run: CourseCurriculumImportRun
    course: Course
    cohorts: tuple[Cohort, ...]
    counts: Mapping[str, int]
    replayed: bool


def _canonical_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise CurriculumImportError("unsupported_source_value")


def _checksum(value: object) -> str:
    encoded = json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_checksum(command: CurriculumImportCommand) -> str:
    return command.manifest_checksum or _checksum(command.source)


def _validate_model(instance: Any) -> None:
    instance.full_clean(validate_unique=False, validate_constraints=False)


class _CurriculumImporter:
    def __init__(self, command: CurriculumImportCommand) -> None:
        self.command = command
        self.commit_sha = command.commit_sha
        self.checksums = command.source_checksums or {}
        self.counts = {
            "courses": 1,
            "cohorts": 0,
            "modules": 0,
            "units": 0,
            "homeworks": 0,
            "questions": 0,
            "flow_items": 0,
            "projects": 0,
            "shared_modules": 0,
            "shared_lessons": 0,
            "placements": 0,
            "archive_cohorts": 0,
        }

    def apply(self) -> tuple[Course, tuple[Cohort, ...], Mapping[str, int]]:
        self._validate_repository_identity()
        course = self._upsert_course()
        imported_cohorts: list[Cohort] = []
        if self.command.source.schema_version == 2:
            shared_curriculum = self._upsert_shared_curriculum(course)
            self._import_shared_modules(shared_curriculum)
            for source in self.command.source.cohorts:
                cohort = self._upsert_cohort(
                    course,
                    source,
                    shared_curriculum=shared_curriculum,
                )
                imported_cohorts.append(cohort)
                self.counts["cohorts"] += 1
                if source.curriculum == CurriculumSource.CURRENT:
                    self._import_shared_placements(shared_curriculum, cohort, source)
                else:
                    self._import_archive_cohort(cohort, source)
            return course, tuple(imported_cohorts), MappingProxyType(dict(self.counts))
        for source in self.command.source.cohorts:
            if source.is_implicit_legacy:
                continue
            cohort = self._upsert_cohort(course, source)
            imported_cohorts.append(cohort)
            self.counts["cohorts"] += 1
            if source.format == CurriculumFormat.MODULES:
                self._import_modules_cohort(cohort, source)
            else:
                self._validate_legacy_transition(cohort)
        return course, tuple(imported_cohorts), MappingProxyType(dict(self.counts))

    def _provenance(self, source: object, path: str, content_id: str) -> dict[str, object]:
        if (
            not isinstance(content_id, str)
            or re.fullmatch(SOURCE_CONTENT_ID_PATTERN, content_id) is None
        ):
            raise CurriculumImportError(
                "invalid_source_content_id",
                source_path=path,
                pointer="/content_id",
            )
        return {
            "source_content_id": content_id,
            "source_path": path,
            "source_commit_sha": self.commit_sha,
            "source_checksum": self.checksums.get(path, _checksum(source)),
        }

    def _validate_repository_identity(self) -> None:
        parsed = urlsplit(self.command.source.course.repository_url)
        expected_path = f"/{self.command.repository_owner}/{self.command.repository_name}"
        actual_path = parsed.path.removesuffix(".git").rstrip("/")
        if (
            parsed.scheme not in {"http", "https"}
            or (parsed.hostname or "").casefold() != "github.com"
            or actual_path.casefold() != expected_path.casefold()
            or parsed.query
            or parsed.fragment
        ):
            raise CurriculumImportError(
                "repository_identity_mismatch",
                source_path=self.command.source.course.source_path,
                pointer="/repository_url",
            )

    def _upsert_course(self) -> Course:
        source = self.command.source.course
        # The course.yaml the repository publishes declares its own family slug
        # directly (e.g. ai-dev-tools-zoomcamp keeps its repository's own name,
        # same as every other course family) -- no normalization needed.
        family_slug = source.slug
        source_id = source.content_id
        by_stable = Course.objects.filter(source_stable_id=self.command.source_stable_id).first()
        by_content = Course.objects.filter(source_content_id=source_id).first()
        if by_stable is not None and by_content is not None and by_stable.pk != by_content.pk:
            raise CurriculumImportError(
                "course_source_identity_conflict", source_path=source.source_path
            )
        course = by_stable or by_content
        if course is not None:
            if course.source_content_id not in {None, source_id}:
                raise CurriculumImportError(
                    "course_content_id_change", source_path=source.source_path
                )
            if course.source_stable_id not in {None, self.command.source_stable_id}:
                raise CurriculumImportError(
                    "course_source_ownership_conflict", source_path=source.source_path
                )
        else:
            slug_match = Course.objects.filter(slug=family_slug).first()
            if slug_match is not None and slug_match.source_content_id is not None:
                raise CurriculumImportError(
                    "course_slug_collision", source_path=source.source_path, pointer="/slug"
                )
            course = slug_match or Course()

        slug_collision = Course.objects.filter(slug=family_slug).exclude(pk=course.pk).exists()
        if slug_collision:
            raise CurriculumImportError(
                "course_slug_collision", source_path=source.source_path, pointer="/slug"
            )
        if (
            course.pk
            and course.slug != family_slug
            and Submission.objects.filter(homework__course__course=course).exists()
        ):
            raise CurriculumImportError(
                "protected_course_slug_change", source_path=source.source_path, pointer="/slug"
            )

        course.slug = family_slug
        course.title = source.title
        if source.description is not None:
            # A repository without ``SITE.md`` describes no website copy, so the stored
            # description stands.  Assigning unconditionally is what overwrote three
            # families' curated text with their README banners.
            course.description = source.description
        course.outcome = source.outcome
        course.github_repo_url = source.repository_url
        course.docs_url = source.docs_url
        course.faq_document_url = source.faq_url
        course.social_media_hashtag = source.hashtag
        course.visible = source.published
        course.source_stable_id = self.command.source_stable_id
        for field, value in self._provenance(source, source.source_path, source.content_id).items():
            setattr(course, field, value)
        _validate_model(course)
        course.save()
        return course

    def _cohort_slug(self, course: Course, source: CohortSource) -> str:
        """Return the published cohort slug for one source cohort.

        A repository whose course slug was normalized also states its cohort
        ``legacy_slug`` with the repository prefix (``ai-dev-tools-zoomcamp-2026``
        for the published ``ai-dev-tools-2026``).  Rewrite that one prefix so the
        cohort stays addressable under its family, and leave every other legacy
        slug byte-for-byte as the source states it.
        """

        if not source.legacy_slug:
            return f"{course.slug}-{source.identifier}"
        source_slug = self.command.source.course.slug
        prefix = f"{source_slug}-"
        if course.slug != source_slug and source.legacy_slug.startswith(prefix):
            return f"{course.slug}-{source.legacy_slug.removeprefix(prefix)}"
        return source.legacy_slug

    def _upsert_cohort(
        self,
        course: Course,
        source: CohortSource,
        *,
        shared_curriculum: SharedCurriculum | None = None,
    ) -> Cohort:
        if source.content_id is None or source.source_path is None:
            raise CurriculumImportError("explicit_cohort_source_identity_missing")
        source_id = source.content_id
        target_slug = self._cohort_slug(course, source)
        by_content = Cohort.objects.filter(course=course, source_content_id=source_id).first()
        by_identifier = Cohort.objects.filter(course=course, identifier=source.identifier).first()
        by_slug = Cohort.objects.filter(slug=target_slug).first()
        collisions = {row.pk for row in (by_content, by_identifier, by_slug) if row is not None}
        if len(collisions) > 1:
            raise CurriculumImportError("cohort_identity_collision", source_path=source.source_path)
        cohort = by_content
        if cohort is None:
            candidate = by_identifier or by_slug
            if candidate is not None and candidate.source_content_id is not None:
                raise CurriculumImportError(
                    "cohort_source_identity_conflict", source_path=source.source_path
                )
            cohort = candidate or Cohort(course=course)

        if Cohort.objects.filter(slug=target_slug).exclude(pk=cohort.pk).exists():
            raise CurriculumImportError(
                "cohort_slug_collision", source_path=source.source_path, pointer="/legacy_slug"
            )
        if (
            Cohort.objects.filter(course=course, identifier=source.identifier)
            .exclude(pk=cohort.pk)
            .exists()
        ):
            raise CurriculumImportError(
                "cohort_identifier_collision", source_path=source.source_path, pointer="/identifier"
            )

        existing = cohort.pk is not None
        cohort.course = course
        cohort.slug = target_slug
        cohort.identifier = source.identifier
        if not (existing and self.command.preserve_existing_records):
            cohort.year = source.year or cohort.year
            cohort.title = source.title or cohort.title
            cohort.description = source.description or ""
            cohort.start_date = source.start_date
            cohort.end_date = source.end_date
            cohort.visible = bool(source.published)
        if source.delivery is not None:
            # Schema-2 projection: the manifest discriminators map one way
            # onto the database fields; nothing infers them back.
            cohort.delivery_mode = source.delivery
            cohort.curriculum_source = source.curriculum
            if source.year is not None:
                cohort.year = source.year
            elif cohort.pk is None:
                # ``year`` is display metadata, never identity.  A v2 manifest
                # need not carry one, so new rows get a deterministic,
                # collision-free value while the legacy (course, year)
                # uniqueness stays intact.
                cohort.year = self._display_year(course, source)
            # title/description are retired v2 cohort.yaml fields, same as
            # year/legacy_slug: no manifest states them, so a blank value
            # (new row, or an old row that never had one) gets a derived
            # one rather than failing Cohort.full_clean's non-blank check.
            if not cohort.title:
                cohort.title = self._display_cohort_title(course, source)
            if not cohort.description:
                cohort.description = self._display_cohort_description(course, source)
            if source.curriculum == CurriculumSource.CURRENT:
                cohort.curriculum_format = CurriculumFormat.SHARED
                cohort.shared_curriculum = shared_curriculum
                cohort.archive_notice_path = ""
                cohort.archive_url = ""
                cohort.archive_commit_sha = ""
            else:
                # A github_archive cohort keeps its existing curriculum
                # format, has no shared placement, and stores only the
                # notice path plus the importer-derived immutable URL.
                cohort.shared_curriculum = None
                cohort.archive_notice_path = source.archive_notice_path or ""
                cohort.archive_commit_sha = self.commit_sha
                cohort.archive_url = self._derived_archive_url(
                    source.archive_notice_path,
                    source_path=source.source_path or ".",
                )
        else:
            cohort.curriculum_format = source.format
        for field, value in self._provenance(source, source.source_path, source.content_id).items():
            setattr(cohort, field, value)
        _validate_model(cohort)
        cohort.save()
        return cohort

    def _display_year(self, course: Course, source: CohortSource) -> int:
        if source.identifier.isdigit():
            return int(source.identifier)
        if source.start_date is not None:
            return source.start_date.year
        known = list(Cohort.objects.filter(course=course).values_list("year", flat=True))
        known.extend(
            int(candidate.identifier)
            for candidate in self.command.source.cohorts
            if candidate.identifier.isdigit()
        )
        return (max(known) if known else 2026) + 1

    def _display_cohort_title(self, course: Course, source: CohortSource) -> str:
        if source.delivery == DeliveryMode.SELF_PACED:
            return f"{course.title} (self-paced)"
        return f"{course.title} {source.identifier}"

    def _display_cohort_description(self, course: Course, source: CohortSource) -> str:
        if source.delivery == DeliveryMode.SELF_PACED:
            return f"The self-paced delivery of {course.title}."
        return f"The {source.identifier} live delivery of {course.title}."

    def _derived_archive_url(self, notice_path: str | None, *, source_path: str) -> str:
        """Derive the immutable GitHub blob URL for an archive notice.

        The repository identity has already been validated against the import
        command, and the commit is the incoming full SHA -- never a branch and
        never a commit the manifest named itself.
        """

        if not notice_path:
            raise CurriculumImportError("archive_notice_missing", source_path=source_path)
        repository_url = self.command.source.course.repository_url.rstrip("/")
        return f"{repository_url}/blob/{self.commit_sha}/{notice_path}"

    def _validate_legacy_transition(self, cohort: Cohort) -> None:
        if Module.objects.filter(cohort=cohort, source_content_id__isnull=False).exists():
            raise CurriculumImportError(
                "protected_curriculum_format_change",
                source_path=cohort.source_path or ".",
                pointer="/format",
            )

    # -- schema-2 shared projection -----------------------------------------

    _IMAGE_REFERENCE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
    _EXTERNAL_REFERENCE = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*:|//|/|#)")

    def _upsert_shared_curriculum(self, course: Course) -> SharedCurriculum:
        source = self.command.source.course
        shared, created = SharedCurriculum.objects.get_or_create(
            course=course,
            defaults={
                "parser_version": self.command.source.parser_version,
                "updated_at": timezone.now(),
                **self._provenance(source, source.source_path, source.content_id),
            },
        )
        if not created:
            shared.parser_version = self.command.source.parser_version
            shared.updated_at = timezone.now()
            for field, value in self._provenance(
                source, source.source_path, source.content_id
            ).items():
                setattr(shared, field, value)
            _validate_model(shared)
            shared.save(
                update_fields=(
                    "parser_version",
                    "updated_at",
                    "source_content_id",
                    "source_path",
                    "source_commit_sha",
                    "source_checksum",
                )
            )
        return shared

    def _import_shared_modules(self, shared_curriculum: SharedCurriculum) -> None:
        incoming_ids: set[str] = set()
        existing = SharedModule.objects.filter(
            curriculum=shared_curriculum, source_content_id__isnull=False
        )
        offset = (
            (existing.aggregate(maximum=Max("position"))["maximum"] or 0) + existing.count() + 1_000
        )
        # Stage old positions out of the way so a reshuffle or a retirement
        # cannot collide with an incoming module's position.
        existing.update(position=F("position") + offset)
        for position, module_source in enumerate(self.command.source.modules):
            incoming_ids.add(module_source.content_id)
            shared_module = self._upsert_shared_module(shared_curriculum, module_source, position)
            self._upsert_shared_lessons(shared_module, module_source)
        # Source-scoped soft retirement: removed modules stay as inactive rows
        # for rollback/audit and never disappear under existing read state.
        stale_modules = SharedModule.objects.filter(
            curriculum=shared_curriculum,
            source_content_id__isnull=False,
        ).exclude(source_content_id__in=incoming_ids)
        now = timezone.now()
        stale_modules.update(published=False, retired_at=now)
        stale_lessons = SharedLesson.objects.filter(
            module__curriculum=shared_curriculum,
            module__source_content_id__isnull=False,
            source_content_id__isnull=False,
        ).exclude(module__source_content_id__in=incoming_ids)
        stale_lessons.update(published=False, retired_at=now)

    def _upsert_shared_module(
        self,
        shared_curriculum: SharedCurriculum,
        source: ModuleSource,
        position: int,
    ) -> SharedModule:
        source_id = source.content_id
        shared_module, created = SharedModule.objects.update_or_create(
            curriculum=shared_curriculum,
            source_content_id=source_id,
            defaults={
                "slug": source.slug,
                "title": source.title,
                "position": position,
                "overview_markdown": source.overview_markdown or "",
                "overview_rendered_html": (
                    render_markdown(source.overview_markdown) if source.overview_markdown else ""
                ),
                "published": True,
                "retired_at": None,
                **self._provenance(source, source.source_path, source.content_id),
            },
        )
        if created:
            self.counts["shared_modules"] += 1
        return shared_module

    def _upsert_shared_lessons(self, shared_module: SharedModule, source: ModuleSource) -> None:
        incoming_ids = {unit.content_id for unit in source.units}
        existing = SharedLesson.objects.filter(module=shared_module)
        offset = (
            (existing.aggregate(maximum=Max("position"))["maximum"] or 0) + existing.count() + 1_000
        )
        existing.update(position=F("position") + offset)
        for position, unit_source in enumerate(source.units):
            self._upsert_shared_lesson(shared_module, unit_source, position)
            self.counts["shared_lessons"] += 1
        stale_lessons = SharedLesson.objects.filter(module=shared_module).exclude(
            source_content_id__in=incoming_ids
        )
        stale_lessons.update(published=False, retired_at=timezone.now())

    def _asset_references(self, unit_source: UnitSource) -> list[tuple[str, bool]]:
        """Relative image references plus declared code sources of one lesson.

        Image references are Markdown-relative to the lesson's module; the
        parser already resolved code sources to repository-relative paths.
        """

        references: list[tuple[str, bool]] = []
        for match in self._IMAGE_REFERENCE.finditer(unit_source.markdown):
            target = match.group(1)
            if self._EXTERNAL_REFERENCE.match(target):
                # Absolute external HTTPS references stay external untouched.
                continue
            references.append((target, False))
        for code in unit_source.metadata.code:
            references.append((code.source_path, True))
        return references

    def _resolve_asset_source_path(
        self, module_dir: str, reference: str, *, lesson_path: str
    ) -> str:
        resolved = posixpath.normpath(
            (PurePosixPath(module_dir) / PurePosixPath(reference)).as_posix()
        )
        if resolved == "." or resolved.startswith("../"):
            raise CurriculumImportError(
                "shared_asset_reference_invalid",
                source_path=lesson_path,
            )
        if resolved not in (self.command.snapshot or {}):
            raise CurriculumImportError(
                "shared_asset_reference_missing",
                source_path=lesson_path,
            )
        return resolved

    def _import_shared_assets(
        self,
        lesson: SharedLesson,
        unit_source: UnitSource,
        module_dir: str,
    ) -> dict[str, str]:
        """Validate, store, and register the lesson's relative assets.

        Returns a mapping from repository-relative reference to stable public
        path.  Storage keys embed the lesson's stable ID, the full commit SHA,
        and the content checksum, so a re-import can never overwrite bytes
        that are currently being served.
        """

        mapping: dict[str, str] = {}
        resolved_paths: set[str] = set()
        for reference, already_resolved in self._asset_references(unit_source):
            source_path = (
                reference
                if already_resolved
                else self._resolve_asset_source_path(
                    module_dir, reference, lesson_path=unit_source.source_path
                )
            )
            resolved_paths.add(source_path)
        # A moved module directory changes resolved source paths; the old rows
        # would collide on their content-addressed public paths, so they go
        # first.  This is one transaction: a later failure restores them.  Row
        # deletion never deletes stored bytes, which stay content-addressed
        # for the commits that reference them.
        SharedCurriculumAsset.objects.filter(lesson=lesson).exclude(
            source_path__in=resolved_paths
        ).delete()
        for reference, already_resolved in self._asset_references(unit_source):
            source_path = (
                reference
                if already_resolved
                else self._resolve_asset_source_path(
                    module_dir, reference, lesson_path=unit_source.source_path
                )
            )
            raw = self.command.snapshot[source_path]
            checksum = hashlib.sha256(raw).hexdigest()
            filename = PurePosixPath(source_path).name
            content_id = str(lesson.source_content_id)
            storage_key = f"shared-lesson/{content_id}/{self.commit_sha}/{checksum}/{filename}"
            public_path = f"/course-assets/lessons/{content_id}/{checksum}/{filename}"
            SharedCurriculumAsset.objects.update_or_create(
                lesson=lesson,
                source_path=source_path,
                defaults={
                    "public_path": public_path,
                    "storage_key": storage_key,
                    "content_type": _asset_content_type(filename),
                    "byte_size": len(raw),
                    **self._provenance(unit_source, source_path, str(unit_source.content_id)),
                },
            )
            stored = default_storage.exists(storage_key)
            if not stored:
                default_storage.save(storage_key, ContentFile(raw))
            mapping[reference] = public_path
        return mapping

    def _upsert_shared_lesson(
        self,
        shared_module: SharedModule,
        unit_source: UnitSource,
        position: int,
    ) -> SharedLesson:
        source_id = unit_source.content_id
        module_dir = str(PurePosixPath(unit_source.source_path).parent)
        shared_lesson, _ = SharedLesson.objects.update_or_create(
            module=shared_module,
            source_content_id=source_id,
            defaults={
                "slug": unit_source.slug,
                "title": unit_source.title,
                "position": position,
                "content_markdown": unit_source.markdown,
                "video_url": unit_source.metadata.video_url or "",
                "code_sources": [
                    {"label": code.label, "source_path": code.source_path}
                    for code in unit_source.metadata.code
                ],
                "published": True,
                "retired_at": None,
                **self._provenance(unit_source, unit_source.source_path, unit_source.content_id),
            },
        )
        # Assets are validated and stored after the row exists (asset rows key
        # on it) but before the rendered HTML is finalised: a missing or
        # escaping reference fails the whole import atomically.
        asset_paths = self._import_shared_assets(shared_lesson, unit_source, module_dir)
        markdown = _rewrite_image_references(unit_source.markdown, asset_paths)
        shared_lesson.content_markdown = markdown
        shared_lesson.rendered_html = render_markdown(markdown)
        shared_lesson.save(update_fields=("content_markdown", "rendered_html"))
        return shared_lesson

    def _import_shared_placements(
        self,
        shared_curriculum: SharedCurriculum,
        cohort: Cohort,
        source: CohortSource,
    ) -> None:
        homework_by_path = {
            homework.source_path: homework for homework in self.command.source.homeworks
        }
        module_by_slug = {module.slug: module for module in self.command.source.modules}
        incoming_module_ids = {module.content_id for module in self.command.source.modules}
        # Placements bound to modules this source no longer declares go first:
        # their positions and module binding would collide with the incoming
        # rows otherwise.  A self-paced cohort binds nothing, so it correctly
        # ends with zero placements and zero manufactured homework rows.
        CohortSharedModule.objects.filter(cohort=cohort).exclude(
            shared_module__source_content_id__in=incoming_module_ids
        ).delete()
        kept_placements: set[int] = set()
        for position, binding in enumerate(source.homework_bindings):
            if binding.module is None:
                raise CurriculumImportError(
                    "archive_module_reference",
                    source_path=source.source_path or ".",
                )
            module_source = module_by_slug.get(binding.module)
            if module_source is None:
                raise CurriculumImportError(
                    "curriculum_source_mismatch",
                    source_path=source.source_path or ".",
                )
            homework_source = homework_by_path.get(binding.source)
            if homework_source is None:
                raise CurriculumImportError(
                    "homework_source_contract_invalid",
                    source_path=source.source_path or ".",
                )
            homework = self._upsert_homework(cohort, homework_source)
            shared_module = SharedModule.objects.filter(
                curriculum=shared_curriculum,
                source_content_id=module_source.content_id,
            ).first()
            if shared_module is None:
                raise CurriculumImportError(
                    "shared_module_missing",
                    source_path=source.source_path or ".",
                )
            placement = CohortSharedModule.objects.update_or_create(
                cohort=cohort,
                shared_module=shared_module,
                defaults={
                    "position": position,
                    "terminal_homework": homework,
                },
            )[0]
            _validate_model(placement)
            kept_placements.add(placement.pk)
            self.counts["placements"] += 1
        CohortSharedModule.objects.filter(cohort=cohort).exclude(pk__in=kept_placements).delete()

    def _import_archive_cohort(self, cohort: Cohort, source: CohortSource) -> None:
        """An archive cohort never creates shared rows or placements.

        Only an explicitly mapped ``module: null`` homework may be registered,
        so historical submissions keep their operational assignment.
        """

        homework_by_path = {
            homework.source_path: homework for homework in self.command.source.homeworks
        }
        for binding in source.homework_bindings:
            if binding.module is not None:
                raise CurriculumImportError(
                    "archive_module_reference",
                    source_path=source.source_path or ".",
                )
            homework_source = homework_by_path.get(binding.source)
            if homework_source is None:
                raise CurriculumImportError(
                    "homework_source_contract_invalid",
                    source_path=source.source_path or ".",
                )
            self._upsert_homework(cohort, homework_source)
        CohortSharedModule.objects.filter(cohort=cohort).delete()
        self.counts["archive_cohorts"] += 1

    def _import_modules_cohort(self, cohort: Cohort, source: CohortSource) -> None:
        project_by_position: dict[int, Project] = {}
        for position, item in enumerate(source.flow):
            if not isinstance(item, ProjectFlowSource):
                continue
            project = Project.objects.filter(course=cohort, slug=item.slug).first()
            if project is None:
                raise CurriculumImportError(
                    "project_reference_missing",
                    source_path=source.source_path or ".",
                    pointer=f"/flow/{position}/project",
                )
            project_by_position[position] = project

        module_items = [item for item in source.flow if isinstance(item, ModuleFlowSource)]
        incoming_module_ids = {item.module.content_id for item in module_items}
        incoming_homework_ids = {item.homework.content_id for item in module_items}
        self._validate_removals(cohort, incoming_module_ids, incoming_homework_ids)
        CurriculumFlowItem.objects.filter(cohort=cohort).delete()
        self._stage_positions(Module, cohort=cohort)

        module_by_source_id: dict[str, Module] = {}
        for module_position, item in enumerate(module_items):
            homework = self._upsert_homework(cohort, item.homework)
            module = self._upsert_module(cohort, item.module, homework, module_position)
            self._upsert_units(module, item.module)
            module_by_source_id[item.module.content_id] = module

        if not self.command.preserve_existing_records:
            self._delete_stale_source_rows(cohort, incoming_module_ids, incoming_homework_ids)

        for flow_position, item in enumerate(source.flow):
            if isinstance(item, ModuleFlowSource):
                module = module_by_source_id[item.module.content_id]
                CurriculumFlowItem.objects.create(
                    cohort=cohort,
                    position=flow_position,
                    module=module,
                )
            else:
                CurriculumFlowItem.objects.create(
                    cohort=cohort,
                    position=flow_position,
                    project=project_by_position[flow_position],
                )
                self.counts["projects"] += 1
            self.counts["flow_items"] += 1

    @staticmethod
    def _stage_positions(model: type[Any], **scope: object) -> None:
        queryset = model.objects.filter(**scope, source_content_id__isnull=False)
        maximum = model.objects.filter(**scope).aggregate(maximum=Max("position"))["maximum"] or 0
        queryset.update(position=F("position") + maximum + queryset.count() + 1_000)

    def _upsert_homework(self, cohort: Cohort, source: HomeworkSource) -> Homework:
        source_id = source.content_id
        homework = Homework.objects.filter(course=cohort, source_content_id=source_id).first()
        slug_match = Homework.objects.filter(course=cohort, slug=source.slug).first()
        if slug_match is not None and slug_match.source_content_id not in {None, source_id}:
            raise CurriculumImportError(
                "homework_source_identity_conflict",
                source_path=source.source_path,
                pointer="/content_id",
            )
        if self.command.preserve_existing_records and homework is None and slug_match is not None:
            homework = slug_match
        if homework is None:
            if slug_match is not None:
                raise CurriculumImportError(
                    "homework_slug_collision", source_path=source.source_path, pointer="/slug"
                )
            homework = Homework(course=cohort, state=_HOMEWORK_STATES[source.initial_state])
        elif slug_match is not None and slug_match.pk != homework.pk:
            raise CurriculumImportError(
                "homework_slug_collision", source_path=source.source_path, pointer="/slug"
            )

        if self.command.preserve_existing_records and homework.pk:
            if homework.source_content_id not in {None, source_id}:
                raise CurriculumImportError(
                    "homework_source_identity_conflict",
                    source_path=source.source_path,
                    pointer="/content_id",
                )
            for field, value in self._provenance(
                source, source.source_path, source.content_id
            ).items():
                setattr(homework, field, value)
            _validate_model(homework)
            homework.save(
                update_fields=(
                    "source_content_id",
                    "source_path",
                    "source_commit_sha",
                    "source_checksum",
                )
            )
            self.counts["homeworks"] += 1
            return homework

        if homework.pk and Submission.objects.filter(homework=homework).exists():
            protected_before = (
                homework.slug,
                homework.due_date,
                homework.learning_in_public_cap,
                homework.homework_url_field,
                homework.time_spent_lectures_field,
                homework.time_spent_homework_field,
                homework.faq_contribution_field,
            )
            protected_after = (
                source.slug,
                source.due_at,
                source.form.learning_in_public_cap,
                source.form.homework_url,
                source.form.time_spent_lectures,
                source.form.time_spent_homework,
                source.form.faq_contribution,
            )
            if protected_before != protected_after:
                raise CurriculumImportError(
                    "protected_homework_change", source_path=source.source_path
                )

        homework.slug = source.slug
        homework.title = source.title
        homework.instructions_markdown = source.instructions_markdown
        # The path is the only bridge between a lesson that links to
        # ``homework.md`` and the page that publishes those instructions.
        homework.instructions_source_path = source.instructions_source_path
        homework.due_date = source.due_at
        homework.learning_in_public_cap = source.form.learning_in_public_cap
        homework.homework_url_field = source.form.homework_url
        homework.time_spent_lectures_field = source.form.time_spent_lectures
        homework.time_spent_homework_field = source.form.time_spent_homework
        homework.faq_contribution_field = source.form.faq_contribution
        for field, value in self._provenance(source, source.source_path, source.content_id).items():
            setattr(homework, field, value)
        _validate_model(homework)
        homework.save()
        self._upsert_questions(homework, source)
        self.counts["homeworks"] += 1
        return homework

    def _upsert_questions(self, homework: Homework, source: HomeworkSource) -> None:
        incoming_ids = {question.content_id for question in source.questions}
        stale = Question.objects.filter(
            homework=homework,
            source_content_id__isnull=False,
        ).exclude(source_content_id__in=incoming_ids)
        if stale.exists() and Submission.objects.filter(homework=homework).exists():
            raise CurriculumImportError(
                "protected_question_removal", source_path=source.source_path
            )
        stale.delete()

        for question_source in source.questions:
            self._upsert_question(homework, source.source_path, question_source)
            self.counts["questions"] += 1

    def _upsert_question(
        self,
        homework: Homework,
        source_path: str,
        source: HomeworkQuestionSource,
    ) -> Question:
        source_id = source.content_id
        question = Question.objects.filter(homework=homework, source_content_id=source_id).first()
        stable_match = Question.objects.filter(
            homework=homework, source_question_id=source.id
        ).first()
        if question is None:
            if stable_match is not None:
                raise CurriculumImportError("question_identity_collision", source_path=source_path)
            question = Question(homework=homework)
        elif stable_match is not None and stable_match.pk != question.pk:
            raise CurriculumImportError("question_identity_collision", source_path=source_path)

        possible_answers = "\n".join(option.label for option in source.options) or None
        option_ids = [option.id for option in source.options] or None
        envelope = dict(source.answer) if source.answer is not None else None
        definition = (
            source.id,
            source.prompt,
            _QUESTION_TYPES[source.type],
            _ANSWER_TYPES[source.answer_type],
            possible_answers,
            option_ids,
            envelope,
            source.points,
        )
        if question.pk and Submission.objects.filter(homework=homework).exists():
            existing_definition = (
                question.source_question_id,
                question.text,
                question.question_type,
                question.answer_type,
                question.possible_answers,
                question.source_option_ids,
                question.answer_envelope,
                question.scores_for_correct_answer,
            )
            if existing_definition != definition:
                raise CurriculumImportError("protected_question_change", source_path=source_path)

        (
            question.source_question_id,
            question.text,
            question.question_type,
            question.answer_type,
            question.possible_answers,
            question.source_option_ids,
            question.answer_envelope,
            question.scores_for_correct_answer,
        ) = definition
        question.correct_answer = None
        for field, value in self._provenance(source, source_path, source.content_id).items():
            setattr(question, field, value)
        _validate_model(question)
        question.save()
        return question

    def _upsert_module(
        self,
        cohort: Cohort,
        source: ModuleSource,
        homework: Homework,
        position: int,
    ) -> Module:
        source_id = source.content_id
        module = Module.objects.filter(cohort=cohort, source_content_id=source_id).first()
        slug_match = Module.objects.filter(cohort=cohort, slug=source.slug).first()
        if module is None:
            if slug_match is not None:
                raise CurriculumImportError(
                    "module_slug_collision", source_path=source.source_path, pointer="/slug"
                )
            module = Module(cohort=cohort)
        elif slug_match is not None and slug_match.pk != module.pk:
            raise CurriculumImportError(
                "module_slug_collision", source_path=source.source_path, pointer="/slug"
            )
        if Module.objects.filter(
            cohort=cohort, position=position, source_content_id__isnull=True
        ).exists():
            raise CurriculumImportError(
                "db_managed_module_position_collision", source_path=source.source_path
            )

        module.position = position
        module.slug = source.slug
        module.title = source.title
        module.terminal_homework = homework
        for field, value in self._provenance(source, source.source_path, source.content_id).items():
            setattr(module, field, value)
        _validate_model(module)
        module.save()
        self.counts["modules"] += 1
        return module

    def _upsert_units(self, module: Module, source: ModuleSource) -> None:
        incoming_ids = {unit.content_id for unit in source.units}
        if not self.command.preserve_existing_records:
            Unit.objects.filter(module=module, source_content_id__isnull=False).exclude(
                source_content_id__in=incoming_ids
            ).delete()
        self._stage_positions(Unit, module=module)
        for position, unit_source in enumerate(source.units):
            self._upsert_unit(module, unit_source, position)
            self.counts["units"] += 1

    def _upsert_unit(self, module: Module, source: UnitSource, position: int) -> Unit:
        source_id = source.content_id
        unit = Unit.objects.filter(module=module, source_content_id=source_id).first()
        slug_match = Unit.objects.filter(module=module, slug=source.slug).first()
        if unit is None:
            if slug_match is not None:
                raise CurriculumImportError("unit_slug_collision", source_path=source.source_path)
            unit = Unit(module=module)
        elif slug_match is not None and slug_match.pk != unit.pk:
            raise CurriculumImportError("unit_slug_collision", source_path=source.source_path)
        if Unit.objects.filter(
            module=module, position=position, source_content_id__isnull=True
        ).exists():
            raise CurriculumImportError(
                "db_managed_unit_position_collision", source_path=source.source_path
            )

        unit.position = position
        unit.slug = source.slug
        unit.title = source.title
        unit.content_markdown = source.markdown
        unit.rendered_html = render_markdown(source.markdown)
        # The parser already lifts the lesson's video and companion code files
        # out of the frontmatter.  Persist them instead of discarding them: the
        # Markdown body no longer contains either, so a unit page that does not
        # read these columns can only lose them.
        unit.video_url = source.metadata.video_url or ""
        unit.code_sources = [
            {"label": code.label, "source_path": code.source_path} for code in source.metadata.code
        ]
        for field, value in self._provenance(source, source.source_path, source.content_id).items():
            setattr(unit, field, value)
        _validate_model(unit)
        unit.save()
        return unit

    @staticmethod
    def _validate_removals(
        cohort: Cohort,
        incoming_module_ids: set[str],
        incoming_homework_ids: set[str],
    ) -> None:
        stale_homeworks = Homework.objects.filter(
            course=cohort,
            source_content_id__isnull=False,
        ).exclude(source_content_id__in=incoming_homework_ids)
        if Submission.objects.filter(homework__in=stale_homeworks).exists():
            raise CurriculumImportError(
                "protected_homework_removal", source_path=cohort.source_path or "."
            )
        stale_modules = Module.objects.filter(
            cohort=cohort,
            source_content_id__isnull=False,
        ).exclude(source_content_id__in=incoming_module_ids)
        stale_homework_ids = set(stale_modules.values_list("terminal_homework_id", flat=True))
        if Submission.objects.filter(homework_id__in=stale_homework_ids).exists():
            raise CurriculumImportError(
                "protected_module_removal", source_path=cohort.source_path or "."
            )

    @staticmethod
    def _delete_stale_source_rows(
        cohort: Cohort,
        incoming_module_ids: set[str],
        incoming_homework_ids: set[str],
    ) -> None:
        Module.objects.filter(cohort=cohort, source_content_id__isnull=False).exclude(
            source_content_id__in=incoming_module_ids
        ).delete()
        Homework.objects.filter(course=cohort, source_content_id__isnull=False).exclude(
            source_content_id__in=incoming_homework_ids
        ).delete()


def _run_values(
    command: CurriculumImportCommand,
    *,
    manifest_checksum: str,
) -> dict[str, object]:
    return {
        "source_uuid": command.source_uuid,
        "source_stable_id": command.source_stable_id,
        "repository_owner": command.repository_owner,
        "repository_name": command.repository_name,
        "repository_branch": command.repository_branch,
        "commit_sha": command.commit_sha,
        "schema_version": command.source.schema_version,
        "parser_version": command.source.parser_version,
        "manifest_checksum": manifest_checksum,
    }


def _get_or_create_run(
    command: CurriculumImportCommand,
    manifest_checksum: str,
) -> CourseCurriculumImportRun:
    identity = {
        "source_uuid": command.source_uuid,
        "commit_sha": command.commit_sha,
        "parser_version": command.source.parser_version,
    }
    existing = CourseCurriculumImportRun.objects.filter(**identity).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            run = CourseCurriculumImportRun(
                **_run_values(command, manifest_checksum=manifest_checksum),
                state=CourseCurriculumImportRun.State.RECEIVED,
                diagnostics=[],
                counts={},
                started_at=timezone.now(),
            )
            _validate_model(run)
            run.save(force_insert=True)
            return run
    except IntegrityError:
        return CourseCurriculumImportRun.objects.get(**identity)


def _result_for_replay(
    command: CurriculumImportCommand,
    run: CourseCurriculumImportRun,
) -> CurriculumImportResult:
    course = Course.objects.filter(source_stable_id=command.source_stable_id).first()
    if course is None:
        raise CurriculumImportError("idempotent_projection_missing")
    content_ids = [
        source.content_id
        for source in command.source.cohorts
        if not source.is_implicit_legacy and source.content_id is not None
    ]
    cohort_by_content_id = {
        cohort.source_content_id: cohort
        for cohort in Cohort.objects.filter(course=course, source_content_id__in=content_ids)
    }
    if len(cohort_by_content_id) != len(content_ids):
        raise CurriculumImportError("idempotent_projection_missing")
    cohorts = tuple(cohort_by_content_id[content_id] for content_id in content_ids)
    return CurriculumImportResult(
        run=run,
        course=course,
        cohorts=cohorts,
        counts=MappingProxyType(dict(run.counts)),
        replayed=True,
    )


def _record_failure(
    run_id: UUID,
    error: CurriculumImportError,
    *,
    state: str,
) -> None:
    with transaction.atomic():
        run = CourseCurriculumImportRun.objects.get(pk=run_id)
        run.state = state
        run.diagnostics = [diagnostic.as_dict() for diagnostic in error.diagnostics]
        run.counts = {}
        run.finished_at = timezone.now()
        _validate_model(run)
        run.save(
            update_fields=(
                "state",
                "diagnostics",
                "counts",
                "finished_at",
                "updated_at",
            )
        )


def import_course_repository_curriculum(
    command: CurriculumImportCommand,
) -> CurriculumImportResult:
    """Atomically project one parsed source graph into source-managed rows."""

    if not isinstance(command, CurriculumImportCommand):
        raise CurriculumImportError("invalid_import_command")
    manifest_checksum = _manifest_checksum(command)
    run = _get_or_create_run(command, manifest_checksum)
    if run.manifest_checksum != manifest_checksum:
        raise CurriculumImportError("source_commit_checksum_conflict")
    expected_run_values = _run_values(command, manifest_checksum=manifest_checksum)
    if any(getattr(run, field) != value for field, value in expected_run_values.items()):
        raise CurriculumImportError("source_import_identity_conflict")
    if run.state == CourseCurriculumImportRun.State.SUCCEEDED:
        return _result_for_replay(command, run)

    try:
        with transaction.atomic():
            run = CourseCurriculumImportRun.objects.get(pk=run.pk)
            run.state = CourseCurriculumImportRun.State.APPLYING
            run.diagnostics = []
            run.counts = {}
            run.finished_at = None
            run.save(
                update_fields=(
                    "state",
                    "diagnostics",
                    "counts",
                    "finished_at",
                    "updated_at",
                )
            )
            course, cohorts, counts = _CurriculumImporter(command).apply()
            run.state = CourseCurriculumImportRun.State.SUCCEEDED
            run.diagnostics = []
            run.counts = dict(counts)
            run.finished_at = timezone.now()
            _validate_model(run)
            run.save(
                update_fields=(
                    "state",
                    "diagnostics",
                    "counts",
                    "finished_at",
                    "updated_at",
                )
            )
        return CurriculumImportResult(
            run=run,
            course=course,
            cohorts=cohorts,
            counts=counts,
            replayed=False,
        )
    except CurriculumImportError as error:
        _record_failure(
            run.pk,
            error,
            state=CourseCurriculumImportRun.State.REJECTED,
        )
        raise
    except (IntegrityError, ValidationError) as cause:
        error = CurriculumImportError("curriculum_import_constraint_failure")
        _record_failure(
            run.pk,
            error,
            state=CourseCurriculumImportRun.State.FAILED,
        )
        raise error from cause


import_curriculum = import_course_repository_curriculum


__all__ = (
    "CurriculumImportCommand",
    "CurriculumImportDiagnostic",
    "CurriculumImportError",
    "CurriculumImportResult",
    "import_course_repository_curriculum",
    "import_curriculum",
)
