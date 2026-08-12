"""Fail-closed local SQLite import for public CMP review content."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qsl, quote, urlsplit

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator

from review_import.environment import disable_local_review_provider_environment
from review_import.manifest import (
    ALLOWLIST,
    ALLOWLIST_SCHEMA_VERSION,
    COPY_ORDER,
    is_sensitive_table,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = PROJECT_ROOT / ".tmp" / "review-data"
ARTIFACTS_DIR = PRIVATE_ROOT / "artifacts"
REPORTS_DIR = PRIVATE_ROOT / "reports"
WORK_DIR = PRIVATE_ROOT / "work"
DEFAULT_TARGET = PRIVATE_ROOT / "review.sqlite3"
SYNTHETIC_ADMIN_EMAIL = "review-admin@example.invalid"
DEFAULT_ADMIN_PASSWORD = "admin"

SNAPSHOT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
SLUG_PATTERN = re.compile(r"[-a-zA-Z0-9_]+\Z")
SENSITIVE_QUERY_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "credential",
    "password",
    "secret",
    "signature",
    "token",
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
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
URL_COLUMNS = frozenset(
    {
        "registration_url",
        "github_repo_url",
        "faq_document_url",
        "hero_image_url",
        "video_url",
        "instructions_url",
    }
)
NULLABLE_COLUMNS = frozenset(
    {
        "start_date",
        "end_date",
        "current_course_id",
        "instructions_url",
        "answer_type",
        "possible_answers",
        "correct_answer",
    }
)
QUESTION_TYPES = frozenset({"MC", "FF", "FL", "CB"})
ANSWER_TYPES = frozenset({"ANY", "FLT", "INT", "EXS", "CTS"})
HOMEWORK_STATES = frozenset({"CL", "OP", "SC"})
PROJECT_STATES = frozenset({"CL", "CS", "PR", "CO"})
REVIEW_CRITERIA_TYPES = frozenset({"RB", "CB"})
ADOPTED_PUBLIC_URL_VALIDATOR = URLValidator(schemes=["http", "https"])


@dataclass
class ImportFailure(RuntimeError):
    """A safe-to-render import failure without source values or paths."""

    category: str
    table: str = "-"
    column: str = "-"
    count: int = 1

    def __str__(self) -> str:
        return (
            "review import failed: "
            f"category={self.category} table={self.table} "
            f"column={self.column} count={self.count}"
        )


@dataclass(frozen=True)
class SnapshotFingerprint:
    size: int
    sha256: str


@dataclass(frozen=True)
class ImportConfig:
    source_db: Path
    snapshot_id: str
    target_db: Path = DEFAULT_TARGET
    dry_run: bool = False
    create_admin: bool = True
    admin_password: str = DEFAULT_ADMIN_PASSWORD
    # Tests construct synthetic snapshots below the required repo-local .tmp root.
    # The public CLI never enables this exception.
    allow_repo_source_for_tests: bool = False


@dataclass(frozen=True)
class ImportPaths:
    source: Path
    target: Path
    artifact: Path
    report: Path
    work: Path


@dataclass(frozen=True)
class AllowedDataset:
    rows: dict[str, list[tuple[Any, ...]]]
    counts: dict[str, int]
    relationships: dict[str, int]
    logical_checksum: str


@dataclass(frozen=True)
class _ForeignKeyConstraint:
    parent_table: str
    columns: tuple[tuple[str, str], ...]


FaultHook = Callable[[str], None]


def _no_fault(_stage: str) -> None:
    return


def _assert_local_environment() -> str:
    environment = os.getenv("DTC_ENVIRONMENT", "local").strip().lower()
    if environment not in {"", "local", "test"}:
        raise ImportFailure("deployed-environment-refused")
    return environment


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolved(path: Path, *, strict: bool = False) -> Path:
    return path.expanduser().resolve(strict=strict)


def _validate_snapshot_id(snapshot_id: str) -> None:
    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise ImportFailure("invalid-snapshot-id")


def _validate_sqlite_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ImportFailure("source-not-regular-file")
    with path.open("rb") as source:
        header = source.read(16)
    if header != b"SQLite format 3\x00":
        raise ImportFailure("source-not-sqlite")


def _validate_existing_target(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_file() or path.is_symlink():
        raise ImportFailure("unsafe-target-path")
    with path.open("rb") as target:
        header = target.read(16)
    if header != b"SQLite format 3\x00":
        raise ImportFailure("target-not-sqlite")
    try:
        connection = sqlite3.connect(
            f"file:{quote(str(path), safe='/')}?mode=ro",
            uri=True,
        )
        try:
            connection.execute("PRAGMA schema_version").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        raise ImportFailure("target-not-sqlite") from None


def _is_gitignored(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", str(path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def resolve_paths(config: ImportConfig) -> ImportPaths:
    _validate_snapshot_id(config.snapshot_id)
    source = _resolved(config.source_db, strict=True)
    target = _resolved(config.target_db)
    private_root = _resolved(PRIVATE_ROOT)
    artifacts_dir = _resolved(ARTIFACTS_DIR)
    reports_dir = _resolved(REPORTS_DIR)
    work_root = _resolved(WORK_DIR)

    _validate_sqlite_file(source)
    if _is_within(source, _resolved(PROJECT_ROOT)) and not config.allow_repo_source_for_tests:
        raise ImportFailure("protected-source-inside-repository")
    if not _is_within(target, _resolved(PROJECT_ROOT)) or not _is_gitignored(target):
        raise ImportFailure("unsafe-target-path")
    if target.suffix not in {".db", ".sqlite", ".sqlite3"}:
        raise ImportFailure("target-not-sqlite")
    _validate_existing_target(target)

    artifact = artifacts_dir / f"{config.snapshot_id}.sqlite3"
    report = reports_dir / f"{config.snapshot_id}.json"
    work = work_root / f"{config.snapshot_id}-{uuid.uuid4().hex}"
    derived_paths = (artifact, report, work)
    for derived in derived_paths:
        if derived.is_symlink() or not _is_within(_resolved(derived), private_root):
            raise ImportFailure("temporary-path-escape")

    all_paths = (source, target, artifact, report, work)
    if len(set(all_paths)) != len(all_paths):
        raise ImportFailure("path-alias")
    for left_index, left in enumerate(all_paths):
        if not left.exists():
            continue
        for right in all_paths[left_index + 1 :]:
            if right.exists() and os.path.samefile(left, right):
                raise ImportFailure("path-alias")

    return ImportPaths(source, target, artifact, report, work)


def fingerprint(path: Path) -> SnapshotFingerprint:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return SnapshotFingerprint(size=path.stat().st_size, sha256=digest.hexdigest())


def _assert_source_unchanged(path: Path, expected: SnapshotFingerprint) -> None:
    if fingerprint(path) != expected:
        raise ImportFailure("source-changed")


def _chmod_private(path: Path, *, directory: bool) -> None:
    path.chmod(0o700 if directory else 0o600)


def _prepare_private_directories() -> None:
    for directory in (PRIVATE_ROOT, ARTIFACTS_DIR, REPORTS_DIR, WORK_DIR):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not _is_within(_resolved(directory), _resolved(PRIVATE_ROOT)):
            raise ImportFailure("temporary-path-escape")
        _chmod_private(directory, directory=True)


def _raise_generated_lock_failure(category: str) -> NoReturn:
    """Raise a safe lock error detached from ambient caller exception state."""

    try:
        raise ImportFailure(category)
    except ImportFailure as error:
        error.__context__ = None
        error.__cause__ = None
        error.__suppress_context__ = True
        raise


def _finalize_operation_lock(
    descriptor: int,
    *,
    locked: bool,
    active_failure: bool,
) -> None:
    unlock_failed = False
    close_failed = False
    try:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        unlock_failed = True
    finally:
        try:
            os.close(descriptor)
        except OSError:
            close_failed = True

    if active_failure:
        return
    if unlock_failed:
        _raise_generated_lock_failure("lock-release")
    if close_failed:
        _raise_generated_lock_failure("lock-close")


def _validated_operation_lock_root() -> Path:
    repository_tmp = PROJECT_ROOT / ".tmp"
    private_root = PRIVATE_ROOT
    try:
        resolved_repository_tmp = _resolved(repository_tmp)
        resolved_private_root = _resolved(private_root)
        unsafe = (
            repository_tmp.is_symlink()
            or private_root.is_symlink()
            or not private_root.is_dir()
            or resolved_private_root == resolved_repository_tmp
            or not _is_within(resolved_private_root, resolved_repository_tmp)
        )
    except ImportFailure:
        raise
    except (OSError, RuntimeError):
        pass
    else:
        if unsafe:
            _raise_generated_lock_failure("unsafe-lock-path")
        return private_root
    _raise_generated_lock_failure("unsafe-lock-path")


def _open_operation_lock(private_root: Path, flags: int) -> int:
    try:
        return os.open(private_root, flags)
    except ImportFailure:
        raise
    except (OSError, RuntimeError):
        pass
    _raise_generated_lock_failure("unsafe-lock-path")


def _operation_lock_metadata(
    descriptor: int,
    private_root: Path,
) -> tuple[os.stat_result, os.stat_result]:
    try:
        return os.fstat(descriptor), os.stat(private_root, follow_symlinks=False)
    except ImportFailure:
        raise
    except (OSError, RuntimeError):
        pass
    _raise_generated_lock_failure("unsafe-lock-path")


def _acquire_operation_lock(descriptor: int) -> None:
    category: str
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    except ImportFailure:
        raise
    except BlockingIOError:
        category = "concurrent-operation"
    except (OSError, RuntimeError):
        category = "lock-acquire"
    _raise_generated_lock_failure(category)


@contextmanager
def _operation_lock() -> Iterator[None]:
    """Lock the owned directory inode without retaining a coordination file."""

    private_root = _validated_operation_lock_root()

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = _open_operation_lock(private_root, flags)

    locked = False
    operation_failed = False
    try:
        metadata, path_metadata = _operation_lock_metadata(descriptor, private_root)
        if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != (
            path_metadata.st_dev,
            path_metadata.st_ino,
        ):
            _raise_generated_lock_failure("unsafe-lock-path")
        _acquire_operation_lock(descriptor)
        locked = True
        yield
    except BaseException:
        operation_failed = True
        raise
    finally:
        _finalize_operation_lock(
            descriptor,
            locked=locked,
            active_failure=operation_failed,
        )


@contextmanager
def _private_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _django_environment(db_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    disable_local_review_provider_environment(environment)
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "website.settings.local_review",
            "DTC_ENVIRONMENT": "local",
            "DTC_USE_SQLITE": "true",
            "DTC_SQLITE_PATH": str(db_path),
        }
    )
    return environment


def _run_django(db_path: Path, arguments: Sequence[str], category: str) -> None:
    result = subprocess.run(
        [sys.executable, "manage.py", *arguments],
        cwd=PROJECT_ROOT,
        env=_django_environment(db_path),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ImportFailure(category)


def _migrate_fresh_database(db_path: Path) -> None:
    if db_path.exists():
        raise ImportFailure("work-file-already-exists")
    _run_django(db_path, ("migrate", "--noinput", "--verbosity", "0"), "migration")
    if not db_path.is_file():
        raise ImportFailure("migration-output-missing")
    with _writable_connection(db_path) as connection:
        _scrub_sensitive_rows(connection)
    _chmod_private(db_path, directory=False)


def _quote_identifier(identifier: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ImportFailure("invalid-schema-identifier")
    return f'"{identifier}"'


@contextmanager
def _readonly_connection(path: Path) -> Iterator[sqlite3.Connection]:
    encoded_path = quote(str(path), safe="/")
    connection = sqlite3.connect(
        f"file:{encoded_path}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "REGEXP",
            2,
            lambda expression, value: bool(value and re.search(expression, str(value))),
            deterministic=True,
        )
        connection.execute("PRAGMA query_only=ON")
        with connection:
            yield connection
    finally:
        connection.close()


@contextmanager
def _writable_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "REGEXP",
            2,
            lambda expression, value: bool(value and re.search(expression, str(value))),
            deterministic=True,
        )
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            yield connection
    finally:
        connection.close()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return {str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_")}


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    query = f"PRAGMA table_info({_quote_identifier(table)})"
    return {str(row["name"]): row for row in connection.execute(query).fetchall()}


def _foreign_key_constraints(
    connection: sqlite3.Connection, table: str
) -> tuple[_ForeignKeyConstraint, ...]:
    grouped: dict[int, list[tuple[int, str, str, str]]] = {}
    query = f"PRAGMA foreign_key_list({_quote_identifier(table)})"
    for row in connection.execute(query).fetchall():
        grouped.setdefault(int(row["id"]), []).append(
            (int(row["seq"]), str(row["from"]), str(row["to"]), str(row["table"]))
        )

    constraints: list[_ForeignKeyConstraint] = []
    for entries in grouped.values():
        ordered = sorted(entries)
        parent_table = ordered[0][3]
        constraints.append(
            _ForeignKeyConstraint(
                parent_table=parent_table,
                columns=tuple((entry[1], entry[2]) for entry in ordered),
            )
        )
    return tuple(constraints)


def _sensitive_delete_order(
    sensitive_tables: set[str],
    foreign_keys: Mapping[str, tuple[_ForeignKeyConstraint, ...]],
) -> tuple[str, ...]:
    dependents: dict[str, set[str]] = {table: set() for table in sensitive_tables}
    for child_table in sensitive_tables:
        for constraint in foreign_keys.get(child_table, ()):
            parent_table = constraint.parent_table
            if parent_table not in sensitive_tables:
                continue
            if parent_table == child_table:
                continue
            dependents[parent_table].add(child_table)

    remaining = set(sensitive_tables)
    deletion_order: list[str] = []
    while remaining:
        leaves = sorted(table for table in remaining if not (dependents[table] & remaining))
        if not leaves:
            first = sorted(remaining)[0]
            raise ImportFailure("sensitive-foreign-key-cycle", table=first, count=len(remaining))
        deletion_order.extend(leaves)
        remaining.difference_update(leaves)
    return tuple(deletion_order)


def _clear_non_sensitive_references(
    connection: sqlite3.Connection,
    parent_table: str,
    foreign_keys: Mapping[str, tuple[_ForeignKeyConstraint, ...]],
) -> None:
    parent_columns = _columns(connection, parent_table)
    for child_table, constraints in foreign_keys.items():
        if is_sensitive_table(child_table):
            continue
        child_columns = _columns(connection, child_table)
        for constraint in constraints:
            if constraint.parent_table != parent_table:
                continue
            columns = tuple(column for column, _parent_column in constraint.columns)
            if any(column not in child_columns for column in columns):
                raise ImportFailure(
                    "sensitive-dependency",
                    table=child_table,
                    column=columns[0],
                )
            if any(
                parent_column not in parent_columns for _column, parent_column in constraint.columns
            ):
                raise ImportFailure("sensitive-dependency", table=parent_table, count=1)

            match = " AND ".join(
                f"c.{_quote_identifier(column)} = p.{_quote_identifier(parent_column)}"
                for column, parent_column in constraint.columns
            )
            count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(child_table)} AS c "
                    f"JOIN {_quote_identifier(parent_table)} AS p ON {match}"
                ).fetchone()[0]
            )
            if not count:
                continue
            if any(
                bool(child_columns[column]["notnull"]) or bool(child_columns[column]["pk"])
                for column in columns
            ):
                raise ImportFailure(
                    "sensitive-dependency",
                    table=child_table,
                    column=columns[0],
                    count=count,
                )
            assignments = ", ".join(f"{_quote_identifier(column)} = NULL" for column in columns)
            connection.execute(
                f"UPDATE {_quote_identifier(child_table)} AS c SET {assignments} "
                f"WHERE EXISTS (SELECT 1 FROM {_quote_identifier(parent_table)} AS p WHERE {match})"
            )


def _clear_self_references(
    connection: sqlite3.Connection,
    table: str,
    constraints: Sequence[_ForeignKeyConstraint],
) -> None:
    columns_by_name = _columns(connection, table)
    for constraint in constraints:
        if constraint.parent_table != table:
            continue
        columns = tuple(column for column, _parent_column in constraint.columns)
        if any(column not in columns_by_name for column in columns):
            raise ImportFailure("sensitive-dependency", table=table, column=columns[0])
        match = " AND ".join(
            f"c.{_quote_identifier(column)} = p.{_quote_identifier(parent_column)}"
            for column, parent_column in constraint.columns
        )
        count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table)} AS c "
                f"JOIN {_quote_identifier(table)} AS p ON {match}"
            ).fetchone()[0]
        )
        if not count:
            continue
        if any(
            bool(columns_by_name[column]["notnull"]) or bool(columns_by_name[column]["pk"])
            for column in columns
        ):
            raise ImportFailure("sensitive-dependency", table=table, column=columns[0], count=count)
        assignments = ", ".join(f"{_quote_identifier(column)} = NULL" for column in columns)
        connection.execute(
            f"UPDATE {_quote_identifier(table)} AS c SET {assignments} "
            f"WHERE EXISTS (SELECT 1 FROM {_quote_identifier(table)} AS p WHERE {match})"
        )


def _scrub_sensitive_rows(connection: sqlite3.Connection) -> None:
    foreign_keys_enabled = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    if foreign_keys_enabled != 1:
        raise ImportFailure("foreign-keys-disabled")

    tables = _table_names(connection)
    foreign_keys = {table: _foreign_key_constraints(connection, table) for table in sorted(tables)}
    sensitive_tables = {
        table
        for table in tables
        if is_sensitive_table(table)
        and connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0]
    }
    for table in _sensitive_delete_order(sensitive_tables, foreign_keys):
        _clear_self_references(connection, table, foreign_keys[table])
        _clear_non_sensitive_references(connection, table, foreign_keys)
        connection.execute(f"DELETE FROM {_quote_identifier(table)}")

    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise ImportFailure("foreign-keys-disabled")
    _assert_sqlite_integrity(connection)


def _validate_source_schema(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
) -> None:
    source_tables = _table_names(source)
    target_tables = _table_names(target)
    unknown_tables = sorted(source_tables - target_tables)
    if unknown_tables:
        raise ImportFailure(
            "schema-unknown-table", table=unknown_tables[0], count=len(unknown_tables)
        )

    for table in sorted(source_tables):
        source_columns = set(_columns(source, table))
        target_columns = set(_columns(target, table))
        unknown_columns = sorted(source_columns - target_columns)
        if unknown_columns:
            raise ImportFailure(
                "schema-unknown-column",
                table=table,
                column=unknown_columns[0],
                count=len(unknown_columns),
            )

    for table, allowed_columns in ALLOWLIST.items():
        if table not in source_tables:
            raise ImportFailure("schema-missing-table", table=table)
        missing_columns = sorted(set(allowed_columns) - set(_columns(source, table)))
        if missing_columns:
            raise ImportFailure(
                "schema-missing-column",
                table=table,
                column=missing_columns[0],
                count=len(missing_columns),
            )


def _is_integer_column(column: str) -> bool:
    return (
        column == "id"
        or column.endswith("_id")
        or column
        in {
            "learning_in_public_cap",
            "learning_in_public_cap_project",
            "learning_in_public_cap_review",
            "min_projects_to_pass",
            "number_of_peers_to_evaluate",
            "points_for_peer_review",
            "project_passing_score",
            "scores_for_correct_answer",
            "total_certificates",
            "total_enrollments",
            "total_participants",
            "total_points",
            "total_submissions",
            "year",
        }
    )


def _is_numeric_column(column: str) -> bool:
    return (
        column.startswith(("min_", "max_", "avg_", "median_", "q1_", "q3_"))
        or column == "total_hours"
    )


def _validate_date(value: Any, *, with_time: bool) -> None:
    if not isinstance(value, str):
        raise ValueError
    if with_time:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        date.fromisoformat(value)


def _validate_url(value: Any) -> None:
    if value in {"", None}:
        return
    if not isinstance(value, str):
        raise ValueError
    ADOPTED_PUBLIC_URL_VALIDATOR(value)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError
    if parsed.username or parsed.password:
        raise ValueError
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.casefold().replace("-", "_")
        if any(part in normalized_key for part in SENSITIVE_QUERY_KEY_PARTS):
            raise ValueError
        if any(pattern.search(query_value) for pattern in SECRET_VALUE_PATTERNS):
            raise ValueError


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _validate_review_options(value: Any) -> None:
    options = _decode_json(value)
    if not isinstance(options, list) or not options:
        raise ValueError
    for option in options:
        if not isinstance(option, dict) or set(option) != {"criteria", "score"}:
            raise ValueError
        if not isinstance(option["criteria"], str) or not option["criteria"].strip():
            raise ValueError
        if isinstance(option["score"], bool) or not isinstance(option["score"], int):
            raise ValueError


def _validate_course_stats(value: Any, course_slugs: set[str]) -> int:
    course_stats = _decode_json(value)
    if not isinstance(course_stats, list):
        raise ValueError
    for item in course_stats:
        if not isinstance(item, dict) or set(item) != {"title", "slug", "enrollment_count"}:
            raise ValueError
        if not isinstance(item["title"], str) or not item["title"].strip():
            raise ValueError
        if not isinstance(item["slug"], str) or item["slug"] not in course_slugs:
            raise ValueError
        count = item["enrollment_count"]
        if (
            isinstance(count, bool)
            or not isinstance(count, (int, float))
            or not math.isfinite(count)
        ):
            raise ValueError
    return len(course_stats)


def _validate_domain(table: str, column: str, value: Any) -> None:
    if value is None:
        if column in NULLABLE_COLUMNS or column.startswith(
            ("min_", "max_", "avg_", "median_", "q1_", "q3_")
        ):
            return
        raise ValueError
    if column in BOOLEAN_COLUMNS:
        if not isinstance(value, (bool, int)) or value not in (0, 1):
            raise ValueError
        return
    if column in DATE_COLUMNS:
        _validate_date(value, with_time=False)
        return
    if column in DATETIME_COLUMNS:
        _validate_date(value, with_time=True)
        return
    if column in URL_COLUMNS:
        _validate_url(value)
        return
    if column == "options":
        _validate_review_options(value)
        return
    if column == "course_stats":
        return
    if _is_integer_column(column):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError
        return
    if _is_numeric_column(column):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError
        return
    if not isinstance(value, str):
        raise ValueError
    if column == "slug" and (not value or not SLUG_PATTERN.fullmatch(value)):
        raise ValueError
    if column == "state" and table == "courses_homework" and value not in HOMEWORK_STATES:
        raise ValueError
    if column == "state" and table == "courses_project" and value not in PROJECT_STATES:
        raise ValueError
    if column == "question_type" and value not in QUESTION_TYPES:
        raise ValueError
    if column == "answer_type" and value not in ANSWER_TYPES | {""}:
        raise ValueError
    if column == "review_criteria_type" and value not in REVIEW_CRITERIA_TYPES:
        raise ValueError


def _validate_row(table: str, columns: tuple[str, ...], row: tuple[Any, ...]) -> None:
    for column, value in zip(columns, row, strict=True):
        try:
            _validate_domain(table, column, value)
        except (TypeError, ValueError, DjangoValidationError, json.JSONDecodeError):
            raise ImportFailure("field-validation", table=table, column=column) from None


def _canonical_value(column: str, value: Any) -> Any:
    if column in {"options", "course_stats"}:
        return _decode_json(value)
    return value


def _logical_checksum(rows_by_table: Mapping[str, Sequence[tuple[Any, ...]]]) -> str:
    digest = hashlib.sha256()
    for table in COPY_ORDER:
        columns = ALLOWLIST[table]
        digest.update(table.encode())
        digest.update(b"\x00")
        digest.update(json.dumps(columns, separators=(",", ":")).encode())
        for row in rows_by_table[table]:
            canonical_row = [
                _canonical_value(column, value) for column, value in zip(columns, row, strict=True)
            ]
            payload = json.dumps(
                canonical_row,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def _column_index(table: str, column: str) -> int:
    return ALLOWLIST[table].index(column)


def _relationship_evidence(rows: Mapping[str, Sequence[tuple[Any, ...]]]) -> dict[str, int]:
    courses = {row[_column_index("courses_course", "id")] for row in rows["courses_course"]}
    course_slugs = {row[_column_index("courses_course", "slug")] for row in rows["courses_course"]}
    homeworks = {row[_column_index("courses_homework", "id")] for row in rows["courses_homework"]}
    projects = {row[_column_index("courses_project", "id")] for row in rows["courses_project"]}

    relation_specs = (
        ("campaign_course", "courses_registrationcampaign", "current_course_id", courses, False),
        ("homework_course", "courses_homework", "course_id", courses, True),
        ("question_homework", "courses_question", "homework_id", homeworks, True),
        (
            "homework_statistics",
            "courses_homeworkstatistics",
            "homework_id",
            homeworks,
            True,
        ),
        ("project_course", "courses_project", "course_id", courses, True),
        ("criteria_course", "courses_reviewcriteria", "course_id", courses, True),
        (
            "project_statistics",
            "courses_projectstatistics",
            "project_id",
            projects,
            True,
        ),
    )
    evidence: dict[str, int] = {}
    for name, table, column, valid_ids, required in relation_specs:
        index = _column_index(table, column)
        linked = 0
        for row in rows[table]:
            value = row[index]
            if value is None and not required:
                continue
            if value not in valid_ids:
                raise ImportFailure("broken-relationship", table=table, column=column)
            linked += 1
        evidence[name] = linked

    wrapped_links = 0
    course_stats_index = _column_index("courses_wrappedstatistics", "course_stats")
    for row in rows["courses_wrappedstatistics"]:
        try:
            wrapped_links += _validate_course_stats(row[course_stats_index], course_slugs)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ImportFailure(
                "field-validation",
                table="courses_wrappedstatistics",
                column="course_stats",
            ) from None
    evidence["wrapped_course_stats"] = wrapped_links

    course_start_index = _column_index("courses_course", "start_date")
    course_end_index = _column_index("courses_course", "end_date")
    for row in rows["courses_course"]:
        start_value = row[course_start_index]
        end_value = row[course_end_index]
        if (
            start_value
            and end_value
            and date.fromisoformat(end_value) < date.fromisoformat(start_value)
        ):
            raise ImportFailure(
                "field-validation",
                table="courses_course",
                column="end_date",
            )
    return evidence


def _read_allowed_dataset(connection: sqlite3.Connection) -> AllowedDataset:
    rows_by_table: dict[str, list[tuple[Any, ...]]] = {}
    counts: dict[str, int] = {}
    for table in COPY_ORDER:
        columns = ALLOWLIST[table]
        column_sql = ", ".join(_quote_identifier(column) for column in columns)
        query = (
            f"SELECT {column_sql} FROM {_quote_identifier(table)} "
            f"ORDER BY {_quote_identifier('id')}"
        )
        rows: list[tuple[Any, ...]] = []
        for source_row in connection.execute(query):
            row = tuple(source_row[column] for column in columns)
            _validate_row(table, columns, row)
            rows.append(row)
        rows_by_table[table] = rows
        counts[table] = len(rows)
    relationships = _relationship_evidence(rows_by_table)
    return AllowedDataset(
        rows=rows_by_table,
        counts=counts,
        relationships=relationships,
        logical_checksum=_logical_checksum(rows_by_table),
    )


def _insert_dataset(target: sqlite3.Connection, dataset: AllowedDataset) -> None:
    target.execute("PRAGMA foreign_keys=ON")
    with target:
        for table in COPY_ORDER:
            columns = ALLOWLIST[table]
            target_columns = columns
            target_rows = dataset.rows[table]
            if table == "courses_wrappedstatistics":
                target_columns = (*columns, "leaderboard")
                target_rows = [(*row, "[]") for row in target_rows]
            column_sql = ", ".join(_quote_identifier(column) for column in target_columns)
            placeholders = ", ".join("?" for _column in target_columns)
            query = f"INSERT INTO {_quote_identifier(table)} ({column_sql}) VALUES ({placeholders})"
            target.executemany(query, target_rows)
        _refresh_sequences(target, dataset.counts)


def _refresh_sequences(connection: sqlite3.Connection, counts: Mapping[str, int]) -> None:
    sequence_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone()
    if not sequence_exists:
        return
    for table in COPY_ORDER:
        if not counts[table]:
            continue
        max_id = connection.execute(
            f"SELECT MAX({_quote_identifier('id')}) FROM {_quote_identifier(table)}"
        ).fetchone()[0]
        connection.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        connection.execute(
            "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
            (table, max_id),
        )


def _assert_sqlite_integrity(connection: sqlite3.Connection) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchall()
    if len(integrity) != 1 or integrity[0][0] != "ok":
        raise ImportFailure("sqlite-integrity", count=len(integrity))
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        table = str(violations[0][0])
        raise ImportFailure("sqlite-foreign-key", table=table, count=len(violations))


def _assert_source_integrity(connection: sqlite3.Connection) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchall()
    if len(integrity) != 1 or integrity[0][0] != "ok":
        raise ImportFailure("source-sqlite-integrity", count=len(integrity))


def _sensitive_zero_counts(connection: sqlite3.Connection) -> dict[str, int]:
    results: dict[str, int] = {}
    for table in sorted(_table_names(connection)):
        if not is_sensitive_table(table):
            continue
        count = int(
            connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0]
        )
        results[table] = count
    return results


def _assert_sanitized_database(path: Path, expected: AllowedDataset) -> dict[str, int]:
    with _writable_connection(path) as connection:
        _assert_sqlite_integrity(connection)
        actual = _read_allowed_dataset(connection)
        if (
            actual.counts != expected.counts
            or actual.relationships != expected.relationships
            or actual.logical_checksum != expected.logical_checksum
        ):
            raise ImportFailure("content-reconciliation")
        leaderboards = connection.execute(
            "SELECT leaderboard FROM courses_wrappedstatistics ORDER BY id"
        ).fetchall()
        for row in leaderboards:
            try:
                if _decode_json(row[0]) != []:
                    raise ValueError
            except (TypeError, ValueError, json.JSONDecodeError):
                raise ImportFailure(
                    "field-validation",
                    table="courses_wrappedstatistics",
                    column="leaderboard",
                ) from None
        deny_counts = _sensitive_zero_counts(connection)
        nonzero = {table: count for table, count in deny_counts.items() if count}
        if nonzero:
            first = sorted(nonzero)[0]
            raise ImportFailure("denylist-not-empty", table=first, count=nonzero[first])
    _run_django(path, ("migrate", "--check", "--verbosity", "0"), "migration-check")
    _run_django(path, ("check", "--verbosity", "0"), "django-system-check")
    return deny_counts


def _create_synthetic_admin(path: Path, password: str) -> None:
    if not password:
        raise ImportFailure("empty-admin-password")
    environment = _django_environment(path)
    environment["REVIEW_ADMIN_PASSWORD"] = password
    code = """
import django
django.setup()
from review_import.admin import create_synthetic_admin
create_synthetic_admin(__import__('os').environ['REVIEW_ADMIN_PASSWORD'])
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ImportFailure("synthetic-admin-bootstrap")


def _assert_final_database(path: Path, expected: AllowedDataset, create_admin: bool) -> None:
    with _writable_connection(path) as connection:
        _assert_sqlite_integrity(connection)
        actual = _read_allowed_dataset(connection)
        if (
            actual.counts != expected.counts
            or actual.relationships != expected.relationships
            or actual.logical_checksum != expected.logical_checksum
        ):
            raise ImportFailure("target-reconciliation")
        users = connection.execute(
            "SELECT email, is_staff, is_superuser FROM accounts_customuser ORDER BY id"
        ).fetchall()
        expected_users = 1 if create_admin else 0
        if len(users) != expected_users:
            raise ImportFailure(
                "target-account-boundary", table="accounts_customuser", count=len(users)
            )
        if create_admin:
            user = users[0]
            if (
                user["email"] != SYNTHETIC_ADMIN_EMAIL
                or not user["is_staff"]
                or not user["is_superuser"]
            ):
                raise ImportFailure("target-account-boundary", table="accounts_customuser")
            role_permissions = connection.execute(
                """
                SELECT auth_group.name AS group_name,
                       django_content_type.app_label || '.' || auth_permission.codename
                           AS permission_name
                FROM accounts_customuser
                JOIN accounts_customuser_groups
                  ON accounts_customuser_groups.customuser_id = accounts_customuser.id
                JOIN auth_group
                  ON auth_group.id = accounts_customuser_groups.group_id
                JOIN auth_group_permissions
                  ON auth_group_permissions.group_id = auth_group.id
                JOIN auth_permission
                  ON auth_permission.id = auth_group_permissions.permission_id
                JOIN django_content_type
                  ON django_content_type.id = auth_permission.content_type_id
                WHERE accounts_customuser.email = ?
                ORDER BY auth_group.name, permission_name
                """,
                (SYNTHETIC_ADMIN_EMAIL,),
            ).fetchall()
            if [(row["group_name"], row["permission_name"]) for row in role_permissions] != [
                ("course_operator", "core.access_studio")
            ]:
                raise ImportFailure("target-account-boundary", table="auth_group")
        deny_counts = _sensitive_zero_counts(connection)
        synthetic_role_counts = {
            "accounts_customuser": expected_users,
            "accounts_customuser_groups": expected_users,
            "auth_group": expected_users,
            "auth_group_permissions": expected_users,
        }
        for table, count in deny_counts.items():
            allowed_count = synthetic_role_counts.get(table, 0)
            if count != allowed_count:
                raise ImportFailure("denylist-not-empty", table=table, count=count)
    _run_django(path, ("migrate", "--check", "--verbosity", "0"), "migration-check")
    _run_django(path, ("check", "--verbosity", "0"), "django-system-check")


def _representative_review_paths(dataset: AllowedDataset) -> list[str]:
    paths = {"/courses/"}
    course_id_index = _column_index("courses_course", "id")
    course_slug_index = _column_index("courses_course", "slug")
    course_finished_index = _column_index("courses_course", "finished")
    course_slugs = {
        row[course_id_index]: row[course_slug_index] for row in dataset.rows["courses_course"]
    }
    selected_finished_states: set[bool] = set()
    for row in dataset.rows["courses_course"]:
        finished = bool(row[course_finished_index])
        if finished not in selected_finished_states:
            paths.add(f"/courses/{row[course_slug_index]}/")
            selected_finished_states.add(finished)

    for table, relation_column, slug_column, suffix in (
        ("courses_homework", "course_id", "slug", "homework"),
        ("courses_project", "course_id", "slug", "project"),
    ):
        if not dataset.rows[table]:
            continue
        row = dataset.rows[table][0]
        course_slug = course_slugs[row[_column_index(table, relation_column)]]
        item_slug = row[_column_index(table, slug_column)]
        base = f"/courses/{course_slug}/{suffix}/{item_slug}"
        paths.update({base, f"{base}/stats"})

    if dataset.rows["courses_registrationcampaign"]:
        row = dataset.rows["courses_registrationcampaign"][0]
        slug = row[_column_index("courses_registrationcampaign", "slug")]
        paths.add(f"/courses/register/{slug}/")
    if dataset.rows["courses_wrappedstatistics"]:
        row = dataset.rows["courses_wrappedstatistics"][0]
        year = row[_column_index("courses_wrappedstatistics", "year")]
        paths.add(f"/courses/wrapped/{year}/")
    return sorted(paths)


def _assert_public_review_pages(path: Path, dataset: AllowedDataset) -> None:
    environment = _django_environment(path)
    environment["REVIEW_ROUTE_PATHS"] = json.dumps(_representative_review_paths(dataset))
    code = """
import json
import os
import django
django.setup()
from django.test import Client
client = Client()
for path in json.loads(os.environ['REVIEW_ROUTE_PATHS']):
    response = client.get(path, follow=True)
    if response.status_code != 200:
        raise SystemExit(2)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ImportFailure("public-content-smoke")


def _build_sanitized_database(
    source_path: Path,
    destination: Path,
    fault_hook: FaultHook,
) -> tuple[AllowedDataset, dict[str, int]]:
    _migrate_fresh_database(destination)
    with _readonly_connection(source_path) as source, _writable_connection(destination) as target:
        _assert_source_integrity(source)
        _validate_source_schema(source, target)
        dataset = _read_allowed_dataset(source)
        fault_hook("during-validation")
        _insert_dataset(target, dataset)
    fault_hook("after-build")
    deny_counts = _assert_sanitized_database(destination, dataset)
    return dataset, deny_counts


def _repo_relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _report(
    config: ImportConfig,
    paths: ImportPaths,
    source: SnapshotFingerprint,
    dataset: AllowedDataset,
    deny_counts: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "snapshot_id": config.snapshot_id,
        "source_size": source.size,
        "source_sha256": source.sha256,
        "allowlist_schema_version": ALLOWLIST_SCHEMA_VERSION,
        "table_counts": dataset.counts,
        "relationship_counts": dataset.relationships,
        "source_origin_denylist_zero_counts": {table: 0 for table in deny_counts},
        "logical_checksum": dataset.logical_checksum,
        "validation_results": {
            "source_read_only": "passed",
            "source_schema": "passed",
            "field_domains": "passed",
            "relationships": "passed",
            "denylist": "passed",
            "sqlite_integrity": "passed",
            "sqlite_foreign_keys": "passed",
            "django_migrations": "passed",
            "django_system": "passed",
            "public_content_smoke": "passed",
            "outbound_side_effects": "disabled",
        },
        "synthetic_admin_count": int(config.create_admin and not config.dry_run),
        "derived_paths": {
            "sanitized": _repo_relative(paths.artifact),
            "report": _repo_relative(paths.report),
            "target": _repo_relative(paths.target),
        },
    }


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _chmod_private(path, directory=False)


def _safe_unlink(path: Path, *, parent: Path) -> bool:
    if not _is_within(path, parent) or path == parent:
        raise ImportFailure("unsafe-cleanup-path")
    if path.is_symlink():
        raise ImportFailure("unsafe-cleanup-symlink")
    if path.exists():
        if not path.is_file():
            raise ImportFailure("unsafe-cleanup-path")
        path.unlink()
        return True
    return False


def _safe_remove_work_directory(path: Path) -> None:
    work_root = _resolved(WORK_DIR)
    resolved = _resolved(path)
    if not _is_within(resolved, work_root) or resolved == work_root or path.is_symlink():
        raise ImportFailure("unsafe-cleanup-path")
    if path.exists():
        shutil.rmtree(path)


def _remove_stale_artifacts(current_artifact: Path, current_report: Path) -> None:
    for candidate in ARTIFACTS_DIR.glob("*.sqlite3"):
        if candidate != current_artifact:
            _safe_unlink(candidate, parent=ARTIFACTS_DIR)
    for candidate in REPORTS_DIR.glob("*.json"):
        if candidate != current_report:
            _safe_unlink(candidate, parent=REPORTS_DIR)


def _atomic_publish(
    staged_artifact: Path,
    staged_report: Path,
    staged_target: Path,
    paths: ImportPaths,
    fault_hook: FaultHook,
    after_publish: Callable[[], None],
) -> None:
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    destinations = (paths.artifact, paths.report, paths.target)
    staged = (staged_artifact, staged_report, staged_target)
    try:
        for destination in destinations:
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _chmod_private(destination.parent, directory=True)
            if destination.exists():
                backup = paths.work / f"backup-{destination.name}"
                shutil.copy2(destination, backup)
                _chmod_private(backup, directory=False)
                backups[destination] = backup
        for source, destination in zip(staged, destinations, strict=True):
            os.replace(source, destination)
            _chmod_private(destination, directory=False)
            published.append(destination)
            if destination == paths.report:
                fault_hook("during-publish")
        after_publish()
    except Exception:
        for destination in reversed(published):
            backup_path = backups.get(destination)
            if backup_path and backup_path.exists():
                os.replace(backup_path, destination)
            else:
                _safe_unlink(destination, parent=destination.parent)
        raise


class ReviewImporter:
    """Build and atomically publish a safe local content review database."""

    def __init__(self, *, fault_hook: FaultHook = _no_fault) -> None:
        self.fault_hook = fault_hook

    def run(self, config: ImportConfig) -> dict[str, Any]:
        environment = _assert_local_environment()
        if config.allow_repo_source_for_tests and environment != "test":
            raise ImportFailure("test-source-exception-refused")

        _prepare_private_directories()
        with _operation_lock():
            return self._run_locked(config)

    def _run_locked(self, config: ImportConfig) -> dict[str, Any]:
        paths = resolve_paths(config)
        before = fingerprint(paths.source)
        paths.work.mkdir(mode=0o700, parents=False, exist_ok=False)
        _chmod_private(paths.work, directory=True)
        staged_artifact = paths.work / "sanitized.sqlite3"
        staged_report = paths.work / "report.json"
        staged_target = paths.work / "review.sqlite3"

        try:
            with _private_umask():
                dataset, deny_counts = _build_sanitized_database(
                    paths.source,
                    staged_artifact,
                    self.fault_hook,
                )
                _assert_public_review_pages(staged_artifact, dataset)
                _assert_source_unchanged(paths.source, before)
                report = _report(config, paths, before, dataset, deny_counts)
                if config.dry_run:
                    self.fault_hook("before-publish")
                    _assert_source_unchanged(paths.source, before)
                    return report

                shutil.copy2(staged_artifact, staged_target)
                _chmod_private(staged_target, directory=False)
                if config.create_admin:
                    _create_synthetic_admin(staged_target, config.admin_password)
                _assert_final_database(staged_target, dataset, config.create_admin)
                _write_private_json(staged_report, report)
                self.fault_hook("before-publish")
                _assert_source_unchanged(paths.source, before)
                _atomic_publish(
                    staged_artifact,
                    staged_report,
                    staged_target,
                    paths,
                    self.fault_hook,
                    lambda: _assert_source_unchanged(paths.source, before),
                )
                _remove_stale_artifacts(paths.artifact, paths.report)
                return report
        except ImportFailure:
            _assert_source_unchanged(paths.source, before)
            raise
        except Exception:
            _assert_source_unchanged(paths.source, before)
            raise ImportFailure("internal") from None
        finally:
            _safe_remove_work_directory(paths.work)


def cleanup_snapshot(
    snapshot_id: str,
    *,
    include_target: bool = False,
    target_db: Path = DEFAULT_TARGET,
) -> dict[str, int]:
    _assert_local_environment()
    _validate_snapshot_id(snapshot_id)
    _prepare_private_directories()
    with _operation_lock():
        artifact = _resolved(ARTIFACTS_DIR) / f"{snapshot_id}.sqlite3"
        report = _resolved(REPORTS_DIR) / f"{snapshot_id}.json"
        removed = {
            "sanitized": int(_safe_unlink(artifact, parent=_resolved(ARTIFACTS_DIR))),
            "report": int(_safe_unlink(report, parent=_resolved(REPORTS_DIR))),
            "target": 0,
        }
        if include_target:
            target = _resolved(target_db)
            if (
                not _is_within(target, _resolved(PROJECT_ROOT))
                or not _is_gitignored(target)
                or target.suffix not in {".db", ".sqlite", ".sqlite3"}
            ):
                raise ImportFailure("unsafe-target-path")
            removed["target"] = int(_safe_unlink(target, parent=target.parent))
        return removed
