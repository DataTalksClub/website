"""Pure layout checks and bounded reports for a schema-2 course repository.

The website-side counterpart of the operations checker: given a validated
snapshot and its parsed schema-2 graph, prove the layout invariants the import
relies on and assemble the deterministic report the release gate records.  No
network, no code execution, and no file contents beyond bounded checksums and
paths -- the report is safe to attach to a run record.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

from courses.services.curriculum_source import CourseRepositorySource

from .course_repository import CourseRepositoryLimits, DEFAULT_LIMITS
from .course_repository_v2 import (
    COHORTS_ROOT,
    MODULE_MANIFEST_NAME,
    V2_MODULE_DIR,
)

_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_NUMBER_PREFIX = re.compile(r"^([0-9]{2,})-")


class CourseRepositoryLayoutError(ValueError):
    """A layout invariant failed; ``code`` is a stable diagnostic identifier."""

    def __init__(self, code: str, source_path: str = ".") -> None:
        self.code = code
        self.source_path = source_path
        super().__init__(f"{source_path}: {code}")


@dataclass(frozen=True, slots=True)
class LayoutReport:
    """Deterministic, content-free evidence about one schema-2 snapshot."""

    schema_version: int
    parser_version: str
    commit_sha: str | None
    course_slug: str
    course_content_id: str
    modules: tuple[dict[str, Any], ...]
    cohorts: tuple[dict[str, Any], ...]
    archive_module_records: int
    file_count: int
    total_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "parser_version": self.parser_version,
            "commit_sha": self.commit_sha,
            "course": {
                "content_id": self.course_content_id,
                "slug": self.course_slug,
            },
            "modules": list(self.modules),
            "cohorts": list(self.cohorts),
            "archive_module_records": self.archive_module_records,
            "files": {"count": self.file_count, "total_bytes": self.total_bytes},
        }


def checksum_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require_full_commit_sha(commit_sha: str) -> str:
    """Reject anything but a full lowercase 40-character commit SHA."""

    if not isinstance(commit_sha, str) or _COMMIT_SHA.fullmatch(commit_sha) is None:
        raise CourseRepositoryLayoutError("source_commit_invalid", "course.yaml")
    return commit_sha


def check_root_modules(snapshot: Mapping[str, bytes]) -> None:
    """Fail on a numbered root directory without its module manifest (a stub).

    The parser validates the manifests it reads; a root directory that is
    numbered but carries no ``module.yaml`` is silent to the parser and would
    quietly publish an empty module index, so the layout gate refuses it.
    """

    numbered: set[str] = set()
    manifests: set[str] = set()
    for path in snapshot:
        parts = PurePosixPath(path).parts
        if len(parts) != 2 or parts[0] == COHORTS_ROOT:
            continue
        if parts[1] == MODULE_MANIFEST_NAME:
            manifests.add(parts[0])
            if V2_MODULE_DIR.fullmatch(parts[0]) is None:
                raise CourseRepositoryLayoutError(
                    "numbered_module_required", path
                )
        elif _NUMBER_PREFIX.match(parts[0]) is not None:
            numbered.add(parts[0])
    stubs = sorted(numbered - manifests)
    if stubs:
        raise CourseRepositoryLayoutError("root_module_stub", f"{stubs[0]}/")


def build_layout_report(
    parsed: CourseRepositorySource,
    snapshot: Mapping[str, bytes],
    *,
    limits: CourseRepositoryLimits = DEFAULT_LIMITS,
) -> LayoutReport:
    """Assemble the bounded report for an already-parsed schema-2 source."""

    if parsed.schema_version != 2:
        raise CourseRepositoryLayoutError("layout_report_requires_schema_2", "course.yaml")
    check_root_modules(snapshot)

    modules: list[dict[str, Any]] = []
    for module in parsed.modules:
        lessons = [
            {
                "content_id": unit.content_id,
                "slug": unit.slug,
                "path": unit.source_path,
                "checksum": checksum_bytes(snapshot[unit.source_path]),
            }
            for unit in module.units
        ]
        modules.append(
            {
                "scope": module.scope,
                "content_id": module.content_id,
                "slug": module.slug,
                "title": module.title,
                "path": module.source_path,
                "overview_path": (
                    PurePosixPath(module.source_path).parent.joinpath("README.md").as_posix()
                    if module.overview_markdown is not None
                    else None
                ),
                "lessons": lessons,
            }
        )

    cohorts: list[dict[str, Any]] = []
    for cohort in parsed.cohorts:
        record: dict[str, Any] = {
            "identifier": cohort.identifier,
            "delivery": cohort.delivery,
            "curriculum": cohort.curriculum,
            "homework": [
                {"module": binding.module, "source": binding.source}
                for binding in cohort.homework_bindings
            ],
        }
        if cohort.archive_notice_path is not None:
            record["archive"] = {"notice_path": cohort.archive_notice_path}
        cohorts.append(record)

    archive_prefixes = {
        f"{COHORTS_ROOT}/{cohort.identifier}/"
        for cohort in parsed.cohorts
        if cohort.curriculum == "github_archive"
    }
    archive_module_records = sum(
        1
        for module in parsed.modules
        if module.source_path.startswith(tuple(archive_prefixes))
    )

    return LayoutReport(
        schema_version=parsed.schema_version,
        parser_version=parsed.parser_version,
        commit_sha=parsed.commit_sha,
        course_slug=parsed.course.slug,
        course_content_id=parsed.course.content_id,
        modules=tuple(modules),
        cohorts=tuple(cohorts),
        archive_module_records=archive_module_records,
        file_count=len(snapshot),
        total_bytes=sum(len(raw) for raw in snapshot.values()),
    )


__all__ = [
    "CourseRepositoryLayoutError",
    "LayoutReport",
    "build_layout_report",
    "check_root_modules",
    "checksum_bytes",
    "require_full_commit_sha",
]
