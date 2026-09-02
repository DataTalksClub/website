"""Import sanitized CMP public course content into a local production-prep database.

``review_import`` is the sanitizing reader: it copies only the versioned allowlist
and leaves learner tables empty.  This service applies that dataset to the
already-migrated local SQLite database that ``scripts/prepare_local_data.py``
builds, which ``make review-data`` never writes.

It also applies two local-only cuts the review snapshot itself does not:

* drop the upstream fixture rows ``fake-course`` and ``fake-course-2``;
* rewrite ``hwN`` homework slugs on the three 2026 module cohorts to
  ``homework-0N`` so ``prepare_local_course_modules`` can adopt them by slug
  without replacing their questions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from django.db import connections

from courses.services.local_course_modules import TARGET_COHORTS
from courses.services.local_course_seed import LocalCourseSeedError, assert_local_database
from review_import.manifest import ALLOWLIST, COPY_ORDER
from review_import.workflow import (
    AllowedDataset,
    ImportFailure,
    _assert_source_integrity,
    _assert_sqlite_integrity,
    _column_index,
    _decode_json,
    _insert_dataset,
    _logical_checksum,
    _read_allowed_dataset,
    _readonly_connection,
    _relationship_evidence,
    _validate_source_schema,
    _writable_connection,
)

FORBIDDEN_COURSE_SLUGS = frozenset({"fake-course", "fake-course-2"})
MODULE_HOMEWORK_SLUG = re.compile(r"^hw(\d+)$")
MODULE_COHORT_SLUGS = frozenset(
    f"{family}-{year}" for family, year in TARGET_COHORTS.items()
)


class LocalCmpContentImportError(RuntimeError):
    """A fail-closed refusal that never renders source values."""


@dataclass(frozen=True, slots=True)
class LocalCmpContentImportResult:
    table_counts: Mapping[str, int]
    relationship_counts: Mapping[str, int]
    skipped_empty_unknown_tables: tuple[str, ...]
    excluded_fixture_courses: tuple[str, ...]
    remapped_homework_slugs: int
    logical_checksum: str

    def summary(self) -> dict[str, Any]:
        return {
            "imported": True,
            "table_counts": dict(self.table_counts),
            "relationship_counts": dict(self.relationship_counts),
            "skipped_empty_unknown_tables": list(self.skipped_empty_unknown_tables),
            "excluded_fixture_courses": list(self.excluded_fixture_courses),
            "remapped_homework_slugs": self.remapped_homework_slugs,
            "logical_checksum": self.logical_checksum,
        }


def _refuse(category: str) -> NoReturn:
    raise LocalCmpContentImportError(category)


def _rebuild_dataset(rows: dict[str, list[tuple[Any, ...]]]) -> AllowedDataset:
    try:
        relationships = _relationship_evidence(rows)
        checksum = _logical_checksum(rows)
    except ImportFailure as error:
        raise LocalCmpContentImportError(error.category) from None
    return AllowedDataset(
        rows=rows,
        counts={table: len(rows[table]) for table in COPY_ORDER},
        relationships=relationships,
        logical_checksum=checksum,
    )


def _replace_column(
    row: tuple[Any, ...],
    table: str,
    column: str,
    value: Any,
) -> tuple[Any, ...]:
    values = list(row)
    values[_column_index(table, column)] = value
    return tuple(values)


def exclude_fixture_courses(dataset: AllowedDataset) -> tuple[AllowedDataset, tuple[str, ...]]:
    """Drop upstream test courses and every allowlisted row that points at them."""

    slug_index = _column_index("courses_course", "slug")
    course_id_index = _column_index("courses_course", "id")
    excluded = tuple(
        sorted(
            str(row[slug_index])
            for row in dataset.rows["courses_course"]
            if row[slug_index] in FORBIDDEN_COURSE_SLUGS
        )
    )
    if not excluded:
        return dataset, ()

    excluded_slugs = set(excluded)
    kept_course_ids = {
        row[course_id_index]
        for row in dataset.rows["courses_course"]
        if row[slug_index] not in excluded_slugs
    }
    rows: dict[str, list[tuple[Any, ...]]] = {}
    for table in COPY_ORDER:
        table_rows = dataset.rows[table]
        if table == "courses_course":
            rows[table] = [
                row for row in table_rows if row[course_id_index] in kept_course_ids
            ]
            continue
        if "course_id" in ALLOWLIST[table]:
            index = _column_index(table, "course_id")
            rows[table] = [row for row in table_rows if row[index] in kept_course_ids]
            continue
        if table == "courses_registrationcampaign":
            index = _column_index(table, "current_course_id")
            rows[table] = [
                row for row in table_rows if row[index] is None or row[index] in kept_course_ids
            ]
            continue
        rows[table] = list(table_rows)

    kept_homework_ids = {
        row[_column_index("courses_homework", "id")] for row in rows["courses_homework"]
    }
    kept_project_ids = {
        row[_column_index("courses_project", "id")] for row in rows["courses_project"]
    }
    rows["courses_question"] = [
        row
        for row in rows["courses_question"]
        if row[_column_index("courses_question", "homework_id")] in kept_homework_ids
    ]
    rows["courses_homeworkstatistics"] = [
        row
        for row in rows["courses_homeworkstatistics"]
        if row[_column_index("courses_homeworkstatistics", "homework_id")] in kept_homework_ids
    ]
    rows["courses_projectstatistics"] = [
        row
        for row in rows["courses_projectstatistics"]
        if row[_column_index("courses_projectstatistics", "project_id")] in kept_project_ids
    ]

    stats_index = _column_index("courses_wrappedstatistics", "course_stats")
    rewritten_stats: list[tuple[Any, ...]] = []
    for row in rows["courses_wrappedstatistics"]:
        stats = _decode_json(row[stats_index])
        if not isinstance(stats, list):
            rewritten_stats.append(row)
            continue
        filtered = [
            item
            for item in stats
            if not (isinstance(item, dict) and item.get("slug") in excluded_slugs)
        ]
        if filtered == stats:
            rewritten_stats.append(row)
            continue
        rewritten_stats.append(
            _replace_column(
                row,
                "courses_wrappedstatistics",
                "course_stats",
                json.dumps(filtered, ensure_ascii=False, separators=(",", ":")),
            )
        )
    rows["courses_wrappedstatistics"] = rewritten_stats
    return _rebuild_dataset(rows), excluded


def align_module_homework_slugs(dataset: AllowedDataset) -> tuple[AllowedDataset, int]:
    """Map CMP ``hwN`` slugs onto the module-curriculum ``homework-0N`` slugs."""

    course_id_index = _column_index("courses_course", "id")
    course_slug_index = _column_index("courses_course", "slug")
    homework_course_index = _column_index("courses_homework", "course_id")
    homework_slug_index = _column_index("courses_homework", "slug")
    module_course_ids = {
        row[course_id_index]
        for row in dataset.rows["courses_course"]
        if row[course_slug_index] in MODULE_COHORT_SLUGS
    }
    if not module_course_ids:
        return dataset, 0

    remapped = 0
    rewritten: list[tuple[Any, ...]] = []
    occupied = {
        (row[homework_course_index], row[homework_slug_index])
        for row in dataset.rows["courses_homework"]
    }
    for row in dataset.rows["courses_homework"]:
        slug = str(row[homework_slug_index])
        match = MODULE_HOMEWORK_SLUG.fullmatch(slug)
        if row[homework_course_index] not in module_course_ids or match is None:
            rewritten.append(row)
            continue
        aligned = f"homework-{int(match.group(1)):02d}"
        identity = (row[homework_course_index], aligned)
        if aligned == slug or identity in occupied:
            rewritten.append(row)
            continue
        occupied.discard((row[homework_course_index], slug))
        occupied.add(identity)
        rewritten.append(_replace_column(row, "courses_homework", "slug", aligned))
        remapped += 1
    if not remapped:
        return dataset, 0
    rows = dict(dataset.rows)
    rows["courses_homework"] = rewritten
    return _rebuild_dataset(rows), remapped


def _copy_source(source_db: Path) -> tuple[Path, str]:
    try:
        resolved = source_db.expanduser().resolve(strict=True)
    except OSError:
        _refuse("source-unavailable")
    if not resolved.is_file() or resolved.is_symlink():
        _refuse("source-not-regular-file")
    work = Path(tempfile.mkdtemp(prefix="dtc-cmp-import-", dir="/tmp"))
    os.chmod(work, 0o700)
    copied = work / "source.sqlite3"
    shutil.copy2(resolved, copied)
    os.chmod(copied, 0o600)
    return copied, str(work)


def import_local_cmp_content(source_db: Path, target_db: Path) -> LocalCmpContentImportResult:
    """Copy allowlisted CMP content into an empty local course catalog."""

    try:
        assert_local_database()
    except LocalCourseSeedError as error:
        raise LocalCmpContentImportError(str(error)) from None

    copied: Path | None = None
    work_dir: str | None = None
    try:
        copied, work_dir = _copy_source(Path(source_db))
        connections.close_all()
        target_path = Path(target_db).resolve(strict=True)
        with _readonly_connection(copied) as source, _writable_connection(target_path) as target:
            _assert_source_integrity(source)
            try:
                skipped = _validate_source_schema(source, target)
                dataset = _read_allowed_dataset(source)
            except ImportFailure as error:
                raise LocalCmpContentImportError(error.category) from None
            existing = int(target.execute("SELECT COUNT(*) FROM courses_course").fetchone()[0])
            if existing:
                _refuse("target-courses-not-empty")
            dataset, excluded = exclude_fixture_courses(dataset)
            dataset, remapped = align_module_homework_slugs(dataset)
            _insert_dataset(target, dataset)
            _assert_sqlite_integrity(target)
        return LocalCmpContentImportResult(
            table_counts=dataset.counts,
            relationship_counts=dataset.relationships,
            skipped_empty_unknown_tables=skipped,
            excluded_fixture_courses=excluded,
            remapped_homework_slugs=remapped,
            logical_checksum=dataset.logical_checksum,
        )
    except LocalCmpContentImportError:
        raise
    except ImportFailure as error:
        raise LocalCmpContentImportError(error.category) from None
    except Exception:
        _refuse("import-failed")
    finally:
        connections.close_all()
        if copied is not None:
            copied.unlink(missing_ok=True)
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)


__all__ = [
    "FORBIDDEN_COURSE_SLUGS",
    "LocalCmpContentImportError",
    "LocalCmpContentImportResult",
    "align_module_homework_slugs",
    "exclude_fixture_courses",
    "import_local_cmp_content",
]
