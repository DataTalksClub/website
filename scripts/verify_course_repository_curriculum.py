#!/usr/bin/env python3
"""No-network release gate for a schema-2 shared-curriculum course repository.

Reads one exact commit out of a local checkout with ``git archive``, parses it
with the website's schema-2 parser, and emits a deterministic, content-free
report: counts, paths, and checksums only -- never Markdown, notebook
contents, answer keys, or credentials.  It executes nothing from the source
and never touches the network or the database.

Exit codes: ``0`` the layout and parser contract hold; ``1`` a bounded refusal
(the report carries the stable diagnostic code); ``2`` usage/transport
failure.

Typical use:

    uv run --frozen python scripts/verify_course_repository_curriculum.py \
        --checkout /home/alexey/git/llm-zoomcamp \
        --commit <full-40-char-sha> \
        --json .tmp/course-content-research/layout-report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The release gate runs as a plain script (``uv run --frozen python
# scripts/verify_course_repository_curriculum.py``), which puts ``scripts/``
# -- not the repository root -- on sys.path.  Fix that before the imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from content_sync import snapshot
from content_sync.course_repository import (
    DEFAULT_LIMITS,
    CourseRepositoryLimits,
    CourseRepositoryValidationError,
    parse_course_repository,
)
from content_sync.course_repository_layout import (
    CourseRepositoryLayoutError,
    build_layout_report,
    require_full_commit_sha,
)

GIT_ARCHIVE_TIMEOUT_SECONDS = 120
MAX_ARCHIVE_BYTES = 200_000_000


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify_course_repository_curriculum",
        description=(
            "Verify one commit of a course repository against the schema-2 "
            "shared-curriculum contract. Offline and read-only."
        ),
    )
    parser.add_argument(
        "--checkout",
        required=True,
        type=Path,
        help="Path to a local checkout of the course repository.",
    )
    parser.add_argument(
        "--commit",
        required=True,
        help="Full lowercase 40-character commit SHA to verify.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        type=Path,
        default=None,
        help="Write the deterministic layout report to this path.",
    )
    parser.add_argument(
        "--expect-json",
        dest="expect_json",
        type=Path,
        default=None,
        help=(
            "Compare the report against an expected normalized-output JSON "
            "(the versioned known-output fixture) and fail on drift."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Parse, check, and report without writing the --json file. "
            "Verification output still goes to stdout."
        ),
    )
    return parser.parse_args(argv)


def _read_checkout_snapshot(
    root: Path, commit_sha: str, limits: CourseRepositoryLimits
) -> dict[str, bytes]:
    def admit(path: str, size: int, files: int, total: int) -> None:
        if size > limits.max_file_bytes:
            raise snapshot.SnapshotError(
                "file_too_large", detail=f"{path} at {size} bytes"
            )
        if files >= limits.max_files:
            raise snapshot.SnapshotError(
                "source_limit_exceeded", detail=f"more than {limits.max_files} files"
            )
        if total + size > limits.max_total_bytes:
            raise snapshot.SnapshotError(
                "source_limit_exceeded",
                detail=f"more than {limits.max_total_bytes} bytes",
            )

    archive_bytes = snapshot.run_git_archive(
        root,
        commit_sha,
        timeout_seconds=GIT_ARCHIVE_TIMEOUT_SECONDS,
        max_bytes=MAX_ARCHIVE_BYTES,
    )
    try:
        return snapshot.read_snapshot_archive(
            archive_bytes, limits=limits, strip_root=False, admit=admit
        )
    except snapshot.SnapshotError as error:
        raise SystemExit(
            f"2 course_repository_{error.code}: {error.detail}"
        ) from error


def _fail(code: str, source_path: str, json_output: Path | None, dry_run: bool) -> int:
    report = {
        "ok": False,
        "error": {"code": code, "source_path": source_path},
    }
    print(json.dumps(report, sort_keys=True, indent=2))
    if json_output is not None and not dry_run:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    return 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        commit_sha = require_full_commit_sha(args.commit)
    except CourseRepositoryLayoutError as error:
        print(f"2 {error.code}: --commit must be a full lowercase 40-character SHA")
        return 2
    root = args.checkout
    if not root.is_dir() or root.is_symlink():
        print(f"2 course_repository_checkout_unavailable: {root}")
        return 2

    limits = DEFAULT_LIMITS
    try:
        parsed_snapshot = _read_checkout_snapshot(root, commit_sha, limits)
    except snapshot.SnapshotError as error:
        print(f"2 course_repository_{error.code}: {error.detail}")
        return 2

    try:
        parsed = parse_course_repository(parsed_snapshot, commit_sha=commit_sha, limits=limits)
        report = build_layout_report(parsed, parsed_snapshot, limits=limits)
    except CourseRepositoryValidationError as error:
        diagnostic = error.diagnostics[0]
        return _fail(diagnostic.code, f"{diagnostic.source_path}{diagnostic.pointer}", args.json_output, args.dry_run)
    except CourseRepositoryLayoutError as error:
        return _fail(error.code, error.source_path, args.json_output, args.dry_run)

    payload = {"ok": True, **report.as_dict()}

    if args.expect_json is not None:
        expected = json.loads(args.expect_json.read_text(encoding="utf-8"))
        drift = _report_drift(payload, expected)
        if drift:
            payload = {"ok": False, "contract_drift": drift, **report.as_dict()}

    encoded = json.dumps(payload, sort_keys=True, indent=2)
    print(encoded)
    if args.json_output is not None and not args.dry_run:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if payload["ok"] else 1


def _report_drift(payload: dict[str, object], expected: dict[str, object]) -> list[str]:
    """Return bounded drift notes between a report and its expected fixture.

    Comparison is confined to the fixture's schema keys -- module/lesson
    identity and cohort records -- never checksums or byte counts, which
    legitimately change with unrelated repository traffic.
    """

    drift: list[str] = []
    expected_modules = expected.get("modules")
    if isinstance(expected_modules, list):
        actual_modules = payload.get("modules")
        if not isinstance(actual_modules, list) or len(actual_modules) != len(expected_modules):
            drift.append("module_count")
        else:
            for index, (actual, wanted) in enumerate(
                zip(actual_modules, expected_modules, strict=False)
            ):
                if not isinstance(actual, dict) or not isinstance(wanted, dict):
                    drift.append(f"modules[{index}]")
                    continue
                for key in ("scope", "content_id", "slug"):
                    if actual.get(key) != wanted.get(key):
                        drift.append(f"modules[{index}].{key}")
                actual_lessons = actual.get("lessons")
                wanted_lessons = wanted.get("lessons")
                if isinstance(wanted_lessons, list):
                    if not isinstance(actual_lessons, list) or len(actual_lessons) != len(
                        wanted_lessons
                    ):
                        drift.append(f"modules[{index}].lesson_count")
                        continue
                    for lesson_index, (lesson, wanted_lesson) in enumerate(
                        zip(actual_lessons, wanted_lessons, strict=False)
                    ):
                        if not isinstance(lesson, dict) or not isinstance(wanted_lesson, dict):
                            drift.append(f"modules[{index}].lessons[{lesson_index}]")
                            continue
                        for key in ("content_id", "slug", "path"):
                            if lesson.get(key) != wanted_lesson.get(key):
                                drift.append(
                                    f"modules[{index}].lessons[{lesson_index}].{key}"
                                )
    expected_cohorts = expected.get("cohorts")
    if isinstance(expected_cohorts, list):
        actual_cohorts = payload.get("cohorts")
        if not isinstance(actual_cohorts, list) or len(actual_cohorts) != len(expected_cohorts):
            drift.append("cohort_count")
        else:
            for index, (actual, wanted) in enumerate(
                zip(actual_cohorts, expected_cohorts, strict=False)
            ):
                if not isinstance(actual, dict) or not isinstance(wanted, dict):
                    drift.append(f"cohorts[{index}]")
                    continue
                for key in ("identifier", "delivery", "curriculum"):
                    if actual.get(key) != wanted.get(key):
                        drift.append(f"cohorts[{index}].{key}")
                actual_homework = actual.get("homework")
                wanted_homework = wanted.get("homework")
                if isinstance(wanted_homework, list) and (
                    not isinstance(actual_homework, list)
                    or len(actual_homework) != len(wanted_homework)
                ):
                    drift.append(f"cohorts[{index}].homework_count")
    return drift


if __name__ == "__main__":
    raise SystemExit(main())
