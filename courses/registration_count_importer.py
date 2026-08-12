"""Read-only adapter for course registration-count baselines.

Only safe campaign/cohort identity and aggregate timestamp/count facts cross this
module boundary.  The registered database locator and registration rows never do.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

ADAPTER_VERSION = "cmp-registration-count-sqlite-v1"
COUNT_POLICY_VERSION = "campaign-recorded-cohort-v1"
MAX_SOURCE_BYTES = 8 * 1024 * 1024 * 1024
MAX_CAMPAIGNS = 10_000
MAX_ROWS = 10_000_000
MAX_SQLITE_STEPS = 25_000_000

_REFERENCE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TABLES = (
    "courses_course",
    "courses_registrationcampaign",
    "courses_courseregistration",
)
_REQUIRED_COLUMNS = {
    "courses_course": frozenset({"id", "slug"}),
    "courses_registrationcampaign": frozenset({"id", "slug", "current_course_id"}),
    "courses_courseregistration": frozenset(
        {"id", "campaign_id", "course_id", "created_at", "email_normalized"}
    ),
}


class CourseCountSourceError(ValueError):
    """A bounded source failure that never embeds a locator or row value."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CountCandidate:
    campaign_slug: str
    cohort_slug: str
    baseline_count: int
    source_min_created_at: datetime | None
    source_max_created_at: datetime | None
    coverage_cutoff_at: datetime
    proposed_native_start_at: datetime
    aggregate_checksum: str


@dataclass(frozen=True, slots=True)
class DerivedCourseCounts:
    adapter_version: str
    schema_version: str
    count_policy_version: str
    whole_source_checksum: str
    source_byte_size: int
    schema_contract_checksum: str
    aggregate_manifest_checksum: str
    captured_at: datetime
    source_frozen_at: datetime
    campaign_total: int
    row_total: int
    candidates: tuple[CountCandidate, ...]


def source_reference_digest(reference: str) -> str:
    if not isinstance(reference, str) or _REFERENCE.fullmatch(reference) is None:
        raise CourseCountSourceError("source_reference_invalid")
    digest = hashlib.sha256(b"dtc-course-count-source-reference-v1\0")
    digest.update(reference.encode())
    return digest.hexdigest()


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    raise CourseCountSourceError("source_too_large")
                digest.update(chunk)
    except CourseCountSourceError:
        raise
    except OSError as error:
        raise CourseCountSourceError("source_unreadable") from error
    return digest.hexdigest(), size


def _registered_source(reference: str) -> Mapping[str, object]:
    source_reference_digest(reference)
    registry = getattr(settings, "COURSE_REGISTRATION_COUNT_SOURCES", {})
    if not isinstance(registry, Mapping):
        raise CourseCountSourceError("source_registry_invalid")
    value = registry.get(reference)
    if not isinstance(value, Mapping):
        raise CourseCountSourceError("source_reference_unavailable")
    return value


def _registered_text(entry: Mapping[str, object], key: str, *, maximum: int = 128) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CourseCountSourceError("source_registry_invalid")
    return value


def _registered_datetime(entry: Mapping[str, object], key: str) -> datetime:
    raw = _registered_text(entry, key, maximum=64)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as error:
        raise CourseCountSourceError("source_registry_invalid") from error
    if not timezone.is_aware(value):
        raise CourseCountSourceError("source_registry_invalid")
    return value


def _source_path(entry: Mapping[str, object]) -> Path:
    raw = _registered_text(entry, "path", maximum=4096)
    path = Path(raw)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CourseCountSourceError("source_unreadable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CourseCountSourceError("source_not_regular")
    if metadata.st_size <= 0 or metadata.st_size > MAX_SOURCE_BYTES:
        raise CourseCountSourceError("source_size_invalid")
    return path


def _open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024 * 1024)
        remaining = [MAX_SQLITE_STEPS]

        def bounded_progress() -> int:
            remaining[0] -= 1_000
            return int(remaining[0] <= 0)

        connection.set_progress_handler(bounded_progress, 1_000)
        return connection
    except sqlite3.Error as error:
        raise CourseCountSourceError("source_unreadable") from error


def _schema_rows(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for table in _SAFE_TABLES:
        table_rows = tuple(connection.execute(f'PRAGMA table_info("{table}")'))
        if not table_rows:
            raise CourseCountSourceError("schema_table_missing")
        columns = frozenset(str(row[1]) for row in table_rows)
        if not _REQUIRED_COLUMNS[table].issubset(columns):
            raise CourseCountSourceError("schema_column_missing")
        rows.extend(("column", table, *row[1:6]) for row in table_rows)
        foreign_keys = tuple(connection.execute(f'PRAGMA foreign_key_list("{table}")'))
        rows.extend(("foreign_key", table, *row[2:8]) for row in foreign_keys)
        index_rows = tuple(connection.execute(f'PRAGMA index_list("{table}")'))
        for index_row in index_rows:
            index_name = str(index_row[1])
            index_columns = tuple(
                str(row[2])
                for row in connection.execute(
                    f'PRAGMA index_info("{index_name.replace(chr(34), chr(34) * 2)}")'
                )
            )
            rows.append(("index", table, bool(index_row[2]), *index_columns))
    registration_foreign_keys = {
        (str(row[2]), str(row[3]), str(row[4]))
        for row in connection.execute('PRAGMA foreign_key_list("courses_courseregistration")')
    }
    required_foreign_keys = {
        ("courses_registrationcampaign", "campaign_id", "id"),
        ("courses_course", "course_id", "id"),
    }
    if not required_foreign_keys.issubset(registration_foreign_keys):
        raise CourseCountSourceError("schema_relation_invalid")
    unique_indexes = {
        tuple(
            str(column[2])
            for column in connection.execute(
                f'PRAGMA index_info("{str(index[1]).replace(chr(34), chr(34) * 2)}")'
            )
        )
        for index in connection.execute('PRAGMA index_list("courses_courseregistration")')
        if bool(index[2])
    }
    if ("campaign_id", "email_normalized") not in unique_indexes:
        raise CourseCountSourceError("schema_uniqueness_invalid")
    return tuple(sorted(rows, key=lambda row: tuple(str(item) for item in row)))


def _schema_checksum(connection: sqlite3.Connection) -> str:
    payload = json.dumps(
        _schema_rows(connection),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(b"dtc-course-count-schema-v1\0" + payload).hexdigest()


def schema_contract_checksum(path: Path) -> str:
    """Return the safe schema fingerprint used when registering a source."""

    connection = _open_read_only(path)
    try:
        return _schema_checksum(connection)
    except sqlite3.Error as error:
        raise CourseCountSourceError("schema_invalid") from error
    finally:
        connection.close()


def _aware_source_datetime(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise CourseCountSourceError("registration_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CourseCountSourceError("registration_timestamp_invalid") from error
    if not timezone.is_aware(parsed):
        raise CourseCountSourceError("registration_timestamp_naive")
    return parsed


def aggregate_checksum(
    *,
    campaign_slug: str,
    cohort_slug: str,
    count: int,
    minimum: datetime | None,
    maximum: datetime | None,
    cutoff: datetime,
) -> str:
    values = (
        campaign_slug,
        cohort_slug,
        str(count),
        minimum.isoformat() if minimum else "",
        maximum.isoformat() if maximum else "",
        cutoff.isoformat(),
    )
    digest = hashlib.sha256(b"dtc-course-registration-count-v1\0")
    for value in values:
        encoded = value.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _derive_candidates(
    connection: sqlite3.Connection,
    *,
    cutoff: datetime,
    native_start: datetime,
) -> tuple[tuple[CountCandidate, ...], int]:
    invalid_timestamp_total = int(
        connection.execute(
            "SELECT COUNT(*) FROM courses_courseregistration "
            "WHERE created_at IS NULL OR length(created_at) > 64 "
            "OR julianday(created_at) IS NULL"
        ).fetchone()[0]
    )
    if invalid_timestamp_total:
        raise CourseCountSourceError("registration_timestamp_invalid")
    unaware_timestamp_total = int(
        connection.execute(
            "SELECT COUNT(*) FROM courses_courseregistration "
            "WHERE NOT (lower(substr(created_at, -1, 1)) = 'z' OR ("
            "substr(created_at, -6, 1) IN ('+', '-') "
            "AND substr(created_at, -5, 2) GLOB '[0-9][0-9]' "
            "AND substr(created_at, -3, 1) = ':' "
            "AND substr(created_at, -2, 2) GLOB '[0-9][0-9]'))"
        ).fetchone()[0]
    )
    if unaware_timestamp_total:
        raise CourseCountSourceError("registration_timestamp_naive")
    rows_after_cutoff = int(
        connection.execute(
            "SELECT COUNT(*) FROM courses_courseregistration "
            "WHERE julianday(created_at) > julianday(?)",
            (cutoff.isoformat(),),
        ).fetchone()[0]
    )
    if rows_after_cutoff:
        raise CourseCountSourceError("registration_after_cutoff")
    duplicate_campaign_slugs = connection.execute(
        "SELECT COUNT(*) FROM ("
        "SELECT slug FROM courses_registrationcampaign GROUP BY slug HAVING COUNT(*) != 1"
        ")"
    ).fetchone()[0]
    duplicate_course_slugs = connection.execute(
        "SELECT COUNT(*) FROM (SELECT slug FROM courses_course GROUP BY slug HAVING COUNT(*) != 1)"
    ).fetchone()[0]
    if duplicate_campaign_slugs or duplicate_course_slugs:
        raise CourseCountSourceError("source_slug_not_unique")
    campaign_total = int(
        connection.execute("SELECT COUNT(*) FROM courses_registrationcampaign").fetchone()[0]
    )
    row_total = int(
        connection.execute("SELECT COUNT(*) FROM courses_courseregistration").fetchone()[0]
    )
    if campaign_total > MAX_CAMPAIGNS or row_total > MAX_ROWS:
        raise CourseCountSourceError("source_count_limit")
    rows = tuple(
        connection.execute(
            "SELECT campaign.slug, current_course.slug, registration.course_id, "
            "recorded_course.slug, COUNT(registration.id), "
            "MIN(registration.created_at), MAX(registration.created_at) "
            "FROM courses_registrationcampaign AS campaign "
            "LEFT JOIN courses_course AS current_course "
            "ON current_course.id = campaign.current_course_id "
            "LEFT JOIN courses_courseregistration AS registration "
            "ON registration.campaign_id = campaign.id "
            "LEFT JOIN courses_course AS recorded_course "
            "ON recorded_course.id = registration.course_id "
            "GROUP BY campaign.id, campaign.slug, current_course.slug, "
            "registration.course_id, recorded_course.slug "
            "ORDER BY campaign.slug, recorded_course.slug"
        )
    )
    grouped: dict[str, list[tuple[object, ...]]] = {}
    for row in rows:
        campaign_slug = row[0]
        if not isinstance(campaign_slug, str) or not campaign_slug:
            raise CourseCountSourceError("source_campaign_identity_invalid")
        grouped.setdefault(campaign_slug, []).append(row)
    candidates: list[CountCandidate] = []
    counted_rows = 0
    for campaign_slug, campaign_rows in grouped.items():
        nonempty_rows = [row for row in campaign_rows if int(row[4]) > 0]
        if len(nonempty_rows) > 1:
            raise CourseCountSourceError("campaign_spans_cohorts")
        row = nonempty_rows[0] if nonempty_rows else campaign_rows[0]
        current_cohort_slug = row[1]
        recorded_course_id = row[2]
        recorded_cohort_slug = row[3]
        count = int(row[4])
        if not isinstance(current_cohort_slug, str) or not current_cohort_slug:
            raise CourseCountSourceError("source_current_cohort_missing")
        if count:
            if recorded_course_id is None or not isinstance(recorded_cohort_slug, str):
                raise CourseCountSourceError("registration_cohort_missing")
            if recorded_cohort_slug != current_cohort_slug:
                raise CourseCountSourceError("campaign_current_cohort_changed")
            minimum = _aware_source_datetime(row[5])
            maximum = _aware_source_datetime(row[6])
            if maximum > cutoff or maximum >= native_start:
                raise CourseCountSourceError("registration_after_cutoff")
        else:
            recorded_cohort_slug = current_cohort_slug
            minimum = None
            maximum = None
        counted_rows += count
        candidates.append(
            CountCandidate(
                campaign_slug=campaign_slug,
                cohort_slug=recorded_cohort_slug,
                baseline_count=count,
                source_min_created_at=minimum,
                source_max_created_at=maximum,
                coverage_cutoff_at=cutoff,
                proposed_native_start_at=native_start,
                aggregate_checksum=aggregate_checksum(
                    campaign_slug=campaign_slug,
                    cohort_slug=recorded_cohort_slug,
                    count=count,
                    minimum=minimum,
                    maximum=maximum,
                    cutoff=cutoff,
                ),
            )
        )
    if len(candidates) != campaign_total or counted_rows != row_total:
        raise CourseCountSourceError("source_manifest_incomplete")
    return tuple(candidates), row_total


def _manifest_checksum(candidates: tuple[CountCandidate, ...]) -> str:
    payload = [
        {
            "aggregate_checksum": item.aggregate_checksum,
            "baseline_count": item.baseline_count,
            "campaign_slug": item.campaign_slug,
            "cohort_slug": item.cohort_slug,
        }
        for item in sorted(candidates, key=lambda value: (value.campaign_slug, value.cohort_slug))
    ]
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(b"dtc-course-count-manifest-v1\0" + encoded).hexdigest()


def derive_registered_source(reference: str) -> DerivedCourseCounts:
    entry = _registered_source(reference)
    if _registered_text(entry, "adapter") != ADAPTER_VERSION:
        raise CourseCountSourceError("adapter_version_invalid")
    expected_checksum = _registered_text(entry, "sha256", maximum=64)
    expected_schema_checksum = _registered_text(entry, "schema_contract_checksum", maximum=64)
    if not _SHA256.fullmatch(expected_checksum) or not _SHA256.fullmatch(expected_schema_checksum):
        raise CourseCountSourceError("source_registry_invalid")
    expected_size = entry.get("byte_size")
    if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size <= 0:
        raise CourseCountSourceError("source_registry_invalid")
    schema_version = _registered_text(entry, "schema_version", maximum=64)
    captured_at = _registered_datetime(entry, "captured_at")
    frozen_at = _registered_datetime(entry, "source_frozen_at")
    cutoff = _registered_datetime(entry, "coverage_cutoff_at")
    native_start = _registered_datetime(entry, "native_start_at")
    if frozen_at != cutoff or frozen_at > captured_at or cutoff >= native_start:
        raise CourseCountSourceError("cutover_evidence_invalid")
    path = _source_path(entry)
    checksum, size = _hash_file(path)
    if checksum != expected_checksum or size != expected_size:
        raise CourseCountSourceError("source_identity_changed")
    connection = _open_read_only(path)
    try:
        actual_schema_checksum = _schema_checksum(connection)
        if actual_schema_checksum != expected_schema_checksum:
            raise CourseCountSourceError("schema_contract_changed")
        candidates, row_total = _derive_candidates(
            connection,
            cutoff=cutoff,
            native_start=native_start,
        )
    except CourseCountSourceError:
        raise
    except sqlite3.Error as error:
        raise CourseCountSourceError("source_query_failed") from error
    finally:
        connection.close()
    return DerivedCourseCounts(
        adapter_version=ADAPTER_VERSION,
        schema_version=schema_version,
        count_policy_version=COUNT_POLICY_VERSION,
        whole_source_checksum=checksum,
        source_byte_size=size,
        schema_contract_checksum=actual_schema_checksum,
        aggregate_manifest_checksum=_manifest_checksum(candidates),
        captured_at=captured_at,
        source_frozen_at=frozen_at,
        campaign_total=len(candidates),
        row_total=row_total,
        candidates=candidates,
    )


def registered_source_matches(run: object, *, revision: object | None = None) -> bool:
    """Check only code-owned registry identity for the public read path."""

    registry = getattr(settings, "COURSE_REGISTRATION_COUNT_SOURCES", {})
    if not isinstance(registry, Mapping):
        return False
    try:
        matches = [
            value
            for reference, value in registry.items()
            if isinstance(reference, str)
            and isinstance(value, Mapping)
            and source_reference_digest(reference) == getattr(run, "source_reference_digest", "")
        ]
    except CourseCountSourceError:
        return False
    if len(matches) != 1:
        return False
    entry = matches[0]
    try:
        captured_at = _registered_datetime(entry, "captured_at")
        frozen_at = _registered_datetime(entry, "source_frozen_at")
        cutoff_at = _registered_datetime(entry, "coverage_cutoff_at")
        native_start_at = _registered_datetime(entry, "native_start_at")
    except CourseCountSourceError:
        return False
    matches_run = (
        entry.get("adapter") == getattr(run, "adapter_version", None)
        and getattr(run, "count_policy_version", None) == COUNT_POLICY_VERSION
        and entry.get("sha256") == getattr(run, "whole_source_checksum", None)
        and entry.get("byte_size") == getattr(run, "source_byte_size", None)
        and entry.get("schema_version") == getattr(run, "schema_version", None)
        and entry.get("schema_contract_checksum") == getattr(run, "schema_contract_checksum", None)
        and captured_at == getattr(run, "captured_at", None)
        and frozen_at == getattr(run, "source_frozen_at", None)
        and frozen_at == cutoff_at
        and cutoff_at < native_start_at
    )
    if revision is None:
        return matches_run
    return (
        matches_run
        and cutoff_at == getattr(revision, "coverage_cutoff_at", None)
        and native_start_at == getattr(revision, "proposed_native_start_at", None)
    )
