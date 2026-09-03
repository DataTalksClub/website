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

``CustomUser`` itself carries no trace of any of this -- see "Claim tracking"
below for where the CMP source id actually lives.

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
``scripts.prod.account_reconciliation`` reads verified ``EmailAddress`` rows in several
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

Claim tracking -- script-owned, not a field on ``CustomUser``
----------------------------------------------------------------

Every function below that needs "which ``CustomUser`` did this importer already
create or attach for CMP source id N" answers it through :class:`CmpClaimsStore`,
never through a column on ``CustomUser``. The live model carries only what the
running application actually reads; a source-system row id is provenance, and
provenance belongs to the one-time import, not the permanent schema -- the same
principle ``_docs/runbooks/ingest-script-inventory.md`` states for every source in
this migration. (An earlier revision of this importer *did* carry the id as
``CustomUser.cmp_source_user_id``, with a ``UniqueConstraint``; that field is gone,
along with the migration that added it, once every caller here moved to the claims
store below.)

The store is a flat ``{"<cmp_source_id>": <user_pk>, ...}`` JSON object, held in
memory during a run and rewritten to disk -- atomically, a temp file plus
``os.replace`` -- immediately after each committed batch in :func:`_import_accounts`,
the only phase that adds claims. It is not scratch: it is this importer's own
durable resumability state, the same role ``CmpLearnerImportProgress`` plays for
the per-table watermark, just script-owned file state instead of a database row
(see ``accounts.models.CmpLearnerImportProgress`` -- that table is unaffected by
this change; it is not a field on a live domain model, so the same principle that
moved the source id off ``CustomUser`` does not ask it to move).

Writing the claims file happens *after* the database transaction that created the
claim commits, never before and never as part of it -- a JSON file cannot join a
database transaction. A process killed in the narrow window between the two is the
one case this design does not make byte-for-byte atomic with the database. It is
still safe: the watermark (committed atomically with the row, inside the same
transaction) has already advanced past that source id, so an ordinary resume never
revisits it. Only a *second*, independent fault -- the watermark itself lost or
reset -- would cause this importer to reconsider that source id, and even then the
outcome is a safe idempotent re-attach (:func:`_attach_existing_account` onto the
row this importer already created, matched by its own ``normalized_email`), not a
duplicate account -- unless that exact source id is also the one CMP row half of
the export's one known same-address collision (ids 2/15515), in which case a
reconciliation-worthy misattribution becomes possible instead of impossible. Given
how narrow that compound window is -- a kill in a few milliseconds of file I/O,
on a watermark that has already independently failed, on the one specific already-
flagged row pair -- this is the accepted trade-off of keeping claim tracking as
ingestion-scoped script state rather than a permanent column, not an oversight.

Cross-source deduplication
---------------------------

This importer runs after ``import_legacy_zoomcamp.py`` (step 1 of the
migration), which also writes ``accounts_customuser`` -- for the pre-2024
Zoomcamp editions, keyed by the learner's recovered real email, with just a
``username`` and ``email`` set. Neither importer's own natural-key check (this
one's claims store, that one's ``normalized_email`` lookup against *existing*
rows) sees the other importer's writes on a first pass in migration order,
because the legacy importer runs first: it has nothing to find yet, and when
this importer runs second it never looked. Left alone, that is a duplicate
account per person migrated by both -- and a duplicate address is exactly what
``accounts.auth.ConsolidatingSocialAccountAdapter`` refuses to sign in
(``verified_owner_ambiguous``, see the migration runbook §5).

So before creating a new row, this importer checks for an existing account
sharing the same ``normalized_email`` that the claims store has no entry for --
i.e. one written by a different importer, not a duplicate within the CMP
export itself. If one exists, this importer attaches its data (profile
fields, an unusable password) onto that *existing* row instead of creating a
second one, and records the claim. It never touches the existing account's
``username`` or ``email`` -- those already identify it, and the account's
primary key never changes, so everything that already references it
(enrollments, submissions, certificates) keeps working unchanged. This is a
plain merge, not the reviewed reconciliation flow in
``scripts.prod.account_reconciliation``: both sides are the same real person,
written once each by two importers that do not know about each other, with no
conflicting history to adjudicate -- unlike the one address the CMP export
shares with itself (ids 2/15515 in the real export), which two CMP rows both
claim and which stays exactly as before, reported by
``synthesis_skipped_collisions`` for the reviewed reconciliation flow to
handle, because a row the claims store already has an entry for never counts
as a cross-source match candidate.

Resumability
------------

Both tables, and the synthesis pass, are processed in ascending source-id order,
in fixed-size batches, tracked in ``accounts.models.CmpLearnerImportProgress``.
Each batch's writes and its watermark advance happen inside one transaction, so a
process killed mid-batch leaves nothing partially written for the next run to
double-count; a re-run's first query is ``id > last_source_id``, so it does not
re-scan rows it already committed. The claims store (above) is this importer's
belt-and-braces check within a batch: a row already carrying a claimed source id
is skipped and counted, not re-created, regardless of what the watermark says.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from allauth.account.models import EmailAddress
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from accounts.identity_values import normalize_account_email
from accounts.models import CmpLearnerImportProgress, CustomUser

__all__ = [
    "CmpClaimsStore",
    "CmpLearnerImportError",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CLAIMS_PATH",
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

DEFAULT_CLAIMS_PATH = Path(".tmp/cmp_learner_import_claims.json")


class CmpLearnerImportError(RuntimeError):
    """A fail-closed refusal that never renders a source value (an email, a name)."""


def _refuse(code: str) -> NoReturn:
    raise CmpLearnerImportError(code)


@dataclass(slots=True)
class CmpClaimsStore:
    """This importer's own durable "CMP source id -> CustomUser pk" record.

    See the module docstring's "Claim tracking" section for why this exists as
    script-owned file state instead of a field on ``CustomUser``, and for the
    exact durability reasoning. Not thread- or process-safe for concurrent
    writers -- this importer is a single-threaded, one-time script, the same
    assumption ``CmpLearnerImportProgress`` already makes.
    """

    path: Path
    _by_source: dict[int, int] = field(default_factory=dict)
    _by_user: dict[int, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> CmpClaimsStore:
        try:
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(path=path)
        except OSError:
            _refuse("claims-file-unreadable")
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError:
            _refuse("claims-file-malformed")
        if not isinstance(raw, dict):
            _refuse("claims-file-malformed")
        by_source: dict[int, int] = {}
        for key, value in raw.items():
            try:
                by_source[int(key)] = int(value)
            except (TypeError, ValueError):
                _refuse("claims-file-malformed")
        store = cls(path=path, _by_source=by_source)
        store._by_user = {user_id: source_id for source_id, user_id in by_source.items()}
        return store

    def is_claimed(self, source_id: int) -> bool:
        return source_id in self._by_source

    def is_claimed_user(self, user_id: int) -> bool:
        return user_id in self._by_user

    def user_id_for_source(self, source_id: int) -> int | None:
        return self._by_source.get(source_id)

    def source_id_for_user(self, user_id: int) -> int | None:
        return self._by_user.get(user_id)

    def claimed_user_ids(self) -> frozenset[int]:
        return frozenset(self._by_user)

    def sorted_claims(self) -> list[tuple[int, int]]:
        return sorted(self._by_source.items())

    def record(self, *, source_id: int, user_id: int) -> None:
        self._by_source[source_id] = user_id
        self._by_user[user_id] = source_id

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {str(source_id): user_id for source_id, user_id in self._by_source.items()},
            sort_keys=True,
        )
        descriptor, tmp_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=".cmp-learner-claims-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def __len__(self) -> int:
        return len(self._by_source)


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
    cross_source_matches: tuple[int, ...] = field(default_factory=tuple)

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
            # Source accounts_customuser ids only -- never an email. These
            # rows were attached onto an account a different importer (today:
            # import_legacy_zoomcamp) already created for the same address,
            # rather than creating a duplicate. This call's matches only --
            # not cumulative across a killed-and-resumed run, see
            # _import_accounts.
            "cross_source_matches": list(self.cross_source_matches),
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


def _populate_profile_fields(user: CustomUser, row: sqlite3.Row) -> None:
    """Copy the export's profile columns onto ``user``. Shared by a brand-new
    account and one attached from a different importer -- see
    ``_attach_existing_account``. Never touches ``username``, ``email``,
    ``is_staff``, ``is_superuser`` or the password; callers own those
    explicitly. The CMP source id is never a field on ``user`` at all -- see
    the module docstring's "Claim tracking" section."""

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


def _build_account(row: sqlite3.Row) -> CustomUser:
    email = (row["email"] or "").strip()
    user = CustomUser(
        username=_resolve_username(row),
        email=email,
        is_staff=False,
        is_superuser=False,
    )
    _populate_profile_fields(user, row)
    user.set_unusable_password()
    return user


def _find_cross_source_match(email: str, *, claims: CmpClaimsStore) -> CustomUser | None:
    """An account another importer already created for ``email``, if any.

    Deliberately excludes any account the claims store already has an entry
    for -- a match there would be a duplicate *within* the CMP export (the one
    known case, ids 2/15515), which is a different, harder problem this
    importer does not resolve; see the module docstring's "Cross-source
    deduplication" section. Accounts sharing one email address are always a
    small set (typically one), so this walks them in Python rather than
    building a large ``exclude(pk__in=...)`` clause against a store that can
    hold up to 20,009 entries.
    """

    normalized = normalize_account_email(email)
    if not normalized:
        return None
    for candidate in CustomUser.objects.filter(normalized_email=normalized).order_by("pk"):
        if not claims.is_claimed_user(candidate.pk):
            return candidate
    return None


def _attach_existing_account(user: CustomUser, row: sqlite3.Row) -> CustomUser:
    """Attach this CMP row onto an account a different importer already
    created for the same address, rather than creating a duplicate. Leaves
    ``username`` and ``email`` untouched -- those already identify the
    account, and its primary key never changes, so every existing reference
    to it (enrollments, submissions, certificates) keeps working unchanged.
    The caller records the claim once this returns.
    """

    _populate_profile_fields(user, row)
    user.is_staff = False
    user.is_superuser = False
    user.set_unusable_password()
    user.save()
    return user


def _import_accounts(
    connection: sqlite3.Connection, *, batch_size: int, claims: CmpClaimsStore
) -> tuple[LearnerImportPhaseReport, tuple[int, ...]]:
    source_total = _count(connection, ACCOUNTS_TABLE)
    progress = _get_progress(ACCOUNTS_TABLE)
    # Source ids attached onto an account a different importer already
    # created, this call only -- see the module docstring's "Cross-source
    # deduplication" section. Not cumulative across a killed-and-resumed run:
    # a row already attached in an earlier call is caught by the claims-store
    # check below and counted as skipped, same as any other already-imported
    # row.
    cross_source_matches: list[int] = []
    while not progress.completed:
        rows = connection.execute(
            "select * from accounts_customuser where id > ? order by id limit ?",
            (progress.last_source_id, batch_size),
        ).fetchall()
        if not rows:
            progress.completed = True
            _save_progress(progress)
            claims.flush()
            break
        batch_had_new_claims = False
        with transaction.atomic():
            written = 0
            skipped = 0
            max_id = progress.last_source_id
            for row in rows:
                max_id = max(max_id, row["id"])
                if claims.is_claimed(row["id"]):
                    skipped += 1
                    continue
                existing = _find_cross_source_match((row["email"] or "").strip(), claims=claims)
                if existing is not None:
                    account = _attach_existing_account(existing, row)
                    cross_source_matches.append(row["id"])
                else:
                    account = _build_account(row)
                    account.save()
                # Recorded in memory immediately -- a later row in this same
                # batch sharing this row's email (the export's one known
                # same-address pair, ids 2/15515) must see this claim to
                # correctly treat it as CMP-claimed, not as an unclaimed
                # cross-source match to attach onto. If this transaction
                # rolls back (the batch fails), this in-memory update is
                # discarded along with it -- the exception propagates out of
                # this whole function uncaught, so execution never reaches
                # the claims-file flush below for this batch.
                claims.record(source_id=row["id"], user_id=account.pk)
                batch_had_new_claims = True
                written += 1
            progress.last_source_id = max_id
            progress.rows_written += written
            progress.rows_skipped += skipped
            _save_progress(progress)
        # The claims file is written only after the database transaction
        # above has really committed -- see the module docstring's "Claim
        # tracking" section for why the order matters and what a crash in
        # between costs.
        if batch_had_new_claims:
            claims.flush()
    return _phase_report(progress, source_total), tuple(cross_source_matches)


def _import_email_addresses(
    connection: sqlite3.Connection, *, batch_size: int, claims: CmpClaimsStore
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
                user_id = claims.user_id_for_source(row["user_id"])
                if user_id is None:
                    skipped += 1
                    continue
                try:
                    user = CustomUser.objects.get(pk=user_id)
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
    *, batch_size: int, claims: CmpClaimsStore
) -> tuple[LearnerImportPhaseReport, tuple[int, ...]]:
    progress = _get_progress(SYNTHESIZED_EMAIL_ADDRESS_TABLE)
    # A snapshot, not a per-batch requery: every claimed user is visited by
    # this function at most once (the watermark below guarantees that), and
    # nothing outside this single-threaded run can give a user an email row
    # between the snapshot and its turn, so this is equivalent to the
    # original per-iteration DB query, not an approximation of it.
    emailless_user_ids = set(
        CustomUser.objects.filter(pk__in=claims.claimed_user_ids())
        .exclude(pk__in=EmailAddress.objects.values("user_id"))
        .exclude(email="")
        .values_list("pk", flat=True)
    )
    ordered_candidates = [
        (source_id, user_id)
        for source_id, user_id in claims.sorted_claims()
        if user_id in emailless_user_ids
    ]
    source_total = len(ordered_candidates)
    collisions: list[int] = []
    while not progress.completed:
        batch = [
            (source_id, user_id)
            for source_id, user_id in ordered_candidates
            if source_id > progress.last_source_id
        ][:batch_size]
        if not batch:
            progress.completed = True
            _save_progress(progress)
            break
        with transaction.atomic():
            written = 0
            skipped = 0
            max_id = progress.last_source_id
            for source_id, user_id in batch:
                max_id = max(max_id, source_id)
                user = CustomUser.objects.get(pk=user_id)
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
                    collisions.append(source_id)
            progress.last_source_id = max_id
            progress.rows_written += written
            progress.rows_skipped += skipped
            _save_progress(progress)
    return _phase_report(progress, source_total), tuple(collisions)


def dry_run_counts(source: Path, *, claims_path: Path = DEFAULT_CLAIMS_PATH) -> dict[str, Any]:
    """Report counts without writing anything."""

    _assert_forbidden_tables_untouched()
    connection = _readonly(source)
    try:
        accounts_total = _count(connection, ACCOUNTS_TABLE)
        emails_total = _count(connection, EMAIL_ADDRESS_TABLE)
    finally:
        connection.close()
    claims = CmpClaimsStore.load(claims_path)
    already_imported = len(claims)
    already_imported_emails = EmailAddress.objects.filter(
        user_id__in=claims.claimed_user_ids()
    ).count()
    return {
        "accounts_in_source": accounts_total,
        "account_emailaddress_in_source": emails_total,
        "accounts_already_imported": already_imported,
        "account_emailaddress_already_imported": already_imported_emails,
        "applied": False,
    }


def progress_status(*, claims_path: Path = DEFAULT_CLAIMS_PATH) -> dict[str, Any]:
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
    return {"progress": rows, "claims_recorded": len(CmpClaimsStore.load(claims_path))}


def import_cmp_learners(
    source: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    claims_path: Path = DEFAULT_CLAIMS_PATH,
) -> LearnerImportResult:
    """Import the CMP export's learner accounts. Safe to kill and re-run."""

    _assert_forbidden_tables_untouched()
    claims = CmpClaimsStore.load(claims_path)
    connection = _readonly(source)
    try:
        accounts_report, cross_source_matches = _import_accounts(
            connection, batch_size=batch_size, claims=claims
        )
        email_report = _import_email_addresses(connection, batch_size=batch_size, claims=claims)
    finally:
        connection.close()
    synthesized_report, collisions = _synthesize_missing_email_addresses(
        batch_size=batch_size, claims=claims
    )
    return LearnerImportResult(
        accounts=accounts_report,
        email_addresses=email_report,
        synthesized_email_addresses=synthesized_report,
        synthesis_skipped_collisions=collisions,
        cross_source_matches=cross_source_matches,
    )
