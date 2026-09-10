"""Pure parser for the version-two shared-curriculum source contract.

Schema 2 makes the repository root the single source for the current teaching
graph: one shared module graph that every delivery references, explicit
``delivery``/``curriculum`` cohort discriminators, and GitHub-only historical
archives whose descendants are opaque.  The parser deliberately knows nothing
about Django, persistence, or GitHub -- exactly like the version-one parser it
sits beside.  A repository whose ``course.yaml`` says ``schema_version: 1``
never reaches this module: v1 is never silently reinterpreted as v2.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath
from typing import Any

from courses.services.curriculum_source import (
    CohortSource,
    CourseRepositorySource,
    CourseSource,
    HomeworkBindingSource,
    HomeworkSource,
    ModuleSource,
    UnitSource,
)

from .course_repository import (
    _COMMIT_SHA,
    DEFAULT_LIMITS,
    CourseRepositoryLimits,
    _boolean,
    _content_id,
    _date,
    _decode_utf8,
    _fail,
    _https_url,
    _load_yaml_mapping,
    _parse_lesson_frontmatter,
    _Parser,
    _relative_source_path,
    _sequence,
    _slug,
    _strict_mapping,
    _string,
    _validated_snapshot,
)

SCHEMA_VERSION = 2
PARSER_VERSION = "course-repository-v2"

V2_MODULE_DIR = re.compile(r"[0-9]{2,}-[a-z0-9]+(?:-[a-z0-9]+)*")
V2_LESSON_FILE = re.compile(r"[0-9]{2,}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
# Grandfathered exception, documented in zoomcamp-ops/STRUCTURE.md: a module
# with exactly one lesson may name it the fixed "lesson.md" instead of a
# numbered file -- ai-dev-tools-zoomcamp is the one repository that does this
# today. Never a choice for a new module or a multi-lesson one.
V2_SINGLE_LESSON_FILE = "lesson.md"

COHORTS_ROOT = "cohorts"
MODULE_MANIFEST_NAME = "module.yaml"
COHORT_MANIFEST_NAME = "cohort.yaml"
HOMEWORK_MANIFEST_NAME = "homework.yaml"
MODULE_OVERVIEW_NAME = "README.md"


class SharedCurriculumParserV2:
    """Parse one schema-2 repository snapshot into the shared source graph."""

    def __init__(
        self,
        snapshot: dict[str, bytes],
        *,
        commit_sha: str | None,
        limits: CourseRepositoryLimits,
    ) -> None:
        self.limits = limits
        self.snapshot = snapshot
        if commit_sha is not None and _COMMIT_SHA.fullmatch(commit_sha) is None:
            _fail("source_commit_invalid")
        self.commit_sha = commit_sha
        self.content_ids: dict[str, tuple[str, str]] = {}
        self.declared_cohorts: dict[str, str] = {}
        # A v1 reader instance supplies the shared lesson-frontmatter and
        # homework-manifest readers.  Its own content-ID registry is unused:
        # every ID is re-registered in this parser's namespace so duplicates
        # across a v2 source fail in one place.
        self._reader = _Parser(snapshot, commit_sha=None, limits=limits)
        self.homework_by_path: dict[str, HomeworkSource] = {}

    # -- shared YAML/path helpers -------------------------------------------

    def _load_yaml(self, path: str) -> dict[str, Any]:
        return _load_yaml_mapping(self.snapshot[path], path=path, limits=self.limits)

    def _register_content_id(self, value: str, *, kind: str, path: str, pointer: str) -> None:
        prior = self.content_ids.get(value)
        if prior is not None:
            _fail("duplicate_content_id", path, pointer)
        self.content_ids[value] = (kind, path)

    # -- discovery -----------------------------------------------------------

    def _root_module_dirs(self) -> list[str]:
        dirs = {
            PurePosixPath(path).parts[0]
            for path in self.snapshot
            if len(PurePosixPath(path).parts) == 2
            and PurePosixPath(path).parts[0] != COHORTS_ROOT
            and PurePosixPath(path).parts[1] == MODULE_MANIFEST_NAME
        }
        if not dirs:
            _fail("current_module_missing", MODULE_MANIFEST_NAME)
        for directory in sorted(dirs):
            if V2_MODULE_DIR.fullmatch(directory) is None:
                _fail("numbered_module_required", f"{directory}/{MODULE_MANIFEST_NAME}")
        prefixes: set[str] = set()
        for directory in sorted(dirs):
            prefix = directory.split("-", 1)[0]
            if prefix in prefixes:
                _fail("duplicate_number_prefix", f"{directory}/{MODULE_MANIFEST_NAME}")
            prefixes.add(prefix)
        return sorted(dirs)

    def _cohort_manifest_paths(self) -> list[str]:
        return sorted(
            path
            for path in self.snapshot
            if len(PurePosixPath(path).parts) == 3
            and PurePosixPath(path).parts[0] == COHORTS_ROOT
            and PurePosixPath(path).name == COHORT_MANIFEST_NAME
        )

    # -- manifests -----------------------------------------------------------

    def _parse_course(self) -> CourseSource:
        path = "course.yaml"
        mapping = _strict_mapping(
            self._load_yaml(path),
            path=path,
            pointer="",
            allowed=frozenset(
                {
                    "schema_version",
                    "content_id",
                    "slug",
                    "title",
                    "current_cohort",
                    "cohorts",
                    "description",
                    "outcome",
                    "urls",
                    "hashtag",
                    "published",
                }
            ),
            required=frozenset(
                {
                    "schema_version",
                    "content_id",
                    "slug",
                    "title",
                    "current_cohort",
                    "cohorts",
                    "description",
                    "outcome",
                    "urls",
                    "hashtag",
                    "published",
                }
            ),
        )
        version = mapping.get("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            _fail("unsupported_schema_version", path, "/schema_version")
        content_id = _content_id(mapping["content_id"], path=path, pointer="/content_id")
        self._register_content_id(content_id, kind="course", path=path, pointer="/content_id")
        description = _string(
            mapping["description"],
            path=path,
            pointer="/description",
            maximum=self.limits.max_site_description_chars,
        )
        current_cohort = _slug(
            mapping["current_cohort"], path=path, pointer="/current_cohort", maximum=80
        )
        self.declared_cohorts = self._parse_cohorts_index(
            path, mapping.get("cohorts"), current_cohort=current_cohort
        )
        urls = _strict_mapping(
            mapping.get("urls"),
            path=path,
            pointer="/urls",
            allowed=frozenset({"repository", "docs", "faq"}),
            required=frozenset({"repository", "docs", "faq"}),
        )
        hashtag = _string(mapping["hashtag"], path=path, pointer="/hashtag", maximum=100)
        if hashtag.startswith("#") or re.fullmatch(r"[A-Za-z0-9_]+", hashtag) is None:
            _fail("invalid_hashtag", path, "/hashtag")
        return CourseSource(
            content_id=content_id,
            slug=_slug(mapping["slug"], path=path, pointer="/slug"),
            title=_string(mapping["title"], path=path, pointer="/title", maximum=200),
            description=description,
            description_source_path=path,
            outcome=_string(mapping["outcome"], path=path, pointer="/outcome"),
            repository_url=_https_url(
                urls["repository"], path=path, pointer="/urls/repository"
            ),
            docs_url=_https_url(urls["docs"], path=path, pointer="/urls/docs"),
            faq_url=_https_url(urls["faq"], path=path, pointer="/urls/faq"),
            hashtag=hashtag,
            published=_boolean(mapping["published"], path=path, pointer="/published"),
            source_path=path,
            current_cohort=current_cohort,
        )

    def _parse_cohorts_index(
        self, path: str, raw: Any, *, current_cohort: str
    ) -> dict[str, str]:
        """Parse and structurally validate course.yaml's ``cohorts`` index.

        Returns ``{identifier: content}``.  Cross-validation against the
        cohorts actually discovered on disk happens later in ``parse()``,
        once every ``cohort.yaml`` has been read.
        """

        if not isinstance(raw, list) or not raw:
            _fail("cohorts_index_required", path, "/cohorts")
        declared: dict[str, str] = {}
        root_identifiers: set[str] = set()
        for index, entry in enumerate(raw):
            pointer = f"/cohorts/{index}"
            item = _strict_mapping(
                entry,
                path=path,
                pointer=pointer,
                allowed=frozenset({"identifier", "content", "legacy"}),
                required=frozenset({"identifier", "content"}),
            )
            identifier = _slug(
                item["identifier"], path=path, pointer=f"{pointer}/identifier", maximum=80
            )
            content = _string(item["content"], path=path, pointer=f"{pointer}/content", maximum=200)
            if identifier in declared:
                _fail("duplicate_cohort_index_entry", path, pointer)
            legacy = item.get("legacy", False)
            if "legacy" in item and type(legacy) is not bool:
                _fail("boolean_required", path, f"{pointer}/legacy")
            if content == "root":
                # More than one cohort may share root: a live delivery and a
                # permanent self-paced one commonly reference the identical
                # current module graph side by side. ``current_cohort`` names
                # the headline one; every ``content: root`` entry is still a
                # real curriculum: current cohort.
                root_identifiers.add(identifier)
                if legacy is True:
                    _fail("current_cohort_cannot_be_legacy", path, f"{pointer}/legacy")
            elif content != f"{COHORTS_ROOT}/{identifier}":
                # A cohort's content may only be its own directory -- never a
                # path into a different cohort.  Sharing content across
                # cohort years is the design this schema deliberately
                # forecloses: a published cohort's tree stays immutable.
                _fail("cohort_content_path_invalid", path, f"{pointer}/content")
            declared[identifier] = content
        if not root_identifiers:
            _fail("current_cohort_entry_missing", path, "/cohorts")
        if current_cohort not in root_identifiers:
            _fail("current_cohort_mismatch", path, "/current_cohort")
        return declared

    def _parse_module(self, directory: str) -> ModuleSource:
        path = f"{directory}/{MODULE_MANIFEST_NAME}"
        mapping = _strict_mapping(
            self._load_yaml(path),
            path=path,
            pointer="",
            allowed=frozenset(
                {"schema_version", "content_id", "slug", "title", "units", "overview_path"}
            ),
            required=frozenset({"schema_version", "content_id", "title", "units"}),
        )
        version = mapping.get("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            _fail("unsupported_schema_version", path, "/schema_version")
        content_id = _content_id(mapping["content_id"], path=path, pointer="/content_id")
        self._register_content_id(content_id, kind="module", path=path, pointer="/content_id")
        module_slug = _slug(directory, path=path, pointer="/slug")
        if "slug" in mapping:
            configured = _slug(mapping["slug"], path=path, pointer="/slug")
            if configured != module_slug:
                _fail("module_slug_path_mismatch", path, "/slug")

        overview_markdown: str | None = None
        if "overview_path" in mapping:
            declared = _string(
                mapping["overview_path"], path=path, pointer="/overview_path", maximum=512
            )
            if declared != MODULE_OVERVIEW_NAME:
                _fail("module_overview_not_readme", path, "/overview_path")
        overview_resolved = posixpath.join(directory, MODULE_OVERVIEW_NAME)
        if overview_resolved in self.snapshot:
            overview_markdown = _decode_utf8(
                self.snapshot[overview_resolved],
                path=overview_resolved,
                limits=self.limits,
            )

        raw_units = _sequence(mapping["units"], path=path, pointer="/units", minimum=1)
        single_lesson_module = len(raw_units) == 1
        units: list[UnitSource] = []
        unit_ids: set[str] = set()
        unit_slugs: set[str] = set()
        unit_paths: set[str] = set()
        for index, raw_unit in enumerate(raw_units):
            pointer = f"/units/{index}"
            unit = _strict_mapping(
                raw_unit,
                path=path,
                pointer=pointer,
                allowed=frozenset({"content_id", "slug", "title", "path"}),
                required=frozenset({"content_id", "title", "path"}),
            )
            unit_id = _content_id(unit["content_id"], path=path, pointer=f"{pointer}/content_id")
            declared_path = _string(unit["path"], path=path, pointer=f"{pointer}/path", maximum=512)
            is_grandfathered_single_lesson = (
                single_lesson_module and declared_path == V2_SINGLE_LESSON_FILE
            )
            if (
                not is_grandfathered_single_lesson
                and ("/" in declared_path or V2_LESSON_FILE.fullmatch(declared_path) is None)
            ):
                _fail("numbered_lesson_required", path, f"{pointer}/path")
            source_path = _relative_source_path(
                unit["path"],
                manifest_path=path,
                pointer=f"{pointer}/path",
                snapshot=self.snapshot,
                limits=self.limits,
                suffix=".md",
            )
            unit_slug = _slug(
                PurePosixPath(source_path).stem,
                path=path,
                pointer=f"{pointer}/slug",
            )
            if "slug" in unit:
                configured = _slug(unit["slug"], path=path, pointer=f"{pointer}/slug")
                if configured != unit_slug:
                    _fail("unit_slug_path_mismatch", path, f"{pointer}/slug")
            if unit_id in unit_ids or unit_slug in unit_slugs:
                _fail("duplicate_unit_id_or_slug", path, pointer)
            unit_ids.add(unit_id)
            unit_slugs.add(unit_slug)
            self._register_content_id(
                unit_id, kind="unit", path=path, pointer=f"{pointer}/content_id"
            )
            if source_path in unit_paths:
                _fail("duplicate_unit_source", path, f"{pointer}/path")
            unit_paths.add(source_path)
            raw_markdown = _decode_utf8(
                self.snapshot[source_path], path=source_path, limits=self.limits
            )
            markdown, metadata = _parse_lesson_frontmatter(
                raw_markdown,
                source_path=source_path,
                snapshot=self.snapshot,
                limits=self.limits,
            )
            units.append(
                UnitSource(
                    content_id=unit_id,
                    slug=unit_slug,
                    title=_string(
                        unit["title"], path=path, pointer=f"{pointer}/title", maximum=200
                    ),
                    source_path=source_path,
                    markdown=markdown,
                    metadata=metadata,
                )
            )
        return ModuleSource(
            content_id=content_id,
            slug=module_slug,
            title=_string(mapping["title"], path=path, pointer="/title", maximum=200),
            source_path=path,
            units=tuple(units),
            scope="shared",
            overview_markdown=overview_markdown,
        )

    def _parse_bound_homework(
        self, path: str, *, course_slug: str, archive: bool
    ) -> HomeworkSource:
        # Archive homework may keep its v1 manifest shape while it is being
        # converted; current homework must be schema 2.
        allowed = frozenset({1, 2}) if archive else frozenset({2})
        homework = self._reader._parse_homework(
            path, course_slug=course_slug, schema_versions=allowed
        )
        self._register_content_id(
            homework.content_id, kind="homework", path=path, pointer="/content_id"
        )
        for question in homework.questions:
            self._register_content_id(
                question.content_id,
                kind="question",
                path=path,
                pointer=f"/questions/{question.id}/content_id",
            )
        self.homework_by_path[path] = homework
        return homework

    # -- cohorts ---------------------------------------------------------------

    def _parse_cohort(
        self,
        path: str,
        *,
        course: CourseSource,
        module_by_slug: dict[str, ModuleSource],
    ) -> CohortSource:
        parts = PurePosixPath(path).parts
        directory = f"{COHORTS_ROOT}/{parts[1]}/"
        mapping = _strict_mapping(
            self._load_yaml(path),
            path=path,
            pointer="",
            allowed=frozenset(
                {
                    "schema_version",
                    "content_id",
                    "course",
                    "identifier",
                    "delivery",
                    "published",
                    "start_date",
                    "end_date",
                    "curriculum",
                    "archive",
                    "homework",
                }
            ),
            required=frozenset(
                {
                    "schema_version",
                    "content_id",
                    "course",
                    "identifier",
                    "delivery",
                    "published",
                    "curriculum",
                    "homework",
                }
            ),
        )
        version = mapping.get("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            _fail("v2_mixed_version", path, "/schema_version")
        content_id = _content_id(mapping["content_id"], path=path, pointer="/content_id")
        self._register_content_id(content_id, kind="cohort", path=path, pointer="/content_id")
        identifier = _slug(mapping["identifier"], path=path, pointer="/identifier", maximum=80)
        if identifier != parts[1]:
            _fail("cohort_identifier_path_mismatch", path, "/identifier")
        course_slug = _slug(mapping["course"], path=path, pointer="/course")
        if course_slug != course.slug:
            _fail("cohort_course_mismatch", path, "/course")
        delivery = _string(mapping["delivery"], path=path, pointer="/delivery", maximum=20)
        if delivery not in {"live", "self_paced"}:
            _fail("invalid_delivery", path, "/delivery")
        published = _boolean(mapping["published"], path=path, pointer="/published")
        curriculum = mapping.get("curriculum")
        if curriculum not in {"current", "github_archive"}:
            _fail("invalid_curriculum_kind", path, "/curriculum")
        if curriculum == "github_archive":
            archive_notice_path = self._parse_archive_block(path, mapping, directory)
        else:
            archive_notice_path = None
            if "archive" in mapping:
                _fail("archive_not_allowed", path, "/archive")

        start_date = (
            _date(mapping["start_date"], path=path, pointer="/start_date")
            if mapping.get("start_date") is not None
            else None
        )
        end_date = (
            _date(mapping["end_date"], path=path, pointer="/end_date")
            if mapping.get("end_date") is not None
            else None
        )
        if start_date and end_date and end_date < start_date:
            _fail("invalid_cohort_date_range", path, "/end_date")
        if published and delivery == "live" and (start_date is None or end_date is None):
            _fail("published_live_dates_required", path, "/start_date")

        raw_homework = mapping.get("homework")
        if not isinstance(raw_homework, list):
            _fail("homework_list_required", path, "/homework")
        if delivery == "self_paced" and raw_homework:
            # Self-paced phase one is reading, shared progress, and ungraded
            # practice; the bootstrap producer never authors a self-paced
            # assignment, and neither may a hand-written manifest.
            _fail("self_paced_homework_rejected", path, "/homework")

        bindings = self._parse_homework_bindings(
            path,
            mapping=raw_homework,
            course_slug=course.slug,
            identifier=identifier,
            directory=directory,
            archive=curriculum == "github_archive",
            module_by_slug=module_by_slug,
        )
        if curriculum == "current":
            self._reject_unreferenced_current_homework(identifier, bindings)

        return CohortSource(
            identifier=identifier,
            # The source graph keeps the v1 vocabulary; the shared projection
            # branches on ``curriculum``/``delivery`` and never on this value.
            format="modules" if curriculum == "current" else "legacy",
            source_path=path,
            content_id=content_id,
            course_slug=course_slug,
            # legacy_slug/year/title/description no longer exist in any v2
            # cohort.yaml -- the shared projection already derives all four
            # (cohort.py:_cohort_slug/_display_year and the `or` fallbacks in
            # _upsert_cohort) when the source leaves them None.
            legacy_slug=None,
            year=None,
            title=None,
            description=None,
            published=published,
            start_date=start_date,
            end_date=end_date,
            flow=(),
            is_implicit_legacy=False,
            delivery=delivery,  # type: ignore[arg-type]
            curriculum=curriculum,  # type: ignore[arg-type]
            archive_notice_path=archive_notice_path,
            homework_bindings=bindings,
        )

    def _parse_archive_block(self, path: str, mapping: dict[str, Any], directory: str) -> str:
        raw = mapping.get("archive")
        if not isinstance(raw, dict) or set(raw) != {"notice_path"}:
            _fail("archive_notice_missing", path, "/archive")
        notice = _string(raw["notice_path"], path=path, pointer="/archive/notice_path", maximum=512)
        if (
            notice.startswith("/")
            or ".." in PurePosixPath(notice).parts
            or not notice.endswith(".md")
            or not notice.startswith(directory)
        ):
            _fail("archive_url_invalid", path, "/archive/notice_path")
        if notice not in self.snapshot:
            _fail("archive_notice_missing", notice)
        return notice

    def _parse_homework_bindings(
        self,
        path: str,
        *,
        mapping: list[Any],
        course_slug: str,
        identifier: str,
        directory: str,
        archive: bool,
        module_by_slug: dict[str, ModuleSource],
    ) -> tuple[HomeworkBindingSource, ...]:
        bindings: list[HomeworkBindingSource] = []
        seen_sources: set[str] = set()
        seen_modules: set[str] = set()
        for index, item in enumerate(mapping):
            pointer = f"/homework/{index}"
            if not isinstance(item, dict) or set(item) != {"module", "source"}:
                _fail("homework_mapping_invalid", path, pointer)
            module = item["module"]
            source = _string(item["source"], path=path, pointer=f"{pointer}/source", maximum=1024)
            if archive:
                if module is not None:
                    _fail("archive_module_reference", path, f"{pointer}/module")
            else:
                if not isinstance(module, str):
                    _fail("homework_mapping_invalid", path, f"{pointer}/module")
                if module not in module_by_slug:
                    _fail("curriculum_source_mismatch", path, f"{pointer}/module")
                if module in seen_modules:
                    _fail("duplicate_homework_module", path, pointer)
                seen_modules.add(module)
                expected = f"{COHORTS_ROOT}/{identifier}/homework/{module}/{HOMEWORK_MANIFEST_NAME}"
                if source != expected:
                    _fail("homework_path_outside_cohort", path, f"{pointer}/source")
            if not source.startswith(directory) or PurePosixPath(source).name != (
                HOMEWORK_MANIFEST_NAME
            ):
                _fail("homework_path_outside_cohort", path, f"{pointer}/source")
            if source in seen_sources:
                _fail("duplicate_homework_source", path, pointer)
            seen_sources.add(source)
            if source not in self.snapshot:
                _fail("source_path_missing", path, f"{pointer}/source")
            self._parse_bound_homework(source, course_slug=course_slug, archive=archive)
            bindings.append(
                HomeworkBindingSource(
                    module=module if isinstance(module, str) else None,
                    source=source,
                )
            )
        return tuple(bindings)

    def _reject_unreferenced_current_homework(
        self, identifier: str, bindings: tuple[HomeworkBindingSource, ...]
    ) -> None:
        mapped = {binding.source for binding in bindings}
        prefix = f"{COHORTS_ROOT}/{identifier}/"
        for path in sorted(self.snapshot):
            if not path.startswith(prefix):
                continue
            if PurePosixPath(path).name == HOMEWORK_MANIFEST_NAME and path not in mapped:
                _fail("homework_unreferenced", path)

    # -- opaque archive enforcement -------------------------------------------

    def _reject_current_cohort_modules(self, archive_prefixes: set[str]) -> None:
        for path in sorted(self.snapshot):
            parts = PurePosixPath(path).parts
            if parts[0] != COHORTS_ROOT or parts[-1] != MODULE_MANIFEST_NAME:
                continue
            if len(parts) < 3:
                continue
            if f"{parts[0]}/{parts[1]}/" in archive_prefixes:
                # An archive's historical module manifests are opaque GitHub
                # content: never loaded, never ID-registered, never projected.
                continue
            _fail("v2_mixed_version", path)

    def _reject_manifestless_cohort_dirs(self, known: set[str]) -> None:
        for path in sorted(self.snapshot):
            parts = PurePosixPath(path).parts
            if len(parts) < 3 or parts[0] != COHORTS_ROOT or parts[1] in known:
                continue
            if len(parts) == 3 and parts[2] == MODULE_OVERVIEW_NAME:
                continue
            _fail("cohort_manifest_missing", path)

    # -- cohorts index cross-validation --------------------------------------

    def _check_declared_cohorts_against_reality(self, cohorts: list[CohortSource]) -> None:
        """course.yaml:cohorts must exactly match the cohort.yaml files on
        disk, and each entry's declared ``content`` must agree with that
        cohort's own ``curriculum`` discriminator. ``_parse_course`` already
        checked the index is internally well-formed (exactly one ``root``
        entry, matching ``current_cohort``); this is the second half, run
        once every cohort.yaml has actually been read.
        """

        actual = {cohort.identifier for cohort in cohorts}
        declared = set(self.declared_cohorts)
        missing = sorted(actual - declared)
        if missing:
            _fail("cohort_not_listed_in_index", f"{COHORTS_ROOT}/{missing[0]}/{COHORT_MANIFEST_NAME}")
        stale = sorted(declared - actual)
        if stale:
            _fail("cohort_index_entry_not_found", "course.yaml", f"/cohorts[{stale[0]!r}]")
        for cohort in cohorts:
            content = self.declared_cohorts[cohort.identifier]
            is_current = cohort.curriculum == "current"
            if is_current and content != "root":
                _fail("cohort_index_content_mismatch", cohort.source_path or "course.yaml")
            if not is_current and content == "root":
                _fail("cohort_index_content_mismatch", cohort.source_path or "course.yaml")

    # -- entry point -------------------------------------------------------------

    def parse(self) -> CourseRepositorySource:
        if "course.yaml" not in self.snapshot:
            _fail("course_manifest_missing", "course.yaml")
        course = self._parse_course()
        modules = [self._parse_module(directory) for directory in self._root_module_dirs()]
        module_by_slug = {module.slug: module for module in modules}
        cohort_paths = self._cohort_manifest_paths()
        archive_prefixes: set[str] = set()
        for path in cohort_paths:
            parts = PurePosixPath(path).parts
            if self._load_yaml(path).get("curriculum") == "github_archive":
                archive_prefixes.add(f"{COHORTS_ROOT}/{parts[1]}/")
        self._reject_current_cohort_modules(archive_prefixes)
        cohorts = [
            self._parse_cohort(path, course=course, module_by_slug=module_by_slug)
            for path in cohort_paths
        ]
        self._reject_manifestless_cohort_dirs(
            {PurePosixPath(path).parts[1] for path in cohort_paths}
        )
        self._check_declared_cohorts_against_reality(cohorts)
        return CourseRepositorySource(
            schema_version=SCHEMA_VERSION,
            parser_version=PARSER_VERSION,
            commit_sha=self.commit_sha,
            course=course,
            cohorts=tuple(sorted(cohorts, key=lambda cohort: cohort.identifier)),
            modules=tuple(modules),
            homeworks=tuple(homework for _, homework in sorted(self.homework_by_path.items())),
        )


def parse_course_repository_v2(
    snapshot: dict[str, bytes],
    *,
    commit_sha: str | None = None,
    limits: CourseRepositoryLimits = DEFAULT_LIMITS,
) -> CourseRepositorySource:
    """Parse and validate one schema-2 repository snapshot."""

    return SharedCurriculumParserV2(
        _validated_snapshot(snapshot, limits=limits),
        commit_sha=commit_sha,
        limits=limits,
    ).parse()


__all__ = [
    "PARSER_VERSION",
    "SCHEMA_VERSION",
    "V2_LESSON_FILE",
    "V2_MODULE_DIR",
    "SharedCurriculumParserV2",
    "parse_course_repository_v2",
]
