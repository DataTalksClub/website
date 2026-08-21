"""Transactional projection of parsed course-repository curriculum sources."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Max
from django.utils import timezone

from courses.models import (
    AnswerTypes,
    Cohort,
    Course,
    CourseCurriculumImportRun,
    CurriculumFlowItem,
    CurriculumFormat,
    Homework,
    HomeworkState,
    Module,
    Project,
    Question,
    QuestionTypes,
    Submission,
    Unit,
)
from courses.models.curriculum_import import (
    REPOSITORY_BRANCH_PATTERN,
    REPOSITORY_COMPONENT_PATTERN,
    SHA1_PATTERN,
    SHA256_PATTERN,
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

    def __post_init__(self) -> None:
        if not isinstance(self.source, CourseRepositorySource):
            raise CurriculumImportError("invalid_source_graph")
        if not isinstance(self.source_uuid, UUID):
            raise CurriculumImportError("invalid_source_uuid")
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
        }

    def apply(self) -> tuple[Course, tuple[Cohort, ...], Mapping[str, int]]:
        self._validate_repository_identity()
        course = self._upsert_course()
        imported_cohorts: list[Cohort] = []
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
        return {
            "source_content_id": UUID(content_id),
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
        source_id = UUID(source.content_id)
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
            slug_match = Course.objects.filter(slug=source.slug).first()
            if slug_match is not None and slug_match.source_content_id is not None:
                raise CurriculumImportError(
                    "course_slug_collision", source_path=source.source_path, pointer="/slug"
                )
            course = slug_match or Course()

        slug_collision = Course.objects.filter(slug=source.slug).exclude(pk=course.pk).exists()
        if slug_collision:
            raise CurriculumImportError(
                "course_slug_collision", source_path=source.source_path, pointer="/slug"
            )
        if (
            course.pk
            and course.slug != source.slug
            and Submission.objects.filter(homework__course__course=course).exists()
        ):
            raise CurriculumImportError(
                "protected_course_slug_change", source_path=source.source_path, pointer="/slug"
            )

        course.slug = source.slug
        course.title = source.title
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

    def _upsert_cohort(self, course: Course, source: CohortSource) -> Cohort:
        if source.content_id is None or source.source_path is None:
            raise CurriculumImportError("explicit_cohort_source_identity_missing")
        source_id = UUID(source.content_id)
        by_content = Cohort.objects.filter(course=course, source_content_id=source_id).first()
        by_identifier = Cohort.objects.filter(course=course, identifier=source.identifier).first()
        by_slug = (
            Cohort.objects.filter(slug=source.legacy_slug).first() if source.legacy_slug else None
        )
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

        target_slug = source.legacy_slug or f"{course.slug}-{source.identifier}"
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

        cohort.course = course
        cohort.slug = target_slug
        cohort.identifier = source.identifier
        cohort.year = source.year or cohort.year
        cohort.title = source.title or cohort.title
        cohort.description = source.description or ""
        cohort.curriculum_format = source.format
        cohort.start_date = source.start_date
        cohort.end_date = source.end_date
        cohort.visible = bool(source.published)
        for field, value in self._provenance(source, source.source_path, source.content_id).items():
            setattr(cohort, field, value)
        _validate_model(cohort)
        cohort.save()
        return cohort

    def _validate_legacy_transition(self, cohort: Cohort) -> None:
        if Module.objects.filter(cohort=cohort, source_content_id__isnull=False).exists():
            raise CurriculumImportError(
                "protected_curriculum_format_change",
                source_path=cohort.source_path or ".",
                pointer="/format",
            )

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
        incoming_module_ids = {UUID(item.module.content_id) for item in module_items}
        incoming_homework_ids = {UUID(item.homework.content_id) for item in module_items}
        self._validate_removals(cohort, incoming_module_ids, incoming_homework_ids)
        CurriculumFlowItem.objects.filter(cohort=cohort).delete()
        self._stage_positions(Module, cohort=cohort)

        module_by_source_id: dict[UUID, Module] = {}
        for module_position, item in enumerate(module_items):
            homework = self._upsert_homework(cohort, item.homework)
            module = self._upsert_module(cohort, item.module, homework, module_position)
            self._upsert_units(module, item.module)
            module_by_source_id[UUID(item.module.content_id)] = module

        self._delete_stale_source_rows(cohort, incoming_module_ids, incoming_homework_ids)

        for flow_position, item in enumerate(source.flow):
            if isinstance(item, ModuleFlowSource):
                module = module_by_source_id[UUID(item.module.content_id)]
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
        source_id = UUID(source.content_id)
        homework = Homework.objects.filter(course=cohort, source_content_id=source_id).first()
        slug_match = Homework.objects.filter(course=cohort, slug=source.slug).first()
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
        incoming_ids = {UUID(question.content_id) for question in source.questions}
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
        source_id = UUID(source.content_id)
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
        source_id = UUID(source.content_id)
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
        incoming_ids = {UUID(unit.content_id) for unit in source.units}
        Unit.objects.filter(module=module, source_content_id__isnull=False).exclude(
            source_content_id__in=incoming_ids
        ).delete()
        self._stage_positions(Unit, module=module)
        for position, unit_source in enumerate(source.units):
            self._upsert_unit(module, unit_source, position)
            self.counts["units"] += 1

    def _upsert_unit(self, module: Module, source: UnitSource, position: int) -> Unit:
        source_id = UUID(source.content_id)
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
        for field, value in self._provenance(source, source.source_path, source.content_id).items():
            setattr(unit, field, value)
        _validate_model(unit)
        unit.save()
        return unit

    @staticmethod
    def _validate_removals(
        cohort: Cohort,
        incoming_module_ids: set[UUID],
        incoming_homework_ids: set[UUID],
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
        incoming_module_ids: set[UUID],
        incoming_homework_ids: set[UUID],
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
        UUID(source.content_id)
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
