"""Import the CMP export's learner history into the database.

Step 4 of ``_docs/runbooks/production-data-migration.md`` has two halves.
``accounts.services.cmp_learner_import`` moves the accounts themselves; this
module moves everything that hangs off one -- course registrations, enrollments,
homework submissions and answers, project submissions, peer reviews, criteria
responses, evaluation scores and per-user Wrapped statistics.  Nine tables,
roughly half a million rows, and every one of them personal data.

Never import (a security boundary, not a style preference)
----------------------------------------------------------

:data:`FORBIDDEN_TABLES`.  A session, an OAuth account or token, or a management
API token never travels, whatever a future revision of this module does.
``socialaccount_socialtoken`` is named there even though the current export does
not contain it, so a future export cannot introduce it quietly.

Do **not** substitute ``review_import``'s ``SENSITIVE_TABLES`` here.  That list
builds a sanitized *review* database and excludes every table this importer
exists to move; using it would import nothing and report success.

Reconcile, never invent
-----------------------

``import_cmp_content`` writes cohorts, homework, questions, projects and review
criteria without keeping CMP's row ids, and ``import_cmp_learners`` writes the
accounts.  So every foreign key here is resolved against what those importers
already wrote, through :class:`Resolution`, built once per run from natural keys:

* a cohort by slug;
* a homework and a project by ``(cohort, slug)``;
* a question by its text within its homework -- and, where one homework repeats
  a question's text verbatim, by the order the texts appear, which is the order
  ``import_cmp_content`` writes them in;
* a review criterion by ``(cohort, description)``, unique within a course in the
  export;
* a registration campaign by slug, and a Wrapped year by year;
* an account through the claims file ``import_cmp_learners`` left behind;
* an enrollment, submission, project submission or peer review through this
  importer's own claims, written by the earlier stage of the same run.

A row whose parent does not resolve is **counted under a named bucket and
skipped**.  Nothing is invented to hang a child off: a placeholder cohort or a
stand-in account would turn a reportable gap into data that looks real.  The
buckets are in :data:`UNRESOLVED_BUCKETS` and every one of them is a count --
never a source value, because the source values here are learner answers, names
and addresses.

Order
-----

The tables are imported in :data:`TABLE_ORDER`, which is dependency order: a
submission cannot be written before the enrollment it belongs to exists, and an
answer cannot be written before its submission.  Each stage's claims are the
next stage's foreign keys.

Resumability
------------

Every table is walked in ascending source-id order in fixed-size batches, with
its high-water mark in ``courses.models.CmpHistoryImportProgress``.  A batch's
writes and its watermark advance share one transaction, so a process killed
mid-batch leaves that batch wholly rolled back and a re-run picks up from the
last committed one.  ``--status`` reports how far a run got without opening the
export at all.

Claim tracking
--------------

Which target row this importer created for a given CMP source id is script-owned
state, not a column on a live model -- the same rule
``accounts.services.cmp_learner_import`` follows, and for the same reason: a
source-system row id is provenance of a one-time import, and the permanent
schema carries only what the running application reads.

:class:`CmpHistoryClaims` keeps one JSON file per table under a claims directory,
so a batch flushes only the table it is working on rather than rewriting every
claim in the migration.  A file is written atomically (temp file plus
``os.replace``) *after* the transaction that created the claims commits, never
inside it: a JSON file cannot join a database transaction.  A process killed in
that window is still safe, because the watermark committed with the rows has
already advanced past those source ids, so an ordinary resume never revisits
them; and if the claims were lost outright, a re-run re-resolves each row's
natural key and *attaches* to the row already there (``rows_attached``) instead
of duplicating it, wherever the target model has a natural key to attach by.

Timestamps
----------

``created_at``/``enrollment_date``/``calculated_at`` are historical facts, so
they are restored from the export after the insert.  Django stamps
``auto_now_add`` and ``auto_now`` columns during the write itself, and the rows
go in through ``bulk_create`` -- which also, deliberately, bypasses the
``save()`` overrides that would otherwise invent a leaderboard name for an
enrollment or re-point a registration at its campaign's current cohort.  A row
is copied as the export has it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from django.db import models, transaction
from django.utils.dateparse import parse_datetime

from courses.models import (
    CmpHistoryImportProgress,
    Cohort,
    CourseRegistration,
    Enrollment,
    RegistrationCampaign,
)

__all__ = [
    "CONTENT_TABLES",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CLAIMS_DIRECTORY",
    "FORBIDDEN_TABLES",
    "LEARNER_TABLES",
    "READ_TABLES",
    "TABLE_ORDER",
    "UNRESOLVED_BUCKETS",
    "CmpHistoryClaims",
    "CmpHistoryImportError",
    "HistoryImportResult",
    "Resolution",
    "TableReport",
    "dry_run_counts",
    "import_cmp_learner_history",
    "progress_status",
]

DEFAULT_BATCH_SIZE = 2000
DEFAULT_CLAIMS_DIRECTORY = Path(".tmp/cmp_learner_history_claims")

# This importer's own never-import set. Deliberately not review_import's
# SENSITIVE_TABLES -- see the module docstring.
FORBIDDEN_TABLES = frozenset(
    {
        "django_session",
        "socialaccount_socialaccount",
        "socialaccount_socialapp",
        "socialaccount_socialapp_sites",
        "socialaccount_socialtoken",
        "accounts_token",
    }
)

# Dependency order: each stage's claims are the next stage's foreign keys.
TABLE_ORDER: tuple[str, ...] = (
    "courses_courseregistration",
    "courses_enrollment",
)

LEARNER_TABLES = frozenset(TABLE_ORDER)

# Read to resolve foreign keys, never copied: import_cmp_content owns these.
CONTENT_TABLES = frozenset(
    {
        "courses_course",
        "courses_homework",
        "courses_question",
        "courses_project",
        "courses_reviewcriteria",
        "courses_registrationcampaign",
        "courses_wrappedstatistics",
    }
)

READ_TABLES = LEARNER_TABLES | CONTENT_TABLES

# Every reason a source row can be skipped, so a report has a fixed shape and a
# reader can tell "none of these" from "this bucket did not exist yet".
UNRESOLVED_BUCKETS: tuple[str, ...] = (
    "campaign",
    "cohort",
    "criteria",
    "enrollment",
    "homework",
    "peer_review",
    "project",
    "project_submission",
    "question",
    "source_json_invalid",
    "submission",
    "user",
    "wrapped",
)


class CmpHistoryImportError(RuntimeError):
    """A fail-closed refusal that never renders a source value."""


def _refuse(code: str) -> NoReturn:
    raise CmpHistoryImportError(code)


class _Unresolved(Exception):
    """A parent row this import will not invent. Carries a bucket, never a value."""

    def __init__(self, bucket: str) -> None:
        if bucket not in UNRESOLVED_BUCKETS:  # pragma: no cover - a code-shape guard
            _refuse("unresolved-bucket-unknown")
        super().__init__(bucket)
        self.bucket = bucket


@dataclass(slots=True)
class CmpHistoryClaims:
    """This importer's own durable "CMP source id -> target pk" record, per table.

    One file per table, so a batch rewrites only the table it is working on.
    Not thread- or process-safe for concurrent writers: this is a single-threaded
    one-time script, the same assumption ``CmpHistoryImportProgress`` makes.
    """

    directory: Path
    _tables: dict[str, dict[int, int]] = field(default_factory=dict)

    def _path(self, table: str) -> Path:
        return self.directory / f"{table}.json"

    def table(self, table: str) -> dict[int, int]:
        cached = self._tables.get(table)
        if cached is not None:
            return cached
        try:
            raw_text = self._path(table).read_text(encoding="utf-8")
        except FileNotFoundError:
            loaded: dict[int, int] = {}
        except OSError:
            _refuse("claims-file-unreadable")
        else:
            try:
                raw = json.loads(raw_text)
            except json.JSONDecodeError:
                _refuse("claims-file-malformed")
            if not isinstance(raw, dict):
                _refuse("claims-file-malformed")
            try:
                loaded = {int(key): int(value) for key, value in raw.items()}
            except (TypeError, ValueError):
                _refuse("claims-file-malformed")
        self._tables[table] = loaded
        return loaded

    def record(self, table: str, source_id: int, target_id: int) -> None:
        self.table(table)[source_id] = target_id

    def flush(self, table: str) -> None:
        payload = json.dumps(
            {str(source_id): target_id for source_id, target_id in self.table(table).items()},
            sort_keys=True,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(
            dir=self.directory, prefix=f".{table}-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, self._path(table))
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def counts(self) -> dict[str, int]:
        return {table: len(self.table(table)) for table in TABLE_ORDER}


def _readonly(source_db: Path) -> sqlite3.Connection:
    try:
        resolved = source_db.expanduser().resolve(strict=True)
    except OSError:
        _refuse("source-unreadable")
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _assert_forbidden_tables_untouched() -> None:
    if READ_TABLES & FORBIDDEN_TABLES:  # pragma: no cover - a code-shape guard
        _refuse("forbidden-table-boundary-violated")


def _rows(
    connection: sqlite3.Connection, table: str, sql: str, parameters: tuple = ()
) -> list[Any]:
    if table not in READ_TABLES:  # pragma: no cover - a code-shape guard
        _refuse("forbidden-table-boundary-violated")
    try:
        return list(connection.execute(sql, parameters))
    except sqlite3.Error:
        _refuse("source-query-failed")


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(_rows(connection, table, f"select count(*) from {table}")[0][0])  # noqa: S608


def _text(value: Any) -> str:
    """A NOT NULL ``blank=True`` column: a source NULL becomes "", never None."""

    return "" if value is None else str(value)


def _moment(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = parse_datetime(str(value).replace(" ", "T", 1))
    if parsed is None:
        _refuse("source-datetime-invalid")
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Resolution:
    """CMP source id -> target primary key, for every parent this import needs.

    Built once per run from natural keys, because ``import_cmp_content`` and
    ``import_cmp_learners`` keep no CMP row id on the rows they write.  A source
    id absent from a map is a parent the target database does not hold; the child
    row is reported under its bucket, never given an invented parent.
    """

    users: Mapping[int, int]
    cohorts: Mapping[int, int]
    campaigns: Mapping[int, int]

    def user(self, source_id: Any) -> int:
        resolved = self.users.get(source_id)
        if resolved is None:
            raise _Unresolved("user")
        return resolved

    def cohort(self, source_id: Any) -> int:
        resolved = self.cohorts.get(source_id)
        if resolved is None:
            raise _Unresolved("cohort")
        return resolved

    def campaign(self, source_id: Any) -> int:
        resolved = self.campaigns.get(source_id)
        if resolved is None:
            raise _Unresolved("campaign")
        return resolved


def _cohort_map(connection: sqlite3.Connection) -> dict[int, int]:
    local = dict(Cohort.objects.values_list("slug", "pk"))
    resolved = {}
    for row in _rows(connection, "courses_course", "select id, slug from courses_course"):
        target = local.get(str(row["slug"]))
        if target is not None:
            resolved[int(row["id"])] = target
    return resolved


def _campaign_map(connection: sqlite3.Connection) -> dict[int, int]:
    local = dict(RegistrationCampaign.objects.values_list("slug", "pk"))
    resolved = {}
    rows = _rows(
        connection,
        "courses_registrationcampaign",
        "select id, slug from courses_registrationcampaign",
    )
    for row in rows:
        target = local.get(str(row["slug"]))
        if target is not None:
            resolved[int(row["id"])] = target
    return resolved


def _build_resolution(connection: sqlite3.Connection, *, users: Mapping[int, int]) -> Resolution:
    return Resolution(
        users=users,
        cohorts=_cohort_map(connection),
        campaigns=_campaign_map(connection),
    )


def _course_registration(row: Any, resolution: Resolution) -> CourseRegistration:
    """A CMP registration, copied as the export has it.

    ``course`` and ``user`` are nullable in the export and stay so here; a
    *present* one that does not resolve is a skipped row, not a silent NULL.
    ``email_normalized`` is set explicitly because ``bulk_create`` skips the
    ``save()`` that would otherwise compute it -- and would also re-point a
    registration with no cohort at its campaign's current one, inventing a
    cohort choice the export did not record.
    """

    email = _text(row["email"])
    return CourseRegistration(
        campaign_id=resolution.campaign(row["campaign_id"]),
        course_id=None if row["course_id"] is None else resolution.cohort(row["course_id"]),
        user_id=None if row["user_id"] is None else resolution.user(row["user_id"]),
        email=email,
        email_normalized=email.strip().lower(),
        name=_text(row["name"]),
        company_name=_text(row["company_name"]),
        country=_text(row["country"]),
        region=_text(row["region"]),
        role=_text(row["role"]),
        comment=_text(row["comment"]),
        accepted_newsletter=bool(row["accepted_newsletter"]),
        created_at=_moment(row["created_at"]),
        updated_at=_moment(row["updated_at"]),
    )


def _course_registration_key(instance: CourseRegistration) -> dict[str, Any]:
    return {
        "email_normalized": instance.email_normalized,
        "campaign_id": instance.campaign_id,
    }


def _enrollment(row: Any, resolution: Resolution) -> Enrollment:
    """A CMP enrollment, certificate URL included.

    There is no certificate table in the export: 2,636 certificates exist only
    as ``courses_enrollment.certificate_url``, so dropping that column would
    silently destroy them.
    """

    return Enrollment(
        student_id=resolution.user(row["student_id"]),
        course_id=resolution.cohort(row["course_id"]),
        enrollment_date=_moment(row["enrollment_date"]),
        display_name=_text(row["display_name"]),
        display_on_leaderboard=bool(row["display_on_leaderboard"]),
        display_public_profile=bool(row["display_public_profile"]),
        position_on_leaderboard=row["position_on_leaderboard"],
        certificate_name=row["certificate_name"],
        total_score=int(row["total_score"] or 0),
        certificate_url=row["certificate_url"],
        disable_learning_in_public=bool(row["disable_learning_in_public"]),
    )


def _enrollment_key(instance: Enrollment) -> dict[str, Any]:
    return {"student_id": instance.student_id, "course_id": instance.course_id}


@dataclass(frozen=True, slots=True)
class TablePlan:
    """How one source table becomes target rows.

    ``build`` raises :class:`_Unresolved` rather than inventing a parent.

    ``natural_key`` returns the lookup that identifies a target row uniquely, for
    the tables that have one.  It does two jobs: a replay that lost its claims
    attaches to the row already there instead of refusing on a unique
    constraint, and two source rows that resolve to the same target row inside
    one batch collapse onto it rather than colliding.  The second is not
    hypothetical -- ``import_cmp_learners`` folds two CMP accounts onto one
    target account whenever another importer wrote it first, and their two CMP
    enrollments in one cohort are then the same person's single enrollment.  A
    table with no natural key leaves this ``None``.

    Its **first** key is the selective one, and the batch's existing rows are
    found through a single ``__in`` on it -- one query per batch rather than one
    per row, which is what keeps a 218,577-row table a few hundred queries.

    ``stamps`` names the columns Django would overwrite with the import's own
    clock and this importer restores from the export afterwards.
    """

    table: str
    model: type[models.Model]
    build: Callable[[Any, Resolution], Any]
    natural_key: Callable[[Any], dict[str, Any]] | None = None
    stamps: tuple[str, ...] = ()


TABLE_PLANS: tuple[TablePlan, ...] = (
    TablePlan(
        table="courses_courseregistration",
        model=CourseRegistration,
        build=_course_registration,
        natural_key=_course_registration_key,
        stamps=("created_at", "updated_at"),
    ),
    TablePlan(
        table="courses_enrollment",
        model=Enrollment,
        build=_enrollment,
        natural_key=_enrollment_key,
        stamps=("enrollment_date",),
    ),
)


@dataclass(frozen=True, slots=True)
class TableReport:
    table: str
    source_total: int
    created: int
    attached: int
    skipped: int
    unresolved: Mapping[str, int]
    last_source_id: int
    completed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "source_total": self.source_total,
            "created": self.created,
            "attached": self.attached,
            "skipped": self.skipped,
            # Counts only. A source value here would be a learner's answer.
            "unresolved": {name: count for name, count in sorted(self.unresolved.items()) if count},
            "unresolved_total": sum(self.unresolved.values()),
            "last_source_id": self.last_source_id,
            "completed": self.completed,
        }


@dataclass(frozen=True, slots=True)
class HistoryImportResult:
    tables: tuple[TableReport, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "tables": [report.as_dict() for report in self.tables],
            "created_total": sum(report.created for report in self.tables),
            "attached_total": sum(report.attached for report in self.tables),
            "skipped_total": sum(report.skipped for report in self.tables),
            "unresolved_total": sum(sum(report.unresolved.values()) for report in self.tables),
            "applied": True,
        }


def _get_progress(table: str) -> CmpHistoryImportProgress:
    progress, _ = CmpHistoryImportProgress.objects.get_or_create(table=table)
    return progress


def _save_progress(progress: CmpHistoryImportProgress) -> None:
    progress.save(
        update_fields=[
            "last_source_id",
            "rows_created",
            "rows_attached",
            "rows_skipped",
            "unresolved",
            "completed",
            "updated_at",
        ]
    )


def _report(progress: CmpHistoryImportProgress, source_total: int) -> TableReport:
    return TableReport(
        table=progress.table,
        source_total=source_total,
        created=progress.rows_created,
        attached=progress.rows_attached,
        skipped=progress.rows_skipped,
        unresolved=dict(progress.unresolved),
        last_source_id=progress.last_source_id,
        completed=progress.completed,
    )


def _existing_rows(plan: TablePlan, lookups: list[dict[str, Any]]) -> dict[tuple, int]:
    """The target rows a batch's natural keys already match, in one query.

    The lookup's first field is the selective one, so ``field__in`` narrows the
    scan to this batch and the exact composite match is made in Python.  A query
    per row would be a quarter of a million round trips for the answer table
    alone.
    """

    if not lookups:
        return {}
    fields = list(lookups[0])
    prefilter = fields[0]
    wanted = {tuple(lookup[name] for name in fields) for lookup in lookups}
    found: dict[tuple, int] = {}
    rows = plan.model.objects.filter(
        **{f"{prefilter}__in": {lookup[prefilter] for lookup in lookups}}
    ).values_list(*fields, "pk")
    for row in rows:
        key = tuple(row[:-1])
        if key in wanted:
            found[key] = int(row[-1])
    return found


def _restore_stamps(plan: TablePlan, created: Iterable[Any], stamps: list[dict[str, Any]]) -> None:
    """Put the export's own timestamps back on rows Django just clock-stamped.

    ``auto_now_add`` and ``auto_now`` columns are written during the insert, so
    the historical value has to be reapplied.  ``QuerySet.update`` is the way to
    do it: it bypasses ``pre_save``, which is exactly what overwrote them.
    """

    if not plan.stamps:
        return
    for instance, values in zip(created, stamps, strict=True):
        plan.model.objects.filter(pk=instance.pk).update(**values)


def _import_table(
    connection: sqlite3.Connection,
    plan: TablePlan,
    *,
    batch_size: int,
    claims: CmpHistoryClaims,
    resolution: Resolution,
) -> TableReport:
    source_total = _count(connection, plan.table)
    progress = _get_progress(plan.table)
    claimed = claims.table(plan.table)
    while not progress.completed:
        rows = _rows(
            connection,
            plan.table,
            f"select * from {plan.table} where id > ? order by id limit ?",  # noqa: S608
            (progress.last_source_id, batch_size),
        )
        if not rows:
            progress.completed = True
            _save_progress(progress)
            claims.flush(plan.table)
            break
        pending: list[tuple[int, Any]] = []
        stamps: list[dict[str, Any]] = []
        # Source rows that resolved onto a target row this import is not
        # creating: one already in the database, or one earlier in this batch.
        attached_to_row: list[tuple[int, int]] = []
        attached_to_pending: list[tuple[int, int]] = []
        pending_by_key: dict[tuple, int] = {}
        skipped = 0
        unresolved: Counter[str] = Counter()
        with transaction.atomic():
            candidates: list[tuple[int, Any, dict[str, Any] | None]] = []
            for row in rows:
                source_id = int(row["id"])
                if source_id in claimed:
                    skipped += 1
                    continue
                try:
                    instance = plan.build(row, resolution)
                except _Unresolved as gap:
                    unresolved[gap.bucket] += 1
                    continue
                lookup = plan.natural_key(instance) if plan.natural_key is not None else None
                candidates.append((source_id, instance, lookup))
            existing = _existing_rows(
                plan, [lookup for _, _, lookup in candidates if lookup is not None]
            )
            for source_id, instance, lookup in candidates:
                if lookup is not None:
                    key = tuple(lookup.values())
                    already = existing.get(key)
                    if already is not None:
                        attached_to_row.append((source_id, already))
                        continue
                    if key in pending_by_key:
                        attached_to_pending.append((source_id, pending_by_key[key]))
                        continue
                    pending_by_key[key] = len(pending)
                stamps.append({name: getattr(instance, name) for name in plan.stamps})
                pending.append((source_id, instance))
            created = plan.model.objects.bulk_create([instance for _, instance in pending])
            _restore_stamps(plan, created, stamps)
            progress.last_source_id = int(rows[-1]["id"])
            progress.rows_created += len(created)
            progress.rows_attached += len(attached_to_row) + len(attached_to_pending)
            progress.rows_skipped += skipped
            progress.unresolved = dict(Counter(progress.unresolved) + unresolved)
            _save_progress(progress)
        # Only after the transaction above has really committed: a JSON file
        # cannot join it, so the order is what makes a killed run recoverable.
        for (source_id, _unsaved), instance in zip(pending, created, strict=True):
            claims.record(plan.table, source_id, int(instance.pk))
        for source_id, index in attached_to_pending:
            claims.record(plan.table, source_id, int(created[index].pk))
        for source_id, target_id in attached_to_row:
            claims.record(plan.table, source_id, target_id)
        claims.flush(plan.table)
    return _report(progress, source_total)


def import_cmp_learner_history(
    source: Path,
    *,
    user_claims: Mapping[int, int],
    batch_size: int = DEFAULT_BATCH_SIZE,
    claims_directory: Path = DEFAULT_CLAIMS_DIRECTORY,
    tables: Iterable[str] | None = None,
) -> HistoryImportResult:
    """Import the CMP export's learner history. Safe to kill and re-run."""

    _assert_forbidden_tables_untouched()
    wanted = set(tables) if tables is not None else set(TABLE_ORDER)
    unknown = wanted - LEARNER_TABLES
    if unknown:
        _refuse("table-not-in-this-import")
    claims = CmpHistoryClaims(directory=claims_directory)
    connection = _readonly(source)
    try:
        resolution = _build_resolution(connection, users=user_claims)
        reports = [
            _import_table(
                connection,
                plan,
                batch_size=batch_size,
                claims=claims,
                resolution=resolution,
            )
            for plan in TABLE_PLANS
            if plan.table in wanted
        ]
    finally:
        connection.close()
    return HistoryImportResult(tables=tuple(reports))


def dry_run_counts(
    source: Path, *, claims_directory: Path = DEFAULT_CLAIMS_DIRECTORY
) -> dict[str, Any]:
    """Report source and already-claimed counts. Writes nothing."""

    _assert_forbidden_tables_untouched()
    claims = CmpHistoryClaims(directory=claims_directory)
    connection = _readonly(source)
    try:
        rows = {table: _count(connection, table) for table in TABLE_ORDER}
    finally:
        connection.close()
    already = claims.counts()
    return {
        "tables": [
            {"table": table, "source_total": rows[table], "already_claimed": already[table]}
            for table in TABLE_ORDER
        ],
        "source_total": sum(rows.values()),
        "applied": False,
    }


def progress_status(*, claims_directory: Path = DEFAULT_CLAIMS_DIRECTORY) -> dict[str, Any]:
    """Report accumulated progress without touching the source export."""

    stored = {row.table: row for row in CmpHistoryImportProgress.objects.all()}
    claims = CmpHistoryClaims(directory=claims_directory)
    return {
        "progress": [
            {
                "table": table,
                "last_source_id": stored[table].last_source_id,
                "created": stored[table].rows_created,
                "attached": stored[table].rows_attached,
                "skipped": stored[table].rows_skipped,
                "unresolved": {
                    name: count for name, count in sorted(stored[table].unresolved.items()) if count
                },
                "completed": stored[table].completed,
                "updated_at": stored[table].updated_at.isoformat(),
                "claims_recorded": claims.counts()[table],
            }
            for table in TABLE_ORDER
            if table in stored
        ],
        "not_started": [table for table in TABLE_ORDER if table not in stored],
    }
