"""Import CMP-export learner accounts into the database.

Reads a CMP production export read-only and writes the two learner-account tables
that belong to the accounts domain: ``accounts_customuser`` (20,009 rows, measured)
and ``account_emailaddress`` (20,005 rows for those same accounts, measured). It is
the "learner/account layer" of the production migration's step 4 -- see
``_docs/runbooks/production-data-migration.md`` -- not the enrollment, submission,
answer and review tables that hang off an account; those belong to a separate
importer that reconciles against the cohorts and homework ``import_cmp_content``
already wrote, and are out of scope here.

Every imported account arrives:

* with its real email address, copied verbatim from the export's ``email`` column
  (never rewritten, never case-folded -- ``CustomUser.save()`` computes
  ``normalized_email`` on its own);
* with ``set_unusable_password()`` called -- no password hash travels, regardless
  of what the export carries;
* with ``is_staff=False`` and ``is_superuser=False``, unconditionally. The export
  carries five superuser/staff rows; copying that column, together with a usable
  password hash, is the one combination that would grant production administrator
  rights by import. Staff access is granted afterwards, through Studio, to named
  people;
* with no ``SocialAccount`` row. OAuth linking happens at sign-in time, through
  ``accounts.auth.ConsolidatingSocialAccountAdapter``, never at import time;
* with ``identity_state`` left at its model default, ``legacy``.

Never import (hard security boundary, not a style preference)
----------------------------------------------------------------

``FORBIDDEN_TABLES`` below. This importer never reads a session, an OAuth token or
credential, or a management API token, no matter what future revision touches this
module. Do not reuse ``review_import``'s ``SENSITIVE_TABLES`` here: that allowlist
exists for a different purpose (a sanitized *review* database) and excludes the
entire learner payload this importer exists to move -- reusing it would import
nothing and report success.

Why ``account_emailaddress`` is imported at all
-------------------------------------------------

It is not load-bearing for sign-in: ``ConsolidatingSocialAccountAdapter`` matches a
provider-verified address against a verified ``EmailAddress`` row *or* the
account's own ``email`` column, precisely because the export has four accounts with
no email row at all. But it **is** load-bearing for reconciliation --
``accounts/reconciliation.py`` reads verified ``EmailAddress`` rows in several
places, including a hard gate in ``_mapping_conflicts``: a merge is refused outright
if either side of a proposed pair holds no verified row at all
(``verified_email_evidence_missing``). ``sociallogin.connect()`` never creates one on
its own (it calls allauth's ``save(connect=True)``, which skips
``setup_user_email``), so an account imported without one stays permanently
unmergeable, not self-healing.

So this importer does two things for email addresses:

1. Import the raw ``account_emailaddress`` rows, faithfully -- ``verified`` and
   ``primary`` copied as the export has them, including the one unverified row.
2. For an account the export gives no email row at all, synthesise one verified,
   primary ``EmailAddress`` from the account's own ``email`` column, so that
   account is not permanently excluded from the merge gate above. This can
   collide with allauth's own ``unique_verified_email`` constraint (at most one
   *verified* row may exist for a given address, site-wide) -- the export's one
   known same-address pair is exactly this shape: one of the two accounts (the
   one the export gives zero email rows) shares its address with the other, which
   already holds a verified row for it. That collision is not resolved here --
   this importer does not merge accounts, it imports both and reports the
   collision by source id so the existing reconciliation mechanism can find it.

Resumability
------------

Both tables, and the synthesis pass, are processed in ascending source-id order,
in fixed-size batches, tracked in ``accounts.models.CmpLearnerImportProgress``.
Each batch's writes and its watermark advance happen inside one transaction, so a
process killed mid-batch leaves nothing partially written for the next run to
double-count; a re-run's first query is ``id > last_source_id``, so it does not
re-scan rows it already committed. ``CustomUser.cmp_source_user_id`` is the
belt-and-braces check within a batch: a row already carrying the source id being
processed is skipped and counted, not re-created, regardless of what the watermark
says.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from allauth.account.models import EmailAddress
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from accounts.models import CmpLearnerImportProgress, CustomUser

__all__ = [
    "CmpLearnerImportError",
    "DEFAULT_BATCH_SIZE",
    "FORBIDDEN_TABLES",
    "LearnerImportPhaseReport",
    "LearnerImportResult",
    "dry_run_counts",
    "import_cmp_learners",
    "progress_status",
]

DEFAULT_BATCH_SIZE = 500

# This importer's own forbidden-table set. Deliberately not
# ``review_import.SENSITIVE_TABLES`` -- see the module docstring.
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

# The only tables this importer ever queries. Checked against FORBIDDEN_TABLES
# below so the boundary is a property of the code, not of the SQL that happens to
# be written beneath it.
READ_TABLES = frozenset({"accounts_customuser", "account_emailaddress"})

ACCOUNTS_TABLE = "accounts_customuser"
EMAIL_ADDRESS_TABLE = "account_emailaddress"
SYNTHESIZED_EMAIL_ADDRESS_TABLE = "account_emailaddress_synthesized"

# Columns that exist on both the export's accounts_customuser and CustomUser with
# the same meaning. password, is_staff, is_superuser, username and email are
# handled explicitly, never through this list -- see _build_account.
_ACCOUNT_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "is_active",
    "role",
    "certificate_name",
    "dark_mode",
    "about_me",
    "github_url",
    "linkedin_url",
    "personal_website_url",
    "country",
    "region",
    "registration_role",
    "preferred_timezone",
)
_BOOLEAN_ACCOUNT_FIELDS = frozenset({"is_active", "dark_mode"})
_DATETIME_ACCOUNT_FIELDS = frozenset({"date_joined", "last_login"})
# CharFields without null=True: a NULL from the export must become "", never None.
_TEXT_DEFAULT_FIELDS = frozenset(
    {"first_name", "last_name", "country", "region", "registration_role"}
)
# Fields that do allow null=True: a NULL from the export may stay None.
_NULLABLE_TEXT_FIELDS = frozenset(
    {"certificate_name", "about_me", "github_url", "linkedin_url", "personal_website_url"}
)

_USERNAME_SANITIZE_RE = re.compile(r"[^\w.@+-]")
_MAX_USERNAME_LENGTH = 150


class CmpLearnerImportError(RuntimeError):
    """A fail-closed refusal that never renders a source value (an email, a name)."""


def _refuse(code: str) -> NoReturn:
    raise CmpLearnerImportError(code)


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


def _count(connection: sqlite3.Connection, table: str) -> int:
    if table not in READ_TABLES:  # pragma: no cover - a code-shape guard
        _refuse("forbidden-table-boundary-violated")
    return connection.execute(f"select count(*) from {table}").fetchone()[0]  # noqa: S608


@dataclass(frozen=True, slots=True)
class LearnerImportPhaseReport:
    table: str
    source_total: int
    written: int
    skipped: int
    last_source_id: int
    completed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "source_total": self.source_total,
            "written": self.written,
            "skipped": self.skipped,
            "last_source_id": self.last_source_id,
            "completed": self.completed,
        }


@dataclass(frozen=True, slots=True)
class LearnerImportResult:
    accounts: LearnerImportPhaseReport
    email_addresses: LearnerImportPhaseReport
    synthesized_email_addresses: LearnerImportPhaseReport
    synthesis_skipped_collisions: tuple[int, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, Any]:
        return {
            "accounts": self.accounts.as_dict(),
            "email_addresses": self.email_addresses.as_dict(),
            "synthesized_email_addresses": self.synthesized_email_addresses.as_dict(),
            # Source accounts_customuser ids only -- never an email. These are the
            # accounts the export gave no email row at all, whose address is
            # already claimed by another account's verified row; they stay
            # without one, for the existing reconciliation mechanism to find.
            "synthesis_skipped_collisions": list(self.synthesis_skipped_collisions),
            "applied": True,
        }


def _get_progress(table: str) -> CmpLearnerImportProgress:
    progress, _ = CmpLearnerImportProgress.objects.get_or_create(table=table)
    return progress


def _save_progress(progress: CmpLearnerImportProgress) -> None:
    progress.save(
        update_fields=["last_source_id", "rows_written", "rows_skipped", "completed", "updated_at"]
    )


def _phase_report(
    progress: CmpLearnerImportProgress, source_total: int
) -> LearnerImportPhaseReport:
    return LearnerImportPhaseReport(
        table=progress.table,
        source_total=source_total,
        written=progress.rows_written,
        skipped=progress.rows_skipped,
        last_source_id=progress.last_source_id,
        completed=progress.completed,
    )


def _username_candidate(email: str) -> str:
    local_part = email.split("@", 1)[0] if email else ""
    sanitized = _USERNAME_SANITIZE_RE.sub("_", local_part).strip("_")
    return (sanitized or "learner")[: _MAX_USERNAME_LENGTH - 6]


def _unique_username(preferred: str) -> str:
    base = (preferred or "learner")[:_MAX_USERNAME_LENGTH]
    candidate = base
    suffix = 1
    while CustomUser.objects.filter(username=candidate).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"[:_MAX_USERNAME_LENGTH]
    return candidate


def _resolve_username(row: sqlite3.Row) -> str:
    source_username = (row["username"] or "").strip()
    preferred = source_username or _username_candidate((row["email"] or "").strip())
    return _unique_username(preferred)


def _build_account(row: sqlite3.Row) -> CustomUser:
    email = (row["email"] or "").strip()
    user = CustomUser(
        username=_resolve_username(row),
        email=email,
        is_staff=False,
        is_superuser=False,
        cmp_source_user_id=row["id"],
    )
    columns = row.keys()
    for name in _ACCOUNT_FIELDS:
        if name not in columns:
            continue
        value = row[name]
        if name in _BOOLEAN_ACCOUNT_FIELDS:
            value = bool(value)
        elif name == "role":
            valid_roles = {choice for choice, _label in CustomUser.ROLE_CHOICES}
            value = value if value in valid_roles else CustomUser._meta.get_field("role").default
        elif value is None and name in _TEXT_DEFAULT_FIELDS:
            value = ""
        elif value is None and name not in _NULLABLE_TEXT_FIELDS:
            continue  # leave the model default in place rather than guess
        setattr(user, name, value)
    for name in _DATETIME_ACCOUNT_FIELDS:
        if name not in columns:
            continue
        raw = row[name]
        setattr(user, name, parse_datetime(raw) if raw else None)
    user.set_unusable_password()
    return user


def _import_accounts(
    connection: sqlite3.Connection, *, batch_size: int
) -> LearnerImportPhaseReport:
    source_total = _count(connection, ACCOUNTS_TABLE)
    progress = _get_progress(ACCOUNTS_TABLE)
    while not progress.completed:
        rows = connection.execute(
            "select * from accounts_customuser where id > ? order by id limit ?",
            (progress.last_source_id, batch_size),
        ).fetchall()
        if not rows:
            progress.completed = True
            _save_progress(progress)
            break
        with transaction.atomic():
            written = 0
            skipped = 0
            max_id = progress.last_source_id
            for row in rows:
                max_id = max(max_id, row["id"])
                if CustomUser.objects.filter(cmp_source_user_id=row["id"]).exists():
                    skipped += 1
                    continue
                _build_account(row).save()
                written += 1
            progress.last_source_id = max_id
            progress.rows_written += written
            progress.rows_skipped += skipped
            _save_progress(progress)
    return _phase_report(progress, source_total)


def _import_email_addresses(
    connection: sqlite3.Connection, *, batch_size: int
) -> LearnerImportPhaseReport:
    source_total = _count(connection, EMAIL_ADDRESS_TABLE)
    progress = _get_progress(EMAIL_ADDRESS_TABLE)
    while not progress.completed:
        rows = connection.execute(
            "select * from account_emailaddress where id > ? order by id limit ?",
            (progress.last_source_id, batch_size),
        ).fetchall()
        if not rows:
            progress.completed = True
            _save_progress(progress)
            break
        with transaction.atomic():
            written = 0
            skipped = 0
            max_id = progress.last_source_id
            for row in rows:
                max_id = max(max_id, row["id"])
                email = (row["email"] or "").strip()
                if not email:
                    skipped += 1
                    continue
                try:
                    user = CustomUser.objects.get(cmp_source_user_id=row["user_id"])
                except CustomUser.DoesNotExist:
                    skipped += 1
                    continue
                if EmailAddress.objects.filter(user=user, email=email).exists():
                    skipped += 1
                    continue
                primary = bool(row["primary"])
                if primary and EmailAddress.objects.filter(user=user, primary=True).exists():
                    # Defensive only: the export is 1:1 (20,005 rows for 20,009
                    # accounts, none with more than one), so this should never
                    # trigger. If a future export ever carries two rows for one
                    # account, this keeps unique_primary_email from refusing the
                    # whole batch over a flag that is secondary to `verified`.
                    primary = False
                try:
                    with transaction.atomic():
                        EmailAddress.objects.create(
                            user=user,
                            email=email,
                            verified=bool(row["verified"]),
                            primary=primary,
                        )
                    written += 1
                except IntegrityError:
                    skipped += 1
            progress.last_source_id = max_id
            progress.rows_written += written
            progress.rows_skipped += skipped
            _save_progress(progress)
    return _phase_report(progress, source_total)


def _synthesize_missing_email_addresses(
    *, batch_size: int
) -> tuple[LearnerImportPhaseReport, tuple[int, ...]]:
    progress = _get_progress(SYNTHESIZED_EMAIL_ADDRESS_TABLE)
    source_total = (
        CustomUser.objects.filter(
            cmp_source_user_id__isnull=False,
        )
        .exclude(pk__in=EmailAddress.objects.values("user_id"))
        .exclude(email="")
        .count()
    )
    collisions: list[int] = []
    while not progress.completed:
        batch = list(
            CustomUser.objects.filter(
                cmp_source_user_id__isnull=False,
                cmp_source_user_id__gt=progress.last_source_id,
            )
            .exclude(pk__in=EmailAddress.objects.values("user_id"))
            .exclude(email="")
            .order_by("cmp_source_user_id")[:batch_size]
        )
        if not batch:
            progress.completed = True
            _save_progress(progress)
            break
        with transaction.atomic():
            written = 0
            skipped = 0
            max_id = progress.last_source_id
            for user in batch:
                max_id = max(max_id, user.cmp_source_user_id)
                email = user.email.strip()
                try:
                    with transaction.atomic():
                        EmailAddress.objects.create(
                            user=user, email=email, verified=True, primary=True
                        )
                    written += 1
                except IntegrityError:
                    # Another account already holds a *verified* row for this
                    # exact address (allauth's unique_verified_email). This is
                    # the known same-address pair's shape: reported, not forced
                    # -- see the module docstring.
                    skipped += 1
                    collisions.append(user.cmp_source_user_id)
            progress.last_source_id = max_id
            progress.rows_written += written
            progress.rows_skipped += skipped
            _save_progress(progress)
    return _phase_report(progress, source_total), tuple(collisions)


def dry_run_counts(source: Path) -> dict[str, Any]:
    """Report counts without writing anything."""

    _assert_forbidden_tables_untouched()
    connection = _readonly(source)
    try:
        accounts_total = _count(connection, ACCOUNTS_TABLE)
        emails_total = _count(connection, EMAIL_ADDRESS_TABLE)
    finally:
        connection.close()
    already_imported = CustomUser.objects.filter(cmp_source_user_id__isnull=False).count()
    already_imported_emails = EmailAddress.objects.filter(
        user__cmp_source_user_id__isnull=False
    ).count()
    return {
        "accounts_in_source": accounts_total,
        "account_emailaddress_in_source": emails_total,
        "accounts_already_imported": already_imported,
        "account_emailaddress_already_imported": already_imported_emails,
        "applied": False,
    }


def progress_status() -> dict[str, Any]:
    """Report accumulated progress without touching the source export."""

    rows = {
        row.table: {
            "last_source_id": row.last_source_id,
            "rows_written": row.rows_written,
            "rows_skipped": row.rows_skipped,
            "completed": row.completed,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in CmpLearnerImportProgress.objects.filter(
            table__in=(ACCOUNTS_TABLE, EMAIL_ADDRESS_TABLE, SYNTHESIZED_EMAIL_ADDRESS_TABLE)
        )
    }
    return {"progress": rows}


def import_cmp_learners(
    source: Path, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> LearnerImportResult:
    """Import the CMP export's learner accounts. Safe to kill and re-run."""

    _assert_forbidden_tables_untouched()
    connection = _readonly(source)
    try:
        accounts_report = _import_accounts(connection, batch_size=batch_size)
        email_report = _import_email_addresses(connection, batch_size=batch_size)
    finally:
        connection.close()
    synthesized_report, collisions = _synthesize_missing_email_addresses(batch_size=batch_size)
    return LearnerImportResult(
        accounts=accounts_report,
        email_addresses=email_report,
        synthesized_email_addresses=synthesized_report,
        synthesis_skipped_collisions=collisions,
    )
