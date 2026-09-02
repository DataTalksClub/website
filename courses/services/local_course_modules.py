"""Bounded local adoption of reviewed course-repository module curricula.

This is intentionally not a production importer.  A caller must provide a JSON
manifest containing the exact checkout, commit, selected files, and SHA-256 for
each file.  The manifest is the boundary between a developer's working-tree
checkout and the existing transactional curriculum importer.

Only the three reviewed 2026 cohorts below are accepted.  The source graph is
filtered to that one explicit cohort before it reaches the importer, so legacy
cohorts in the same repository are not inferred, converted, or touched.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, NoReturn
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Count

from content_sync.course_repository import (
    DEFAULT_LIMITS,
    CourseRepositorySource,
    CourseRepositoryValidationError,
    ModuleFlowSource,
    parse_course_repository,
)
from courses.models import Cohort, CurriculumFormat
from courses.services.curriculum_import import (
    CurriculumImportCommand,
    CurriculumImportError,
    CurriculumImportResult,
    import_course_repository_curriculum,
    validate_source_path,
)
from courses.services.local_course_seed import LocalCourseSeedError, assert_local_database

PREPARATION_SCHEMA_VERSION = 1
TARGET_COHORTS = MappingProxyType(
    {
        "llm-zoomcamp": "2026",
        "ml-zoomcamp": "2026",
        "ai-dev-tools-zoomcamp": "2026",
    }
)
TARGET_REPOSITORIES = MappingProxyType(
    {
        "llm-zoomcamp": ("DataTalksClub", "llm-zoomcamp"),
        "ml-zoomcamp": ("DataTalksClub", "machine-learning-zoomcamp"),
        "ai-dev-tools-zoomcamp": ("DataTalksClub", "ai-dev-tools-zoomcamp"),
    }
)
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SENSITIVE_SOURCE_PARTS = frozenset(
    {
        "credential",
        "credentials",
        "enrollment",
        "enrollments",
        "learner",
        "learners",
        "private",
        "registration",
        "registrations",
        "secret",
        "secrets",
        "student",
        "students",
        "submission",
        "submissions",
        "token",
        "tokens",
    }
)
_SOURCE_FIELDS = frozenset(
    {
        "source_uuid",
        "source_stable_id",
        "repository_owner",
        "repository_name",
        "repository_branch",
        "commit_sha",
        "cohort_identifier",
        "root",
        "snapshot_sha256",
        "files",
        "homework_slug_overrides",
        "unpublished_commit_reason",
    }
)


class LocalCourseModulesError(RuntimeError):
    """A safe, bounded refusal to inspect or write local course data."""


@dataclass(frozen=True, slots=True)
class PreparedCourseSource:
    """Safe source metadata suitable for command output."""

    source_stable_id: str
    source_uuid: UUID
    repository_owner: str
    repository_name: str
    repository_branch: str
    root: str
    commit_sha: str
    snapshot_sha256: str
    cohort_identifier: str
    cohort_count: int
    module_count: int
    homework_count: int
    commit_public: bool = True
    unpublished_commit_reason: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "source_stable_id": self.source_stable_id,
            "source_uuid": str(self.source_uuid),
            "repository": f"{self.repository_owner}/{self.repository_name}",
            "branch": self.repository_branch,
            "root": self.root,
            "commit_sha": self.commit_sha,
            "snapshot_sha256": self.snapshot_sha256,
            "cohort_identifier": self.cohort_identifier,
            "cohorts": self.cohort_count,
            "modules": self.module_count,
            "homeworks": self.homework_count,
            "commit_public": self.commit_public,
            "unpublished_commit_reason": self.unpublished_commit_reason,
        }


@dataclass(frozen=True, slots=True)
class PreparedCourseImport:
    source: PreparedCourseSource
    counts: Mapping[str, int]
    replayed: bool

    def summary(self) -> dict[str, Any]:
        return {
            **self.source.summary(),
            "counts": dict(self.counts),
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class LocalCourseModulesPreparationResult:
    sources: tuple[PreparedCourseSource, ...]
    imports: tuple[PreparedCourseImport, ...]
    written: bool
    database_counts: Mapping[str, Mapping[str, int]]

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": PREPARATION_SCHEMA_VERSION,
            "written": self.written,
            "sources": [source.summary() for source in self.sources],
            "imports": [item.summary() for item in self.imports],
            "database_counts": {key: dict(value) for key, value in self.database_counts.items()},
        }


def snapshot_checksum(checksums: Mapping[str, str]) -> str:
    """Return the stable digest used by the preparation manifest.

    The digest covers normalized repository paths and their raw SHA-256 values;
    it is independent of JSON key order and does not put source content in
    command output or the preparation record.
    """

    digest = hashlib.sha256()
    for path in sorted(checksums):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(checksums[path].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _refuse(code: str) -> NoReturn:
    raise LocalCourseModulesError(code)


def _mapping(value: object, *, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _refuse(code)
    return value


def _string(value: object, *, code: str) -> str:
    if not isinstance(value, str) or not value:
        _refuse(code)
    return value


def _uuid(value: object) -> UUID:
    raw = _string(value, code="source_uuid_invalid")
    try:
        return UUID(raw)
    except ValueError:
        _refuse("source_uuid_invalid")


def _validate_snapshot_paths(raw_files: object) -> dict[str, str]:
    files = _mapping(raw_files, code="source_files_invalid")
    if not files or len(files) > DEFAULT_LIMITS.max_files:
        _refuse("source_files_invalid")
    result: dict[str, str] = {}
    for raw_path, raw_checksum in files.items():
        try:
            validate_source_path(raw_path)
        except (ValidationError, TypeError):
            _refuse("source_path_invalid")
        if PurePosixPath(raw_path).parts[0] == ".git":
            _refuse("source_path_invalid")
        if any(
            part.casefold().startswith(".env") or part.casefold() in _SENSITIVE_SOURCE_PARTS
            for part in PurePosixPath(raw_path).parts
        ):
            _refuse("source_sensitive_path")
        if not isinstance(raw_checksum, str) or _SHA256.fullmatch(raw_checksum) is None:
            _refuse("source_checksum_invalid")
        result[raw_path] = raw_checksum
    if "course.yaml" not in result:
        _refuse("course_manifest_missing")
    return result


def _validate_git_checkout(root: Path, *, commit_sha: str, branch: str) -> None:
    commands = (
        (["git", "-C", str(root), "rev-parse", "HEAD"], commit_sha),
        (["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], branch),
        (
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            "",
        ),
    )
    for command, expected in commands:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            _refuse("source_git_unavailable")
        if completed.returncode != 0 or completed.stdout.strip() != expected:
            _refuse("source_git_revision_mismatch")


def _git(root: Path, *arguments: str) -> str | None:
    """Return trimmed git output for the checkout, or ``None`` when git fails."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def public_repository_urls(owner: str, name: str) -> frozenset[str]:
    """Return the URL spellings that identify one public GitHub repository."""

    path = f"{owner}/{name}".casefold()
    return frozenset(
        {
            f"https://github.com/{path}",
            f"https://github.com/{path}.git",
            f"http://github.com/{path}",
            f"http://github.com/{path}.git",
            f"ssh://git@github.com/{path}",
            f"ssh://git@github.com/{path}.git",
            f"git@github.com:{path}",
            f"git@github.com:{path}.git",
        }
    )


def _public_remote_names(root: Path, *, owner: str, name: str) -> tuple[str, ...]:
    listing = _git(root, "config", "--get-regexp", r"^remote\..*\.url$")
    if listing is None:
        return ()
    expected = public_repository_urls(owner, name)
    remotes: list[str] = []
    for line in listing.splitlines():
        key, _, url = line.partition(" ")
        if not url or not key.startswith("remote.") or not key.endswith(".url"):
            continue
        if url.strip().rstrip("/").casefold() in expected:
            remotes.append(key[len("remote.") : -len(".url")])
    return tuple(remotes)


def commit_is_public(root: Path, *, owner: str, name: str, commit_sha: str) -> bool:
    """Return whether the commit is on a branch of the public GitHub remote.

    A unit records its provenance as a repository plus a commit, and every
    public affordance built from that provenance -- the edit link, the raw image
    URL, a reader following a source path -- assumes the commit is one the
    public can resolve.  Reachability is read from the checkout's own
    remote-tracking branches, so this stays offline and deterministic: a
    checkout cloned from a private or local mirror simply has no branch of the
    public remote containing the commit.
    """

    remotes = _public_remote_names(root, owner=owner, name=name)
    if not remotes:
        return False
    containing = _git(root, "branch", "--remotes", "--contains", commit_sha, "--format=%(refname)")
    if not containing:
        return False
    prefixes = tuple(f"refs/remotes/{remote}/" for remote in remotes)
    return any(line.strip().startswith(prefixes) for line in containing.splitlines())


def validate_public_commit(
    root: Path,
    *,
    owner: str,
    name: str,
    commit_sha: str,
    unpublished_reason: str,
) -> bool:
    if commit_is_public(root, owner=owner, name=name, commit_sha=commit_sha):
        return True
    if not unpublished_reason:
        # Importing a commit no reader can resolve publishes pages whose source
        # links, images and edit affordances can only 404.  Refuse rather than
        # degrade the page, and make the operator state the reason to proceed.
        _refuse("source_commit_not_public")
    return False


def _read_snapshot(root: Path, checksums: Mapping[str, str]) -> dict[str, bytes]:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        _refuse("source_checkout_unavailable")
    resolved_root = root.resolve()
    snapshot: dict[str, bytes] = {}
    total_bytes = 0
    for relative_path, expected_checksum in checksums.items():
        candidate = root / relative_path
        cursor = root
        for part in PurePosixPath(relative_path).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                _refuse("source_symlink_not_allowed")
        try:
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(resolved_root)
            if not resolved_candidate.is_file():
                _refuse("source_file_unavailable")
            content = resolved_candidate.read_bytes()
        except (OSError, ValueError):
            _refuse("source_file_unavailable")
        if len(content) > DEFAULT_LIMITS.max_file_bytes:
            _refuse("source_file_too_large")
        total_bytes += len(content)
        if total_bytes > DEFAULT_LIMITS.max_total_bytes:
            _refuse("source_size_limit_exceeded")
        if hashlib.sha256(content).hexdigest() != expected_checksum:
            _refuse("source_checksum_mismatch")
        snapshot[relative_path] = content
    return snapshot


def _load_manifest(path: Path) -> tuple[Mapping[str, Any], ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _refuse("preparation_manifest_invalid")
    manifest = _mapping(raw, code="preparation_manifest_invalid")
    if set(manifest) != {"schema_version", "sources"}:
        _refuse("preparation_manifest_invalid")
    if manifest.get("schema_version") != PREPARATION_SCHEMA_VERSION:
        _refuse("preparation_schema_unsupported")
    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list) or len(raw_sources) != len(TARGET_COHORTS):
        _refuse("preparation_source_count_invalid")
    sources: list[Mapping[str, Any]] = []
    for raw_source in raw_sources:
        source = _mapping(raw_source, code="preparation_source_invalid")
        required = _SOURCE_FIELDS - {"homework_slug_overrides"}
        if not required.issubset(source) or not set(source).issubset(_SOURCE_FIELDS):
            _refuse("preparation_source_invalid")
        sources.append(source)
    return tuple(sources)


def _validate_source_record(record: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
    stable_id = _string(record.get("source_stable_id"), code="source_stable_id_invalid")
    if stable_id not in TARGET_COHORTS:
        _refuse("source_not_reviewed")
    cohort_identifier = _string(record.get("cohort_identifier"), code="cohort_selection_invalid")
    if cohort_identifier != TARGET_COHORTS[stable_id]:
        _refuse("cohort_selection_invalid")
    source_uuid = _uuid(record.get("source_uuid"))
    owner = _string(record.get("repository_owner"), code="repository_identity_invalid")
    name = _string(record.get("repository_name"), code="repository_identity_invalid")
    branch = _string(record.get("repository_branch"), code="repository_identity_invalid")
    if TARGET_REPOSITORIES[stable_id] != (owner, name) or branch != "main":
        _refuse("repository_identity_invalid")
    commit_sha = _string(record.get("commit_sha"), code="source_commit_invalid")
    if not _SHA1.fullmatch(commit_sha):
        _refuse("source_commit_invalid")
    root_raw = _string(record.get("root"), code="source_checkout_invalid")
    root = Path(root_raw)
    checksums = _validate_snapshot_paths(record.get("files"))
    expected_snapshot = _string(record.get("snapshot_sha256"), code="source_snapshot_invalid")
    if _SHA256.fullmatch(expected_snapshot) is None:
        _refuse("source_snapshot_invalid")
    if snapshot_checksum(checksums) != expected_snapshot:
        _refuse("source_snapshot_checksum_mismatch")
    overrides_raw = record.get("homework_slug_overrides", {})
    overrides = _mapping(overrides_raw, code="homework_slug_overrides_invalid")
    for path, slug in overrides.items():
        try:
            validate_source_path(path)
        except (ValidationError, TypeError):
            _refuse("homework_slug_overrides_invalid")
        if path not in checksums or not isinstance(slug, str) or _SLUG.fullmatch(slug) is None:
            _refuse("homework_slug_overrides_invalid")
    unpublished_reason = record.get("unpublished_commit_reason", "")
    if not isinstance(unpublished_reason, str) or len(unpublished_reason) > 512:
        _refuse("unpublished_commit_reason_invalid")
    unpublished_reason = unpublished_reason.strip()
    _validate_git_checkout(root, commit_sha=commit_sha, branch=branch)
    commit_public = validate_public_commit(
        root,
        owner=owner,
        name=name,
        commit_sha=commit_sha,
        unpublished_reason=unpublished_reason,
    )
    snapshot = _read_snapshot(root, checksums)
    return (
        {
            "source_uuid": source_uuid,
            "source_stable_id": stable_id,
            "repository_owner": owner,
            "repository_name": name,
            "repository_branch": branch,
            "commit_sha": commit_sha,
            "cohort_identifier": cohort_identifier,
            "root": root,
            "checksums": checksums,
            "snapshot_sha256": expected_snapshot,
            "homework_slug_overrides": dict(overrides),
            "commit_public": commit_public,
            "unpublished_commit_reason": "" if commit_public else unpublished_reason,
        },
        snapshot,
    )


def select_target_cohort(
    source: CourseRepositorySource,
    *,
    source_stable_id: str,
    cohort_identifier: str,
):
    """Return exactly the reviewed explicit modules cohort from a source graph."""

    if source_stable_id not in TARGET_COHORTS:
        _refuse("source_not_reviewed")
    if cohort_identifier != TARGET_COHORTS[source_stable_id]:
        _refuse("cohort_selection_invalid")
    if source.course.slug != source_stable_id:
        _refuse("source_course_identity_mismatch")
    matches = [
        cohort
        for cohort in source.cohorts
        if not cohort.is_implicit_legacy and cohort.identifier == cohort_identifier
    ]
    if len(matches) != 1:
        _refuse("target_cohort_missing_or_ambiguous")
    target = matches[0]
    if target.format != CurriculumFormat.MODULES:
        _refuse("target_cohort_not_modules")
    return target


def target_source_graph(
    source: CourseRepositorySource,
    *,
    source_stable_id: str,
    cohort_identifier: str,
    homework_slug_overrides: Mapping[str, str] | None = None,
) -> CourseRepositorySource:
    """Limit one parsed source to one approved cohort and its referenced rows."""

    target = select_target_cohort(
        source,
        source_stable_id=source_stable_id,
        cohort_identifier=cohort_identifier,
    )
    overrides = dict(homework_slug_overrides or {})
    target_homework_paths = {
        item.homework.source_path for item in target.flow if isinstance(item, ModuleFlowSource)
    }
    if set(overrides) - target_homework_paths:
        _refuse("homework_slug_overrides_invalid")

    flow = []
    module_paths: set[str] = set()
    homework_paths: set[str] = set()
    for item in target.flow:
        if not isinstance(item, ModuleFlowSource):
            flow.append(item)
            continue
        homework = item.homework
        override = overrides.get(homework.source_path)
        if override is not None:
            homework = replace(homework, slug=override)
        flow.append(replace(item, homework=homework))
        module_paths.add(item.module.source_path)
        homework_paths.add(homework.source_path)
    target = replace(target, flow=tuple(flow))
    modules = tuple(module for module in source.modules if module.source_path in module_paths)
    homeworks = tuple(
        homework for homework in source.homeworks if homework.source_path in homework_paths
    )
    homework_by_path = {homework.source_path: homework for homework in homeworks}
    for item in target.flow:
        if isinstance(item, ModuleFlowSource):
            source_homework = homework_by_path.get(item.homework.source_path)
            if source_homework is None or source_homework.slug != item.homework.slug:
                homeworks = tuple(
                    replace(homework, slug=item.homework.slug)
                    if homework.source_path == item.homework.source_path
                    else homework
                    for homework in homeworks
                )
    return replace(source, cohorts=(target,), modules=modules, homeworks=homeworks)


def _prepare_source(
    record: Mapping[str, Any],
) -> tuple[PreparedCourseSource, CurriculumImportCommand]:
    values, snapshot = _validate_source_record(record)
    try:
        parsed = parse_course_repository(
            snapshot,
            commit_sha=values["commit_sha"],
            limits=DEFAULT_LIMITS,
        )
    except CourseRepositoryValidationError as error:
        diagnostic = error.diagnostics[0]
        raise LocalCourseModulesError(
            f"source_invalid:{diagnostic.code}:{diagnostic.source_path}"
        ) from None
    graph = target_source_graph(
        parsed,
        source_stable_id=values["source_stable_id"],
        cohort_identifier=values["cohort_identifier"],
        homework_slug_overrides=values["homework_slug_overrides"],
    )
    module_count = sum(isinstance(item, ModuleFlowSource) for item in graph.cohorts[0].flow)
    homework_count = module_count
    prepared = PreparedCourseSource(
        source_stable_id=values["source_stable_id"],
        source_uuid=values["source_uuid"],
        repository_owner=values["repository_owner"],
        repository_name=values["repository_name"],
        repository_branch=values["repository_branch"],
        root=str(values["root"]),
        commit_sha=values["commit_sha"],
        snapshot_sha256=values["snapshot_sha256"],
        cohort_identifier=values["cohort_identifier"],
        cohort_count=1,
        module_count=module_count,
        homework_count=homework_count,
        commit_public=values["commit_public"],
        unpublished_commit_reason=values["unpublished_commit_reason"],
    )
    command = CurriculumImportCommand(
        source=graph,
        source_uuid=values["source_uuid"],
        source_stable_id=values["source_stable_id"],
        repository_owner=values["repository_owner"],
        repository_name=values["repository_name"],
        repository_branch=values["repository_branch"],
        commit_sha=values["commit_sha"],
        source_checksums=values["checksums"],
        manifest_checksum=values["snapshot_sha256"],
        preserve_existing_records=True,
    )
    return prepared, command


def _database_counts() -> dict[str, Mapping[str, int]]:
    formats = {
        row["curriculum_format"]: row["total"]
        for row in Cohort.objects.values("curriculum_format").annotate(total=Count("pk"))
    }
    finished = {
        ("finished" if row["finished"] else "active"): row["total"]
        for row in Cohort.objects.values("finished").annotate(total=Count("pk"))
    }
    return {
        "cohorts_by_format": MappingProxyType(formats),
        "cohorts_by_status": MappingProxyType(finished),
    }


def prepare_local_course_modules(
    manifest_path: str | Path,
    *,
    write: bool = True,
) -> LocalCourseModulesPreparationResult:
    """Validate explicit local snapshots and optionally project all three targets."""

    try:
        assert_local_database()
    except LocalCourseSeedError as error:
        raise LocalCourseModulesError(str(error)) from None
    records = _load_manifest(Path(manifest_path))
    by_stable_id: dict[str, Mapping[str, Any]] = {}
    for record in records:
        stable_id = record.get("source_stable_id")
        if not isinstance(stable_id, str) or stable_id in by_stable_id:
            _refuse("preparation_source_identity_collision")
        by_stable_id[stable_id] = record
    if set(by_stable_id) != set(TARGET_COHORTS):
        _refuse("preparation_source_set_invalid")

    prepared_commands = [_prepare_source(by_stable_id[stable_id]) for stable_id in TARGET_COHORTS]
    prepared_sources = tuple(item[0] for item in prepared_commands)
    imports: list[PreparedCourseImport] = []
    if write:
        for prepared, command in prepared_commands:
            try:
                result: CurriculumImportResult = import_course_repository_curriculum(command)
            except CurriculumImportError as error:
                raise LocalCourseModulesError(
                    f"source_import_rejected:{prepared.source_stable_id}:{error.code}"
                ) from None
            imports.append(
                PreparedCourseImport(
                    source=prepared,
                    counts=MappingProxyType(dict(result.counts)),
                    replayed=result.replayed,
                )
            )
    return LocalCourseModulesPreparationResult(
        sources=prepared_sources,
        imports=tuple(imports),
        written=write,
        database_counts=_database_counts(),
    )


__all__ = [
    "commit_is_public",
    "validate_public_commit",
    "public_repository_urls",
    "LocalCourseModulesError",
    "LocalCourseModulesPreparationResult",
    "PreparedCourseImport",
    "PreparedCourseSource",
    "PREPARATION_SCHEMA_VERSION",
    "TARGET_COHORTS",
    "prepare_local_course_modules",
    "select_target_cohort",
    "snapshot_checksum",
    "target_source_graph",
]
