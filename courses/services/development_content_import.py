"""Fail-closed import of the approved, sanitized CMP public-content artifact."""

from __future__ import annotations

import hashlib
import json
import math
import stat
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.color import no_style
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.template.loader import get_template
from django.urls import resolve

from core.bootstrap import RuntimeEnvironment
from core.idempotency import execute_idempotent
from courses.models import (
    Course,
    Homework,
    HomeworkStatistics,
    Project,
    ProjectStatistics,
    Question,
    RegistrationCampaign,
    ReviewCriteria,
    WrappedStatistics,
)
from review_import.manifest import (
    ALLOWLIST,
    ALLOWLIST_SCHEMA_VERSION,
    COPY_ORDER,
)
from review_import.workflow import (
    AllowedDataset,
    ImportFailure,
    _assert_source_integrity,
    _assert_source_unchanged,
    _columns,
    _decode_json,
    _logical_checksum,
    _read_allowed_dataset,
    _readonly_connection,
    _relationship_evidence,
    _sensitive_zero_counts,
    _table_names,
    fingerprint,
)

APPROVED_SOURCE_SHA256 = "ac55cb0cb10cc0924dd8c9a9e63fe9b09ae809cac8aac14d6da2ce46c3586d04"
APPROVED_SOURCE_SIZE = 1_740_800
APPROVED_LOGICAL_CHECKSUM = "eb5fd5f8e7d27aee107d925ea7e17c60274a4695f4fd7783e5c67f79a59e0a20"
APPROVED_SEMANTIC_CHECKSUM = "2687838817b2918a7691206c9bc6b79082f2e1c8356f099c699aab1395e73426"
APPROVED_SCHEMA_CHECKSUM = "06a23857b9d8a4265c520ad67a6285fc0ed604007f6280a02ca7fb2d6a35c96e"
APPROVED_COUNTS = {
    "courses_course": 20,
    "courses_registrationcampaign": 2,
    "courses_homework": 119,
    "courses_question": 604,
    "courses_homeworkstatistics": 109,
    "courses_project": 48,
    "courses_reviewcriteria": 169,
    "courses_projectstatistics": 41,
    "courses_wrappedstatistics": 1,
}
APPROVED_RELATIONSHIPS = {
    "campaign_course": 2,
    "homework_course": 119,
    "question_homework": 604,
    "homework_statistics": 109,
    "project_course": 48,
    "criteria_course": 169,
    "project_statistics": 41,
    "wrapped_course_stats": 8,
}
IDEMPOTENCY_SCOPE = "courses.development-content-import"
RECEIPT_TABLE = "core_idempotencyrecord"
EXPECTED_LIST_TEMPLATE_SHA256 = "26e391ffdd2c90b89a668c41118f4a8e43efd2b5dde015097f893aee707984ef"
EXPECTED_DETAIL_TEMPLATE_SHA256 = "d8a7b0770392c3ead0b103e28f8327bddba15eca24727f5f318c7c99ef0eff67"
DATE_COLUMNS = frozenset({"start_date", "end_date"})
DATETIME_COLUMNS = frozenset(
    {
        "created_at",
        "updated_at",
        "due_date",
        "last_calculated",
        "submission_due_date",
        "peer_review_due_date",
        "calculated_at",
    }
)
BOOLEAN_COLUMNS = frozenset(
    {
        "first_homework_scored",
        "finished",
        "visible",
        "homework_problems_comments_field",
        "is_active",
        "homework_url_field",
        "time_spent_lectures_field",
        "time_spent_homework_field",
        "faq_contribution_field",
        "time_spent_project_field",
        "problems_comments_field",
        "time_spent_evaluation_field",
        "is_visible",
    }
)
JSON_COLUMNS = frozenset({"options", "course_stats", "leaderboard"})
IMPORTED_MODELS = (
    Course,
    RegistrationCampaign,
    Homework,
    Question,
    HomeworkStatistics,
    Project,
    ReviewCriteria,
    ProjectStatistics,
    WrappedStatistics,
)


class DevelopmentContentImportError(RuntimeError):
    """Safe operator error which never renders artifact paths or row values."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"development course content import refused: {category}")


@dataclass(frozen=True, slots=True)
class ArtifactContract:
    source_sha256: str
    source_size: int
    logical_checksum: str
    semantic_checksum: str
    counts: dict[str, int]
    relationships: dict[str, int]
    schema_version: str = ALLOWLIST_SCHEMA_VERSION
    schema_checksum: str = APPROVED_SCHEMA_CHECKSUM


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    source_sha256: str
    logical_checksum: str
    counts: dict[str, int]
    relationships: dict[str, int]
    imported: bool
    replayed: bool
    sensitive_tables_preserved: bool


APPROVED_ARTIFACT = ArtifactContract(
    source_sha256=APPROVED_SOURCE_SHA256,
    source_size=APPROVED_SOURCE_SIZE,
    logical_checksum=APPROVED_LOGICAL_CHECKSUM,
    semantic_checksum=APPROVED_SEMANTIC_CHECKSUM,
    counts=APPROVED_COUNTS,
    relationships=APPROVED_RELATIONSHIPS,
)


def schema_contract_checksum() -> str:
    payload = json.dumps(
        ALLOWLIST,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _assert_environment(*, allow_test_environment: bool) -> None:
    allowed = {RuntimeEnvironment.DEVELOPMENT}
    if allow_test_environment:
        allowed.add(RuntimeEnvironment.TEST)
        allowed.add(RuntimeEnvironment.LOCAL)
    if settings.RUNTIME_ENVIRONMENT not in allowed:
        raise DevelopmentContentImportError("environment-not-development")


def _assert_private_regular_artifact(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError:
        raise DevelopmentContentImportError("artifact-unavailable") from None
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise DevelopmentContentImportError("artifact-not-regular-file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise DevelopmentContentImportError("artifact-permissions-not-private")
    return path.resolve(strict=True)


def _target_schema() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        for table in tables:
            try:
                description = connection.introspection.get_table_description(cursor, table)
                result[table] = {field.name for field in description}
            except IndexError:
                # Preserve actual-table coverage if a backend's DDL parser rejects legal DDL.
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)} WHERE 1 = 0")
                if cursor.description is None:
                    raise DevelopmentContentImportError("target-schema-unavailable") from None
                result[table] = {str(field[0]) for field in cursor.description}
    return result


def _assert_target_migrations_current() -> None:
    executor = MigrationExecutor(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise DevelopmentContentImportError("target-migrations-pending")


def _assert_source_schema(source: Any) -> None:
    if ALLOWLIST_SCHEMA_VERSION != APPROVED_ARTIFACT.schema_version:
        raise DevelopmentContentImportError("schema-version-drift")
    if schema_contract_checksum() != APPROVED_ARTIFACT.schema_checksum:
        raise DevelopmentContentImportError("schema-contract-drift")

    source_tables = _table_names(source)
    target_schema = _target_schema()
    unknown_tables = source_tables - set(target_schema)
    if unknown_tables:
        raise DevelopmentContentImportError("artifact-unknown-table")
    for table in source_tables:
        if set(_columns(source, table)) - target_schema[table]:
            raise DevelopmentContentImportError("artifact-unknown-column")
    for table, allowed_columns in ALLOWLIST.items():
        if table not in source_tables:
            raise DevelopmentContentImportError("artifact-missing-table")
        if set(allowed_columns) - set(_columns(source, table)):
            raise DevelopmentContentImportError("artifact-missing-column")


def _assert_empty_wrapped_leaderboard(source: Any) -> None:
    columns = _columns(source, "courses_wrappedstatistics")
    if "leaderboard" not in columns:
        raise DevelopmentContentImportError("artifact-missing-leaderboard-boundary")
    for row in source.execute(
        "SELECT leaderboard FROM courses_wrappedstatistics ORDER BY id"
    ).fetchall():
        try:
            value = _decode_json(row[0])
        except (TypeError, ValueError, json.JSONDecodeError):
            raise DevelopmentContentImportError("artifact-invalid-leaderboard-boundary") from None
        if value != []:
            raise DevelopmentContentImportError("artifact-leaderboard-not-empty")


def _load_artifact(path: Path, contract: ArtifactContract) -> AllowedDataset:
    resolved = _assert_private_regular_artifact(path)
    before = fingerprint(resolved)
    if before.sha256 != contract.source_sha256 or before.size != contract.source_size:
        raise DevelopmentContentImportError("artifact-fingerprint-mismatch")
    try:
        with _readonly_connection(resolved) as source:
            _assert_source_integrity(source)
            _assert_source_schema(source)
            dataset = _read_allowed_dataset(source)
            _assert_empty_wrapped_leaderboard(source)
            sensitive_counts = _sensitive_zero_counts(source)
            if any(sensitive_counts.values()):
                raise DevelopmentContentImportError("artifact-sensitive-table-not-empty")
    except DevelopmentContentImportError:
        raise
    except ImportFailure as error:
        raise DevelopmentContentImportError(f"artifact-{error.category}") from None
    except Exception:
        raise DevelopmentContentImportError("artifact-read-failed") from None
    try:
        _assert_source_unchanged(resolved, before)
    except ImportFailure:
        raise DevelopmentContentImportError("artifact-changed-during-read") from None
    if (
        dataset.logical_checksum != contract.logical_checksum
        or semantic_dataset_checksum(dataset) != contract.semantic_checksum
        or dataset.counts != contract.counts
        or dataset.relationships != contract.relationships
    ):
        raise DevelopmentContentImportError("artifact-content-contract-mismatch")
    return dataset


def _normalized_database_value(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in BOOLEAN_COLUMNS:
        return int(value)
    if isinstance(value, datetime):
        rendered = value.isoformat(sep=" ")
        return rendered.removesuffix("+00:00") + "+00" if rendered.endswith("+00:00") else rendered
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _read_target_dataset() -> AllowedDataset:
    rows_by_table: dict[str, list[tuple[Any, ...]]] = {}
    counts: dict[str, int] = {}
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        for table in COPY_ORDER:
            columns = ALLOWLIST[table]
            cursor.execute(
                f"SELECT {', '.join(quote(column) for column in columns)} "
                f"FROM {quote(table)} ORDER BY {quote('id')}"
            )
            rows = [
                tuple(
                    _normalized_database_value(column, value)
                    for column, value in zip(columns, raw_row, strict=True)
                )
                for raw_row in cursor.fetchall()
            ]
            rows_by_table[table] = rows
            counts[table] = len(rows)
    try:
        relationships = _relationship_evidence(rows_by_table)
        checksum = _logical_checksum(rows_by_table)
    except ImportFailure as error:
        raise DevelopmentContentImportError(f"target-{error.category}") from None
    return AllowedDataset(rows_by_table, counts, relationships, checksum)


def _assert_target_wrapped_leaderboard_empty() -> None:
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {quote('leaderboard')} FROM {quote('courses_wrappedstatistics')} "
            f"ORDER BY {quote('id')}"
        )
        for (value,) in cursor.fetchall():
            if _decode_json(value) != []:
                raise DevelopmentContentImportError("target-leaderboard-not-empty")


def _matches(left: AllowedDataset, right: AllowedDataset) -> bool:
    if left.counts != right.counts or left.relationships != right.relationships:
        return False
    for table in COPY_ORDER:
        columns = ALLOWLIST[table]
        if len(left.rows[table]) != len(right.rows[table]):
            return False
        for left_row, right_row in zip(left.rows[table], right.rows[table], strict=True):
            left_values = tuple(
                _semantic_value(column, value)
                for column, value in zip(columns, left_row, strict=True)
            )
            right_values = tuple(
                _semantic_value(column, value)
                for column, value in zip(columns, right_row, strict=True)
            )
            if left_values != right_values:
                return False
    return True


def _target_is_empty(dataset: AllowedDataset) -> bool:
    return not any(dataset.counts.values())


def _insert_value(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in JSON_COLUMNS:
        return connection.ops.adapt_json_value(_decode_json(value), None)
    if column in BOOLEAN_COLUMNS:
        return bool(value)
    if column in DATE_COLUMNS and isinstance(value, str):
        return date.fromisoformat(value)
    if column in DATETIME_COLUMNS and isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise DevelopmentContentImportError("artifact-naive-datetime")
        return parsed
    return value


def _insert_dataset(dataset: AllowedDataset) -> None:
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        for table in COPY_ORDER:
            columns = ALLOWLIST[table]
            inserted_columns = columns
            rows = dataset.rows[table]
            if table == "courses_wrappedstatistics":
                inserted_columns = (*columns, "leaderboard")
                rows = [(*row, []) for row in rows]
            placeholders = ", ".join(["%s"] * len(inserted_columns))
            cursor.executemany(
                f"INSERT INTO {quote(table)} "
                f"({', '.join(quote(column) for column in inserted_columns)}) "
                f"VALUES ({placeholders})",
                [
                    tuple(
                        _insert_value(column, value)
                        for column, value in zip(inserted_columns, row, strict=True)
                    )
                    for row in rows
                ],
            )


def _reset_imported_sequences() -> None:
    statements = connection.ops.sequence_reset_sql(no_style(), IMPORTED_MODELS)
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def _safe_value_digest(value: Any) -> bytes:
    if value is None:
        payload = b"null"
    elif isinstance(value, bytes):
        payload = b"bytes:" + hashlib.sha256(value).hexdigest().encode()
    elif isinstance(value, (dict, list)):
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    elif isinstance(value, float) and not math.isfinite(value):
        payload = repr(value).encode()
    else:
        payload = f"{type(value).__name__}:{value!s}".encode()
    return hashlib.sha256(payload).digest()


def _protected_target_evidence() -> dict[str, tuple[int, str]]:
    schema = _target_schema()
    quote = connection.ops.quote_name
    evidence: dict[str, tuple[int, str]] = {}
    with connection.cursor() as cursor:
        for table in sorted(schema):
            if table == RECEIPT_TABLE or table in ALLOWLIST:
                continue
            columns = sorted(schema[table])
            cursor.execute(
                f"SELECT {', '.join(quote(column) for column in columns)} FROM {quote(table)}"
            )
            row_digests = []
            for row in cursor.fetchall():
                digest = hashlib.sha256()
                for value in row:
                    digest.update(_safe_value_digest(value))
                row_digests.append(digest.digest())
            aggregate = hashlib.sha256()
            for row_digest in sorted(row_digests):
                aggregate.update(row_digest)
            evidence[table] = (len(row_digests), aggregate.hexdigest())
    return evidence


def _assert_course_activity_empty() -> None:
    schema = _target_schema()
    quote = connection.ops.quote_name
    protected_course_tables = sorted(
        table for table in schema if table.startswith("courses_") and table not in ALLOWLIST
    )
    with connection.cursor() as cursor:
        for table in protected_course_tables:
            cursor.execute(f"SELECT COUNT(*) FROM {quote(table)}")
            if int(cursor.fetchone()[0]):
                raise DevelopmentContentImportError("target-course-activity-not-empty")


def _semantic_value(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in BOOLEAN_COLUMNS:
        return bool(value)
    if column in JSON_COLUMNS:
        return _decode_json(value)
    if column in DATE_COLUMNS:
        return date.fromisoformat(value) if isinstance(value, str) else value
    if column in DATETIME_COLUMNS:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
        if not isinstance(parsed, datetime) or parsed.tzinfo is None:
            raise DevelopmentContentImportError("dataset-naive-datetime")
        return parsed
    if isinstance(value, Decimal):
        return float(value)
    return value


def semantic_dataset_checksum(dataset: AllowedDataset) -> str:
    digest = hashlib.sha256()
    for table in COPY_ORDER:
        columns = ALLOWLIST[table]
        digest.update(table.encode())
        digest.update(b"\x00")
        for row in dataset.rows[table]:
            values = []
            for column, value in zip(columns, row, strict=True):
                semantic = _semantic_value(column, value)
                if isinstance(semantic, (date, datetime)):
                    semantic = semantic.isoformat()
                values.append(semantic)
            payload = json.dumps(
                values,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def _assert_exact_target(dataset: AllowedDataset) -> None:
    actual = _read_target_dataset()
    if not _matches(actual, dataset):
        raise DevelopmentContentImportError("target-content-drift")
    _assert_target_wrapped_leaderboard_empty()


def import_development_course_content(
    artifact: Path,
    *,
    contract: ArtifactContract = APPROVED_ARTIFACT,
    allow_test_environment: bool = False,
) -> ImportOutcome:
    """Atomically import once, or prove that an exact prior import is unchanged."""

    _assert_environment(allow_test_environment=allow_test_environment)
    if contract.schema_version != ALLOWLIST_SCHEMA_VERSION:
        raise DevelopmentContentImportError("schema-version-mismatch")
    if contract.schema_checksum != schema_contract_checksum():
        raise DevelopmentContentImportError("schema-checksum-mismatch")
    _assert_target_migrations_current()
    source = _load_artifact(Path(artifact), contract)

    def command() -> dict[str, Any]:
        protected_before = _protected_target_evidence()
        target_before = _read_target_dataset()
        if _target_is_empty(target_before):
            _assert_course_activity_empty()
            _insert_dataset(source)
            connection.check_constraints(table_names=COPY_ORDER)
            imported = True
        elif _matches(target_before, source):
            imported = False
        else:
            raise DevelopmentContentImportError("target-not-empty-or-exact")
        _assert_exact_target(source)
        if _protected_target_evidence() != protected_before:
            raise DevelopmentContentImportError("protected-target-drift")
        if imported:
            _reset_imported_sequences()
        return {
            "counts": source.counts,
            "relationships": source.relationships,
            "logical_checksum": source.logical_checksum,
            "source_sha256": contract.source_sha256,
            "imported": imported,
            "sensitive_tables_preserved": True,
        }

    result = execute_idempotent(
        scope=IDEMPOTENCY_SCOPE,
        key=contract.source_sha256,
        request={
            "source_sha256": contract.source_sha256,
            "source_size": contract.source_size,
            "logical_checksum": contract.logical_checksum,
            "schema_version": contract.schema_version,
            "schema_checksum": contract.schema_checksum,
            "semantic_checksum": contract.semantic_checksum,
        },
        command=command,
    )
    _assert_exact_target(source)
    value = result.value
    return ImportOutcome(
        source_sha256=str(value["source_sha256"]),
        logical_checksum=str(value["logical_checksum"]),
        counts={key: int(count) for key, count in dict(value["counts"]).items()},
        relationships={key: int(count) for key, count in dict(value["relationships"]).items()},
        imported=bool(value["imported"]) and not result.replayed,
        replayed=result.replayed,
        sensitive_tables_preserved=bool(value["sensitive_tables_preserved"]),
    )


def _repo_relative_template_origin(template_name: str) -> tuple[str, str]:
    origin = get_template(template_name).origin
    if origin is None or not origin.name:
        raise DevelopmentContentImportError("template-origin-unavailable")
    path = Path(origin.name).resolve(strict=True)
    try:
        relative = path.relative_to(Path(settings.BASE_DIR).resolve()).as_posix()
    except ValueError:
        raise DevelopmentContentImportError("template-origin-outside-repository") from None
    return relative, hashlib.sha256(path.read_bytes()).hexdigest()


def development_course_content_evidence(
    *,
    representative_slug: str = "de-zoomcamp-2026",
    contract: ArtifactContract = APPROVED_ARTIFACT,
    allow_test_environment: bool = False,
) -> dict[str, Any]:
    """Return only public-safe, read-only reconciliation evidence."""

    _assert_environment(allow_test_environment=allow_test_environment)
    _assert_target_migrations_current()
    target = _read_target_dataset()
    if (
        target.counts != contract.counts
        or target.relationships != contract.relationships
        or semantic_dataset_checksum(target) != contract.semantic_checksum
    ):
        raise DevelopmentContentImportError("target-content-drift")
    _assert_target_wrapped_leaderboard_empty()
    list_match = resolve("/courses")
    detail_match = resolve(f"/courses/{representative_slug}")
    if not Course.objects.filter(slug=representative_slug).exists():
        raise DevelopmentContentImportError("representative-course-missing")
    list_origin, list_hash = _repo_relative_template_origin("courses/course_list.html")
    detail_origin, detail_hash = _repo_relative_template_origin("courses/course.html")
    list_callback = f"{list_match.func.__module__}.{list_match.func.__name__}"
    detail_callback = f"{detail_match.func.__module__}.{detail_match.func.__name__}"
    if list_callback != "courses.views.course_list.course_list":
        raise DevelopmentContentImportError("course-list-route-drift")
    if detail_callback != "courses.views.course.course_view":
        raise DevelopmentContentImportError("course-detail-route-drift")
    if list_hash != EXPECTED_LIST_TEMPLATE_SHA256:
        raise DevelopmentContentImportError("course-list-template-drift")
    if detail_hash != EXPECTED_DETAIL_TEMPLATE_SHA256:
        raise DevelopmentContentImportError("course-detail-template-drift")
    representative = {
        "slug": representative_slug,
        "courses": Course.objects.filter(slug=representative_slug).count(),
        "homeworks": Homework.objects.filter(course__slug=representative_slug).count(),
        "projects": Project.objects.filter(course__slug=representative_slug).count(),
        "questions": Question.objects.filter(homework__course__slug=representative_slug).count(),
    }
    if representative_slug == "de-zoomcamp-2026" and representative != {
        "slug": "de-zoomcamp-2026",
        "courses": 1,
        "homeworks": 8,
        "projects": 3,
        "questions": 50,
    }:
        raise DevelopmentContentImportError("representative-course-content-drift")
    return {
        "schema_version": contract.schema_version,
        "schema_checksum": contract.schema_checksum,
        "logical_checksum": contract.logical_checksum,
        "semantic_checksum": contract.semantic_checksum,
        "table_counts": target.counts,
        "relationship_counts": target.relationships,
        "representative": representative,
        "routes": {
            "list": list_callback,
            "detail": detail_callback,
        },
        "templates": {
            "list": {"origin": list_origin, "sha256": list_hash},
            "detail": {"origin": detail_origin, "sha256": detail_hash},
        },
    }
