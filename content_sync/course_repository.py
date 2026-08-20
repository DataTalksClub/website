"""Pure parser for the version-one course repository source contract.

The parser deliberately knows nothing about Django, persistence, GitHub, or answer
decryption.  Its input is a commit-pinned repository snapshot represented as POSIX
paths and bytes; its output is an immutable command graph for a later import service.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Literal, Never
from urllib.parse import urlsplit
from uuid import UUID

import yaml
from yaml.events import (
    AliasEvent,
    CollectionStartEvent,
    DocumentStartEvent,
    ScalarEvent,
)

SCHEMA_VERSION = 1
PARSER_VERSION = "course-repository-v1"

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_URL_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_PLAINTEXT_ANSWER_KEYS = frozenset(
    {
        "answer_key",
        "answer_value",
        "correct_answer",
        "plaintext_answer",
        "solution",
    }
)


@dataclass(frozen=True, slots=True)
class CourseRepositoryLimits:
    max_files: int = 5_000
    max_total_bytes: int = 100_000_000
    max_file_bytes: int = 8_000_000
    max_yaml_file_bytes: int = 512_000
    max_yaml_depth: int = 32
    max_yaml_nodes: int = 50_000
    max_string_chars: int = 100_000
    max_list_items: int = 1_000
    max_mapping_items: int = 1_000
    max_path_chars: int = 512
    max_diagnostics: int = 25


DEFAULT_LIMITS = CourseRepositoryLimits()


@dataclass(frozen=True, slots=True)
class CourseRepositoryDiagnostic:
    code: str
    source_path: str
    pointer: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "source_path": self.source_path,
            "pointer": self.pointer,
        }


class CourseRepositoryValidationError(ValueError):
    """A validation failure containing bounded, source-safe diagnostics."""

    def __init__(self, code: str, source_path: str = ".", pointer: str = "") -> None:
        diagnostic = CourseRepositoryDiagnostic(
            code=code[:128],
            source_path=(source_path or ".")[:512],
            pointer=pointer[:512],
        )
        self.diagnostics = (diagnostic,)
        location = f"{diagnostic.source_path}{diagnostic.pointer}"
        super().__init__(f"{location}: {diagnostic.code}")


@dataclass(frozen=True, slots=True)
class CourseSource:
    content_id: str
    slug: str
    title: str
    description: str
    description_source_path: str | None
    outcome: str
    repository_url: str
    docs_url: str
    faq_url: str
    hashtag: str
    published: bool
    source_path: str


@dataclass(frozen=True, slots=True)
class UnitSource:
    content_id: str
    slug: str
    title: str
    source_path: str
    markdown: str


@dataclass(frozen=True, slots=True)
class ModuleSource:
    content_id: str
    slug: str
    title: str
    source_path: str
    units: tuple[UnitSource, ...]


@dataclass(frozen=True, slots=True)
class HomeworkOptionSource:
    id: str
    label: str


type AnswerEnvelopeValue = str | int
type AnswerEnvelope = Mapping[str, AnswerEnvelopeValue]


@dataclass(frozen=True, slots=True)
class HomeworkQuestionSource:
    content_id: str
    id: str
    type: Literal["multiple_choice", "checkboxes", "free_form", "free_form_long"]
    prompt: str
    points: int
    options: tuple[HomeworkOptionSource, ...]
    answer_type: Literal["any", "float", "integer", "exact_string", "contains_string"] | None
    answer: AnswerEnvelope | None


@dataclass(frozen=True, slots=True)
class HomeworkFormSource:
    homework_url: bool
    time_spent_lectures: bool
    time_spent_homework: bool
    faq_contribution: bool
    learning_in_public_cap: int


@dataclass(frozen=True, slots=True)
class HomeworkSource:
    content_id: str
    slug: str
    title: str
    source_path: str
    instructions_source_path: str
    instructions_markdown: str
    due_at: datetime
    initial_state: Literal["closed", "open", "scored"]
    form: HomeworkFormSource
    questions: tuple[HomeworkQuestionSource, ...]


@dataclass(frozen=True, slots=True)
class ModuleFlowSource:
    module: ModuleSource
    homework: HomeworkSource


@dataclass(frozen=True, slots=True)
class ProjectFlowSource:
    slug: str


type CurriculumFlowSource = ModuleFlowSource | ProjectFlowSource


@dataclass(frozen=True, slots=True)
class CohortSource:
    identifier: str
    format: Literal["legacy", "modules"]
    source_path: str | None
    content_id: str | None
    course_slug: str
    legacy_slug: str | None
    year: int | None
    title: str | None
    description: str | None
    published: bool | None
    start_date: date | None
    end_date: date | None
    flow: tuple[CurriculumFlowSource, ...]
    is_implicit_legacy: bool


@dataclass(frozen=True, slots=True)
class CourseRepositorySource:
    schema_version: int
    parser_version: str
    commit_sha: str | None
    course: CourseSource
    cohorts: tuple[CohortSource, ...]
    modules: tuple[ModuleSource, ...]
    homeworks: tuple[HomeworkSource, ...]


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.YAMLError("mapping keys must be scalar") from error
        if duplicate:
            raise yaml.YAMLError("duplicate mapping key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _fail(code: str, path: str = ".", pointer: str = "") -> Never:
    raise CourseRepositoryValidationError(code, path, pointer)


def _validate_repository_path(path: object, *, limits: CourseRepositoryLimits) -> str:
    if not isinstance(path, str) or not path or len(path) > limits.max_path_chars:
        _fail("invalid_repository_path")
    if path != path.strip() or "\\" in path or "\x00" in path:
        _fail("invalid_repository_path", path)
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        _fail("invalid_repository_path", path)
    if path.startswith("/") or _URL_SCHEME.match(path):
        _fail("invalid_repository_path", path)
    pure = PurePosixPath(path)
    if pure.as_posix() != path or any(part in {"", ".", ".."} for part in pure.parts):
        _fail("invalid_repository_path", path)
    return path


def _validated_snapshot(
    snapshot: Mapping[str, bytes], *, limits: CourseRepositoryLimits
) -> dict[str, bytes]:
    if not isinstance(snapshot, Mapping):
        _fail("snapshot_mapping_required")
    if len(snapshot) > limits.max_files:
        _fail("file_count_limit_exceeded")
    result: dict[str, bytes] = {}
    folded_paths: dict[str, str] = {}
    total_bytes = 0
    for raw_path, raw in snapshot.items():
        path = _validate_repository_path(raw_path, limits=limits)
        if not isinstance(raw, bytes):
            _fail("file_bytes_required", path)
        if len(raw) > limits.max_file_bytes:
            _fail("file_size_limit_exceeded", path)
        total_bytes += len(raw)
        if total_bytes > limits.max_total_bytes:
            _fail("repository_size_limit_exceeded")
        folded = path.casefold()
        prior = folded_paths.get(folded)
        if prior is not None and prior != path:
            _fail("case_colliding_paths", path)
        folded_paths[folded] = path
        result[path] = raw
    return result


def _decode_utf8(raw: bytes, *, path: str, limits: CourseRepositoryLimits) -> str:
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail("utf8_required", path)
    if len(value) > limits.max_string_chars:
        _fail("string_size_limit_exceeded", path)
    return value


def _validate_loaded_value(
    value: object,
    *,
    path: str,
    pointer: str,
    limits: CourseRepositoryLimits,
    depth: int = 0,
) -> None:
    if depth > limits.max_yaml_depth:
        _fail("yaml_depth_limit_exceeded", path, pointer)
    if isinstance(value, str):
        if len(value) > limits.max_string_chars:
            _fail("string_size_limit_exceeded", path, pointer)
        return
    if value is None or isinstance(value, (bool, int, float, date, datetime)):
        return
    if isinstance(value, list):
        if len(value) > limits.max_list_items:
            _fail("list_size_limit_exceeded", path, pointer)
        for index, item in enumerate(value):
            _validate_loaded_value(
                item,
                path=path,
                pointer=f"{pointer}/{index}",
                limits=limits,
                depth=depth + 1,
            )
        return
    if isinstance(value, dict):
        if len(value) > limits.max_mapping_items:
            _fail("mapping_size_limit_exceeded", path, pointer)
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                _fail("invalid_mapping_key", path, pointer)
            _validate_loaded_value(
                item,
                path=path,
                pointer=f"{pointer}/{key}",
                limits=limits,
                depth=depth + 1,
            )
        return
    _fail("unsupported_yaml_value", path, pointer)


def _load_yaml_mapping(raw: bytes, *, path: str, limits: CourseRepositoryLimits) -> dict[str, Any]:
    if len(raw) > limits.max_yaml_file_bytes:
        _fail("yaml_file_size_limit_exceeded", path)
    text = _decode_utf8(raw, path=path, limits=limits)
    try:
        depth = 0
        nodes = 0
        documents = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, DocumentStartEvent):
                documents += 1
                if documents > 1:
                    raise yaml.YAMLError("one document is required")
            if isinstance(event, AliasEvent):
                raise yaml.YAMLError("aliases are not supported")
            if isinstance(event, (CollectionStartEvent, ScalarEvent)):
                nodes += 1
                if event.anchor is not None or event.tag is not None:
                    raise yaml.YAMLError("anchors and explicit tags are not supported")
                if nodes > limits.max_yaml_nodes:
                    raise yaml.YAMLError("node count exceeded")
            if isinstance(event, CollectionStartEvent):
                depth += 1
                if depth > limits.max_yaml_depth:
                    raise yaml.YAMLError("depth exceeded")
            elif isinstance(event, yaml.events.CollectionEndEvent):
                depth -= 1
        value = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError:
        _fail("invalid_or_unsafe_yaml", path)
    if not isinstance(value, dict):
        _fail("yaml_mapping_required", path)
    _validate_loaded_value(value, path=path, pointer="", limits=limits)
    return value


def _strict_mapping(
    value: object,
    *,
    path: str,
    pointer: str,
    allowed: frozenset[str],
    required: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("mapping_required", path, pointer)
    unknown = sorted(set(value) - allowed)
    if unknown:
        key = unknown[0]
        code = "plaintext_answer_not_allowed" if key in _PLAINTEXT_ANSWER_KEYS else "unknown_key"
        _fail(code, path, f"{pointer}/{key}")
    missing = sorted(required - set(value))
    if missing:
        _fail("required_key_missing", path, f"{pointer}/{missing[0]}")
    return value


def _schema(mapping: Mapping[str, Any], *, path: str) -> None:
    if (
        type(mapping.get("schema_version")) is not int
        or mapping["schema_version"] != SCHEMA_VERSION
    ):
        _fail("unsupported_schema_version", path, "/schema_version")


def _string(
    value: object,
    *,
    path: str,
    pointer: str,
    minimum: int = 1,
    maximum: int = 10_000,
) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        _fail("invalid_string", path, pointer)
    return value


def _boolean(value: object, *, path: str, pointer: str) -> bool:
    if type(value) is not bool:
        _fail("boolean_required", path, pointer)
    return value


def _integer(value: object, *, path: str, pointer: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("invalid_integer", path, pointer)
    return value


def _slug(value: object, *, path: str, pointer: str, maximum: int = 100) -> str:
    result = _string(value, path=path, pointer=pointer, maximum=maximum)
    if _SLUG.fullmatch(result) is None:
        _fail("invalid_slug", path, pointer)
    return result


def _content_id(value: object, *, path: str, pointer: str) -> str:
    result = _string(value, path=path, pointer=pointer, maximum=36)
    try:
        parsed = UUID(result)
    except ValueError:
        _fail("invalid_content_id", path, pointer)
    if str(parsed) != result:
        _fail("invalid_content_id", path, pointer)
    return result


def _https_url(value: object, *, path: str, pointer: str) -> str:
    result = _string(value, path=path, pointer=pointer, maximum=2_048)
    parsed = urlsplit(result)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        _fail("invalid_https_url", path, pointer)
    return result


def _date(value: object, *, path: str, pointer: str) -> date:
    if isinstance(value, datetime):
        _fail("date_required", path, pointer)
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    _fail("date_required", path, pointer)


def _datetime(value: object, *, path: str, pointer: str) -> datetime:
    result: datetime
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _fail("datetime_required", path, pointer)
    else:
        _fail("datetime_required", path, pointer)
    if result.tzinfo is None or result.utcoffset() is None:
        _fail("timezone_required", path, pointer)
    return result


def _sequence(value: object, *, path: str, pointer: str, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list) or len(value) < minimum:
        _fail("list_required", path, pointer)
    return value


def _referenced_path(
    value: object,
    *,
    path: str,
    pointer: str,
    snapshot: Mapping[str, bytes],
    limits: CourseRepositoryLimits,
    suffix: str,
) -> str:
    result = _string(value, path=path, pointer=pointer, maximum=limits.max_path_chars)
    _validate_repository_path(result, limits=limits)
    if PurePosixPath(result).suffix != suffix:
        _fail("invalid_source_extension", path, pointer)
    if result not in snapshot:
        _fail("source_path_missing", path, pointer)
    return result


def _relative_source_path(
    value: object,
    *,
    manifest_path: str,
    pointer: str,
    snapshot: Mapping[str, bytes],
    limits: CourseRepositoryLimits,
    suffix: str,
    beside_manifest: bool = False,
) -> str:
    relative = _string(value, path=manifest_path, pointer=pointer, maximum=limits.max_path_chars)
    _validate_repository_path(relative, limits=limits)
    relative_path = PurePosixPath(relative)
    if len(relative_path.parts) == 0 or (beside_manifest and len(relative_path.parts) != 1):
        _fail("invalid_relative_source_path", manifest_path, pointer)
    resolved = (PurePosixPath(manifest_path).parent / relative_path).as_posix()
    if PurePosixPath(resolved).suffix != suffix or resolved not in snapshot:
        _fail("source_path_missing", manifest_path, pointer)
    return resolved


class _Parser:
    def __init__(
        self,
        snapshot: Mapping[str, bytes],
        *,
        commit_sha: str | None,
        limits: CourseRepositoryLimits,
    ) -> None:
        self.limits = limits
        self.snapshot = _validated_snapshot(snapshot, limits=limits)
        if commit_sha is not None and _COMMIT_SHA.fullmatch(commit_sha) is None:
            _fail("source_commit_invalid")
        self.commit_sha = commit_sha
        self.content_ids: dict[str, tuple[str, str]] = {}

    def parse(self) -> CourseRepositorySource:
        self._validate_manifest_placement()
        if "course.yaml" not in self.snapshot:
            _fail("course_manifest_missing", "course.yaml")
        course = self._parse_course()
        modules = tuple(self._parse_module(path) for path in self._module_manifest_paths())
        module_slugs: set[str] = set()
        for module in modules:
            if module.slug in module_slugs:
                _fail("duplicate_module_slug", module.source_path, "/slug")
            module_slugs.add(module.slug)
        module_by_path = {module.source_path: module for module in modules}
        homeworks = tuple(
            self._parse_homework(path, course_slug=course.slug)
            for path in self._homework_manifest_paths()
        )
        homework_by_path = {homework.source_path: homework for homework in homeworks}
        explicit_cohort_paths = self._cohort_manifest_paths()
        cohorts = [
            self._parse_cohort(
                path,
                course=course,
                module_by_path=module_by_path,
                homework_by_path=homework_by_path,
            )
            for path in explicit_cohort_paths
        ]
        explicit_identifiers = {cohort.identifier for cohort in cohorts}
        for identifier in self._cohort_directory_identifiers():
            if identifier in explicit_identifiers:
                continue
            cohorts.append(
                CohortSource(
                    identifier=identifier,
                    format="legacy",
                    source_path=None,
                    content_id=None,
                    course_slug=course.slug,
                    legacy_slug=None,
                    year=None,
                    title=None,
                    description=None,
                    published=None,
                    start_date=None,
                    end_date=None,
                    flow=(),
                    is_implicit_legacy=True,
                )
            )
        legacy_slugs: set[str] = set()
        for cohort in cohorts:
            if cohort.legacy_slug is None:
                continue
            if cohort.legacy_slug in legacy_slugs:
                _fail("duplicate_legacy_slug", cohort.source_path or ".", "/legacy_slug")
            legacy_slugs.add(cohort.legacy_slug)
        self._validate_homework_references(cohorts, homeworks)
        return CourseRepositorySource(
            schema_version=SCHEMA_VERSION,
            parser_version=PARSER_VERSION,
            commit_sha=self.commit_sha,
            course=course,
            cohorts=tuple(sorted(cohorts, key=lambda cohort: cohort.identifier)),
            modules=modules,
            homeworks=homeworks,
        )

    def _validate_manifest_placement(self) -> None:
        for path in self.snapshot:
            parts = PurePosixPath(path).parts
            name = parts[-1]
            valid = True
            if name == "course.yaml":
                valid = parts == ("course.yaml",)
            elif name == "module.yaml":
                valid = len(parts) == 2 and parts[0] != "cohorts"
            elif name == "cohort.yaml":
                valid = len(parts) == 3 and parts[0] == "cohorts"
            elif name == "homework.yaml":
                valid = len(parts) == 4 and parts[0] == "cohorts"
            if not valid:
                _fail("manifest_path_invalid", path)

    def _register_content_id(self, value: str, *, kind: str, path: str, pointer: str) -> None:
        prior = self.content_ids.get(value)
        if prior is not None:
            _fail("duplicate_content_id", path, pointer)
        self.content_ids[value] = (kind, path)

    def _module_manifest_paths(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path
                for path in self.snapshot
                if len(PurePosixPath(path).parts) == 2
                and PurePosixPath(path).name == "module.yaml"
                and PurePosixPath(path).parts[0] != "cohorts"
            )
        )

    def _cohort_manifest_paths(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path
                for path in self.snapshot
                if len(PurePosixPath(path).parts) == 3
                and PurePosixPath(path).parts[0] == "cohorts"
                and PurePosixPath(path).name == "cohort.yaml"
            )
        )

    def _homework_manifest_paths(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path
                for path in self.snapshot
                if len(PurePosixPath(path).parts) == 4
                and PurePosixPath(path).parts[0] == "cohorts"
                and PurePosixPath(path).name == "homework.yaml"
            )
        )

    def _cohort_directory_identifiers(self) -> tuple[str, ...]:
        identifiers: set[str] = set()
        for path in self.snapshot:
            parts = PurePosixPath(path).parts
            if len(parts) < 3 or parts[0] != "cohorts":
                continue
            identifier = _slug(parts[1], path=path, pointer="/identifier", maximum=80)
            identifiers.add(identifier)
        return tuple(sorted(identifiers))

    def _parse_course(self) -> CourseSource:
        path = "course.yaml"
        mapping = _strict_mapping(
            _load_yaml_mapping(self.snapshot[path], path=path, limits=self.limits),
            path=path,
            pointer="",
            allowed=frozenset(
                {
                    "schema_version",
                    "content_id",
                    "slug",
                    "title",
                    "description",
                    "description_path",
                    "outcome",
                    "repository_url",
                    "docs_url",
                    "faq_url",
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
                    "outcome",
                    "repository_url",
                    "docs_url",
                    "faq_url",
                    "hashtag",
                    "published",
                }
            ),
        )
        _schema(mapping, path=path)
        if ("description" in mapping) == ("description_path" in mapping):
            _fail("course_description_source_required", path)
        content_id = _content_id(mapping["content_id"], path=path, pointer="/content_id")
        self._register_content_id(content_id, kind="course", path=path, pointer="/content_id")
        description_source_path: str | None = None
        if "description" in mapping:
            description = _string(mapping["description"], path=path, pointer="/description")
        else:
            description_source_path = _referenced_path(
                mapping["description_path"],
                path=path,
                pointer="/description_path",
                snapshot=self.snapshot,
                limits=self.limits,
                suffix=".md",
            )
            description = _decode_utf8(
                self.snapshot[description_source_path],
                path=description_source_path,
                limits=self.limits,
            )
        hashtag = _string(mapping["hashtag"], path=path, pointer="/hashtag", maximum=100)
        if hashtag.startswith("#") or re.fullmatch(r"[A-Za-z0-9_]+", hashtag) is None:
            _fail("invalid_hashtag", path, "/hashtag")
        return CourseSource(
            content_id=content_id,
            slug=_slug(mapping["slug"], path=path, pointer="/slug"),
            title=_string(mapping["title"], path=path, pointer="/title", maximum=200),
            description=description,
            description_source_path=description_source_path,
            outcome=_string(mapping["outcome"], path=path, pointer="/outcome"),
            repository_url=_https_url(
                mapping["repository_url"], path=path, pointer="/repository_url"
            ),
            docs_url=_https_url(mapping["docs_url"], path=path, pointer="/docs_url"),
            faq_url=_https_url(mapping["faq_url"], path=path, pointer="/faq_url"),
            hashtag=hashtag,
            published=_boolean(mapping["published"], path=path, pointer="/published"),
            source_path=path,
        )

    def _parse_module(self, path: str) -> ModuleSource:
        mapping = _strict_mapping(
            _load_yaml_mapping(self.snapshot[path], path=path, limits=self.limits),
            path=path,
            pointer="",
            allowed=frozenset({"schema_version", "content_id", "slug", "title", "units"}),
            required=frozenset({"schema_version", "content_id", "slug", "title", "units"}),
        )
        _schema(mapping, path=path)
        content_id = _content_id(mapping["content_id"], path=path, pointer="/content_id")
        self._register_content_id(content_id, kind="module", path=path, pointer="/content_id")
        units: list[UnitSource] = []
        unit_ids: set[str] = set()
        unit_slugs: set[str] = set()
        unit_paths: set[str] = set()
        for index, raw_unit in enumerate(
            _sequence(mapping["units"], path=path, pointer="/units", minimum=1)
        ):
            pointer = f"/units/{index}"
            unit = _strict_mapping(
                raw_unit,
                path=path,
                pointer=pointer,
                allowed=frozenset({"content_id", "slug", "title", "path"}),
                required=frozenset({"content_id", "slug", "title", "path"}),
            )
            unit_id = _content_id(unit["content_id"], path=path, pointer=f"{pointer}/content_id")
            unit_slug = _slug(unit["slug"], path=path, pointer=f"{pointer}/slug")
            if unit_id in unit_ids or unit_slug in unit_slugs:
                _fail("duplicate_unit_id_or_slug", path, pointer)
            unit_ids.add(unit_id)
            unit_slugs.add(unit_slug)
            self._register_content_id(
                unit_id, kind="unit", path=path, pointer=f"{pointer}/content_id"
            )
            source_path = _relative_source_path(
                unit["path"],
                manifest_path=path,
                pointer=f"{pointer}/path",
                snapshot=self.snapshot,
                limits=self.limits,
                suffix=".md",
            )
            if source_path in unit_paths:
                _fail("duplicate_unit_source", path, f"{pointer}/path")
            unit_paths.add(source_path)
            units.append(
                UnitSource(
                    content_id=unit_id,
                    slug=unit_slug,
                    title=_string(
                        unit["title"], path=path, pointer=f"{pointer}/title", maximum=200
                    ),
                    source_path=source_path,
                    markdown=_decode_utf8(
                        self.snapshot[source_path], path=source_path, limits=self.limits
                    ),
                )
            )
        return ModuleSource(
            content_id=content_id,
            slug=_slug(mapping["slug"], path=path, pointer="/slug"),
            title=_string(mapping["title"], path=path, pointer="/title", maximum=200),
            source_path=path,
            units=tuple(units),
        )

    def _parse_answer_envelope(
        self,
        value: object,
        *,
        path: str,
        pointer: str,
        course_slug: str,
        homework_slug: str,
        question_id: str,
    ) -> AnswerEnvelope:
        answer = _strict_mapping(
            value,
            path=path,
            pointer=pointer,
            allowed=frozenset(
                {
                    "version",
                    "algorithm",
                    "kdf",
                    "key_id",
                    "salt",
                    "nonce",
                    "ciphertext",
                    "context_sha256",
                }
            ),
            required=frozenset(
                {
                    "version",
                    "algorithm",
                    "kdf",
                    "key_id",
                    "salt",
                    "nonce",
                    "ciphertext",
                    "context_sha256",
                }
            ),
        )
        if type(answer["version"]) is not int or answer["version"] != 1:
            _fail("invalid_answer_envelope", path, f"{pointer}/version")
        for key, expected in (("algorithm", "A256GCM"), ("kdf", "HKDF-SHA256")):
            if answer[key] != expected:
                _fail("invalid_answer_envelope", path, f"{pointer}/{key}")
        _slug(answer["key_id"], path=path, pointer=f"{pointer}/key_id", maximum=100)
        for key, size in (("salt", 32), ("nonce", 12)):
            encoded = _string(answer[key], path=path, pointer=f"{pointer}/{key}", maximum=256)
            if _BASE64URL.fullmatch(encoded) is None:
                _fail("invalid_answer_envelope", path, f"{pointer}/{key}")
            try:
                decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            except (ValueError, binascii.Error):
                _fail("invalid_answer_envelope", path, f"{pointer}/{key}")
            if len(decoded) != size:
                _fail("invalid_answer_envelope", path, f"{pointer}/{key}")
        ciphertext = _string(
            answer["ciphertext"], path=path, pointer=f"{pointer}/ciphertext", maximum=100_000
        )
        if _BASE64URL.fullmatch(ciphertext) is None:
            _fail("invalid_answer_envelope", path, f"{pointer}/ciphertext")
        try:
            ciphertext_bytes = base64.urlsafe_b64decode(ciphertext + "=" * (-len(ciphertext) % 4))
        except (ValueError, binascii.Error):
            _fail("invalid_answer_envelope", path, f"{pointer}/ciphertext")
        if len(ciphertext_bytes) < 16:
            _fail("invalid_answer_envelope", path, f"{pointer}/ciphertext")
        context = f"dtc-homework-answer:v1\0{course_slug}\0{homework_slug}\0{question_id}".encode()
        expected_context = hashlib.sha256(context).hexdigest()
        context_sha256 = _string(
            answer["context_sha256"],
            path=path,
            pointer=f"{pointer}/context_sha256",
            maximum=64,
        )
        if _SHA256.fullmatch(context_sha256) is None or context_sha256 != expected_context:
            _fail("answer_context_mismatch", path, f"{pointer}/context_sha256")
        frozen = {key: answer[key] for key in answer}
        return MappingProxyType(frozen)

    def _parse_homework(self, path: str, *, course_slug: str) -> HomeworkSource:
        mapping = _strict_mapping(
            _load_yaml_mapping(self.snapshot[path], path=path, limits=self.limits),
            path=path,
            pointer="",
            allowed=frozenset(
                {
                    "schema_version",
                    "content_id",
                    "slug",
                    "title",
                    "instructions_path",
                    "due_at",
                    "initial_state",
                    "form",
                    "questions",
                }
            ),
            required=frozenset(
                {
                    "schema_version",
                    "content_id",
                    "slug",
                    "title",
                    "instructions_path",
                    "due_at",
                    "initial_state",
                    "form",
                    "questions",
                }
            ),
        )
        _schema(mapping, path=path)
        content_id = _content_id(mapping["content_id"], path=path, pointer="/content_id")
        self._register_content_id(content_id, kind="homework", path=path, pointer="/content_id")
        slug = _slug(mapping["slug"], path=path, pointer="/slug")
        form_mapping = _strict_mapping(
            mapping["form"],
            path=path,
            pointer="/form",
            allowed=frozenset(
                {
                    "homework_url",
                    "time_spent_lectures",
                    "time_spent_homework",
                    "faq_contribution",
                    "learning_in_public_cap",
                }
            ),
            required=frozenset(
                {
                    "homework_url",
                    "time_spent_lectures",
                    "time_spent_homework",
                    "faq_contribution",
                    "learning_in_public_cap",
                }
            ),
        )
        questions: list[HomeworkQuestionSource] = []
        question_ids: set[str] = set()
        for index, raw_question in enumerate(
            _sequence(mapping["questions"], path=path, pointer="/questions", minimum=1)
        ):
            pointer = f"/questions/{index}"
            question = _strict_mapping(
                raw_question,
                path=path,
                pointer=pointer,
                allowed=frozenset(
                    {
                        "content_id",
                        "id",
                        "type",
                        "prompt",
                        "options",
                        "points",
                        "answer_type",
                        "answer",
                    }
                ),
                required=frozenset({"content_id", "id", "type", "prompt", "points"}),
            )
            question_content_id = _content_id(
                question["content_id"], path=path, pointer=f"{pointer}/content_id"
            )
            question_id = _slug(question["id"], path=path, pointer=f"{pointer}/id")
            if question_id in question_ids:
                _fail("duplicate_question_id", path, f"{pointer}/id")
            question_ids.add(question_id)
            self._register_content_id(
                question_content_id,
                kind="question",
                path=path,
                pointer=f"{pointer}/content_id",
            )
            question_type = _string(
                question["type"], path=path, pointer=f"{pointer}/type", maximum=32
            )
            valid_question_types = {
                "multiple_choice",
                "checkboxes",
                "free_form",
                "free_form_long",
            }
            if question_type not in valid_question_types:
                _fail("invalid_question_type", path, f"{pointer}/type")
            options: list[HomeworkOptionSource] = []
            answer_type: str | None = None
            if question_type in {"multiple_choice", "checkboxes"}:
                if "answer_type" in question:
                    _fail("unknown_key", path, f"{pointer}/answer_type")
                option_ids: set[str] = set()
                for option_index, raw_option in enumerate(
                    _sequence(
                        question.get("options"),
                        path=path,
                        pointer=f"{pointer}/options",
                        minimum=2,
                    )
                ):
                    option_pointer = f"{pointer}/options/{option_index}"
                    option = _strict_mapping(
                        raw_option,
                        path=path,
                        pointer=option_pointer,
                        allowed=frozenset({"id", "label"}),
                        required=frozenset({"id", "label"}),
                    )
                    option_id = _slug(option["id"], path=path, pointer=f"{option_pointer}/id")
                    if option_id in option_ids:
                        _fail("duplicate_option_id", path, f"{option_pointer}/id")
                    option_ids.add(option_id)
                    options.append(
                        HomeworkOptionSource(
                            id=option_id,
                            label=_string(
                                option["label"],
                                path=path,
                                pointer=f"{option_pointer}/label",
                                maximum=1_000,
                            ),
                        )
                    )
            else:
                if "options" in question:
                    _fail("unknown_key", path, f"{pointer}/options")
                answer_type = _string(
                    question.get("answer_type"),
                    path=path,
                    pointer=f"{pointer}/answer_type",
                    maximum=32,
                )
                if answer_type not in {
                    "any",
                    "float",
                    "integer",
                    "exact_string",
                    "contains_string",
                }:
                    _fail("invalid_answer_type", path, f"{pointer}/answer_type")
            answer: AnswerEnvelope | None
            if answer_type == "any":
                if "answer" in question:
                    _fail("answer_not_allowed", path, f"{pointer}/answer")
                answer = None
            else:
                if "answer" not in question:
                    _fail("answer_envelope_required", path, f"{pointer}/answer")
                answer = self._parse_answer_envelope(
                    question["answer"],
                    path=path,
                    pointer=f"{pointer}/answer",
                    course_slug=course_slug,
                    homework_slug=slug,
                    question_id=question_id,
                )
            questions.append(
                HomeworkQuestionSource(
                    content_id=question_content_id,
                    id=question_id,
                    type=question_type,  # type: ignore[arg-type]
                    prompt=_string(question["prompt"], path=path, pointer=f"{pointer}/prompt"),
                    points=_integer(
                        question["points"],
                        path=path,
                        pointer=f"{pointer}/points",
                        minimum=0,
                        maximum=1_000,
                    ),
                    options=tuple(options),
                    answer_type=answer_type,  # type: ignore[arg-type]
                    answer=answer,
                )
            )
        instructions_path = _relative_source_path(
            mapping["instructions_path"],
            manifest_path=path,
            pointer="/instructions_path",
            snapshot=self.snapshot,
            limits=self.limits,
            suffix=".md",
            beside_manifest=True,
        )
        initial_state = _string(
            mapping["initial_state"], path=path, pointer="/initial_state", maximum=16
        )
        if initial_state not in {"closed", "open", "scored"}:
            _fail("invalid_homework_state", path, "/initial_state")
        return HomeworkSource(
            content_id=content_id,
            slug=slug,
            title=_string(mapping["title"], path=path, pointer="/title", maximum=200),
            source_path=path,
            instructions_source_path=instructions_path,
            instructions_markdown=_decode_utf8(
                self.snapshot[instructions_path], path=instructions_path, limits=self.limits
            ),
            due_at=_datetime(mapping["due_at"], path=path, pointer="/due_at"),
            initial_state=initial_state,  # type: ignore[arg-type]
            form=HomeworkFormSource(
                homework_url=_boolean(
                    form_mapping["homework_url"], path=path, pointer="/form/homework_url"
                ),
                time_spent_lectures=_boolean(
                    form_mapping["time_spent_lectures"],
                    path=path,
                    pointer="/form/time_spent_lectures",
                ),
                time_spent_homework=_boolean(
                    form_mapping["time_spent_homework"],
                    path=path,
                    pointer="/form/time_spent_homework",
                ),
                faq_contribution=_boolean(
                    form_mapping["faq_contribution"],
                    path=path,
                    pointer="/form/faq_contribution",
                ),
                learning_in_public_cap=_integer(
                    form_mapping["learning_in_public_cap"],
                    path=path,
                    pointer="/form/learning_in_public_cap",
                    minimum=0,
                    maximum=100,
                ),
            ),
            questions=tuple(questions),
        )

    def _parse_cohort(
        self,
        path: str,
        *,
        course: CourseSource,
        module_by_path: Mapping[str, ModuleSource],
        homework_by_path: Mapping[str, HomeworkSource],
    ) -> CohortSource:
        mapping = _strict_mapping(
            _load_yaml_mapping(self.snapshot[path], path=path, limits=self.limits),
            path=path,
            pointer="",
            allowed=frozenset(
                {
                    "schema_version",
                    "content_id",
                    "course",
                    "identifier",
                    "legacy_slug",
                    "year",
                    "title",
                    "description",
                    "format",
                    "published",
                    "start_date",
                    "end_date",
                    "flow",
                }
            ),
            required=frozenset(
                {
                    "schema_version",
                    "content_id",
                    "course",
                    "identifier",
                    "legacy_slug",
                    "year",
                    "title",
                    "description",
                    "format",
                    "published",
                    "start_date",
                    "end_date",
                }
            ),
        )
        _schema(mapping, path=path)
        content_id = _content_id(mapping["content_id"], path=path, pointer="/content_id")
        self._register_content_id(content_id, kind="cohort", path=path, pointer="/content_id")
        identifier = _slug(mapping["identifier"], path=path, pointer="/identifier", maximum=80)
        folder_identifier = PurePosixPath(path).parts[1]
        if identifier != folder_identifier:
            _fail("cohort_identifier_path_mismatch", path, "/identifier")
        course_slug = _slug(mapping["course"], path=path, pointer="/course")
        if course_slug != course.slug:
            _fail("cohort_course_mismatch", path, "/course")
        format_value = _string(mapping["format"], path=path, pointer="/format", maximum=16)
        if format_value not in {"legacy", "modules"}:
            _fail("invalid_cohort_format", path, "/format")
        flow: list[CurriculumFlowSource] = []
        module_paths: set[str] = set()
        homework_paths: set[str] = set()
        project_slugs: set[str] = set()
        if format_value == "legacy":
            if "flow" in mapping:
                _fail("legacy_flow_not_allowed", path, "/flow")
        else:
            for index, raw_item in enumerate(
                _sequence(mapping.get("flow"), path=path, pointer="/flow", minimum=1)
            ):
                pointer = f"/flow/{index}"
                item = _strict_mapping(
                    raw_item,
                    path=path,
                    pointer=pointer,
                    allowed=frozenset({"module", "project"}),
                    required=frozenset(),
                )
                if set(item) == {"module"}:
                    module_ref = _strict_mapping(
                        item["module"],
                        path=path,
                        pointer=f"{pointer}/module",
                        allowed=frozenset({"source", "homework"}),
                        required=frozenset({"source", "homework"}),
                    )
                    module_path = _referenced_path(
                        module_ref["source"],
                        path=path,
                        pointer=f"{pointer}/module/source",
                        snapshot=self.snapshot,
                        limits=self.limits,
                        suffix=".yaml",
                    )
                    if module_path not in module_by_path:
                        _fail("module_source_contract_invalid", path, f"{pointer}/module/source")
                    homework_path = _referenced_path(
                        module_ref["homework"],
                        path=path,
                        pointer=f"{pointer}/module/homework",
                        snapshot=self.snapshot,
                        limits=self.limits,
                        suffix=".yaml",
                    )
                    if homework_path not in homework_by_path:
                        _fail(
                            "homework_source_contract_invalid",
                            path,
                            f"{pointer}/module/homework",
                        )
                    homework_parts = PurePosixPath(homework_path).parts
                    if homework_parts[1] != identifier:
                        _fail("homework_cohort_mismatch", path, f"{pointer}/module/homework")
                    if module_path in module_paths or homework_path in homework_paths:
                        _fail("duplicate_module_or_homework_reference", path, pointer)
                    module_paths.add(module_path)
                    homework_paths.add(homework_path)
                    flow.append(
                        ModuleFlowSource(
                            module=module_by_path[module_path],
                            homework=homework_by_path[homework_path],
                        )
                    )
                elif set(item) == {"project"}:
                    project_slug = _slug(item["project"], path=path, pointer=f"{pointer}/project")
                    if project_slug in project_slugs:
                        _fail("duplicate_project_reference", path, f"{pointer}/project")
                    project_slugs.add(project_slug)
                    flow.append(ProjectFlowSource(slug=project_slug))
                else:
                    _fail("invalid_flow_item", path, pointer)
            if not any(isinstance(item, ModuleFlowSource) for item in flow):
                _fail("module_flow_required", path, "/flow")
        start_date = _date(mapping["start_date"], path=path, pointer="/start_date")
        end_date = _date(mapping["end_date"], path=path, pointer="/end_date")
        if end_date < start_date:
            _fail("invalid_cohort_date_range", path, "/end_date")
        return CohortSource(
            identifier=identifier,
            format=format_value,  # type: ignore[arg-type]
            source_path=path,
            content_id=content_id,
            course_slug=course_slug,
            legacy_slug=_slug(mapping["legacy_slug"], path=path, pointer="/legacy_slug"),
            year=_integer(mapping["year"], path=path, pointer="/year", minimum=2000, maximum=9999),
            title=_string(mapping["title"], path=path, pointer="/title", maximum=200),
            description=_string(mapping["description"], path=path, pointer="/description"),
            published=_boolean(mapping["published"], path=path, pointer="/published"),
            start_date=start_date,
            end_date=end_date,
            flow=tuple(flow),
            is_implicit_legacy=False,
        )

    def _validate_homework_references(
        self, cohorts: Sequence[CohortSource], homeworks: Sequence[HomeworkSource]
    ) -> None:
        referenced = {
            item.homework.source_path
            for cohort in cohorts
            for item in cohort.flow
            if isinstance(item, ModuleFlowSource)
        }
        for homework in homeworks:
            if homework.source_path not in referenced:
                _fail("unreferenced_homework_manifest", homework.source_path)
        seen_slugs: dict[tuple[str, str], str] = {}
        for homework in homeworks:
            identifier = PurePosixPath(homework.source_path).parts[1]
            key = (identifier, homework.slug)
            if key in seen_slugs:
                _fail("duplicate_homework_slug", homework.source_path, "/slug")
            seen_slugs[key] = homework.source_path


def parse_course_repository(
    snapshot: Mapping[str, bytes],
    *,
    commit_sha: str | None = None,
    limits: CourseRepositoryLimits = DEFAULT_LIMITS,
) -> CourseRepositorySource:
    """Parse and validate one immutable repository snapshot.

    ``commit_sha`` is provenance only.  When supplied it must be a full lowercase
    Git SHA; this pure parser does not prove repository reachability.
    """

    return _Parser(snapshot, commit_sha=commit_sha, limits=limits).parse()


__all__ = [
    "DEFAULT_LIMITS",
    "PARSER_VERSION",
    "SCHEMA_VERSION",
    "AnswerEnvelope",
    "CohortSource",
    "CourseRepositoryDiagnostic",
    "CourseRepositoryLimits",
    "CourseRepositorySource",
    "CourseRepositoryValidationError",
    "CourseSource",
    "CurriculumFlowSource",
    "HomeworkFormSource",
    "HomeworkOptionSource",
    "HomeworkQuestionSource",
    "HomeworkSource",
    "ModuleFlowSource",
    "ModuleSource",
    "ProjectFlowSource",
    "UnitSource",
    "parse_course_repository",
]
