"""Attendee-level registrant identity consolidation and per-event registration facts.

This is the one place in the codebase that reads a Luma/Eventbrite export's
attendee-level rows (an email, a per-event registration status) at all --
deliberately kept separate from :mod:`events.importers`, whose own contract is
"no attendee value crosses this module boundary" for the aggregate-only
adapters it provides.  Nothing here reuses that module's checksum-pinned
production-count safety net (``derive_luma``/``derive_eventbrite``); this reads
the same protected directory independently, with its own narrower safety
checks, because minting an identity/fact row carries none of the "silently
corrupt a public count" risk those adapters exist to guard against -- the same
reasoning ``events.importers.discover_luma_events`` already uses for
identity-only reads.

The core principle, stated by the product owner: someone who both took a
course and registered for an event must resolve to one account, never two.
So every registrant row is consolidated against ``accounts_customuser`` by
``normalized_email`` first, exactly the way
``accounts.services.cmp_learner_import`` consolidates a second importer's rows
against a first importer's accounts (see its ``_find_cross_source_match``).
Only when that lookup, and a lookup against a previously-seen registrant-only
identity from earlier in this same run, both come up empty does a new
registrant-only identity get created -- see
:class:`events.models.EventRegistrantIdentity` for the full contract.

Sequencing matches the owner's stated design: ingest one event's identity
(elsewhere, in ``events.identity``/``scripts/prod/import_events.py``), then
that event's registrant rows here, one event at a time -- never the whole
export's rows in one pass.  Consolidation lookups are global across the run
(a Django queryset always sees every previously committed transaction), so
the same person is recognised whether they are on event 3 or event 300.

Resumability is at event granularity, not row granularity -- see
:class:`events.models.EventRegistrantImportProgress` for why.  A completed
event is skipped without even reopening its file; an interrupted event is
retried whole, inside one transaction, on the next run.

Eventbrite is not read here yet.  The real durable export at
``/data/tmp/luma-eventbrite-export/`` currently holds only the Luma
side (``luma-aggregate-v1``); no real Eventbrite attendee-level archive is
available to build or verify an Eventbrite reader against, and this
codebase's own Eventbrite adapter (``events.importers.derive_eventbrite``)
never needed to know that provider's attendee-level column names, since it
only ever counted rows. Rather than guess a schema for a real PII export this
module has never seen, ``EventRegistration.Provider.EVENTBRITE`` and every
field below are already provider-generic, so an Eventbrite reader is a
follow-up that adds a second ``discover_*``/``read_*`` pair, not a rework of
the model or matching logic.
"""

from __future__ import annotations

import csv
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from accounts.identity_values import normalize_account_email
from accounts.models import CustomUser

from .identity import EventIdentityNotFound, provider_source_identity, resolve_source_identity
from .models import (
    EventRegistrantIdentity,
    EventRegistrantImportProgress,
    EventRegistration,
)

__all__ = [
    "RegistrantImportError",
    "DiscoveredRegistrantFile",
    "EventImportOutcome",
    "RunReport",
    "discover_luma_registrant_files",
    "read_luma_registrant_rows",
    "import_luma_registrants",
]

# Required for identity-consolidation reads only -- a narrower set than
# events.importers's LUMA_REQUIRED_COLUMNS, since this reader also needs
# `email` and `registered_at`, neither of which the aggregate-only adapter
# ever touches.
_REGISTRANT_REQUIRED_COLUMNS = (
    "event_id",
    "guest_id",
    "email",
    "approval_status",
    "registered_at",
)
# Generous headroom over the real export's largest single event file
# (~3,500 rows, measured) -- a bound, not a tuned expectation.
MAX_ROWS_PER_EVENT = 100_000


class RegistrantImportError(ValueError):
    """A bounded refusal that never embeds a source value (an email, a name, a guest id)."""


def _refuse(code: str) -> NoReturn:
    raise RegistrantImportError(code)


def _safe_csv_path(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError:
        _refuse("source_unavailable")
    if stat.S_ISLNK(metadata.st_mode):
        _refuse("source_symlink")
    if not resolved.is_file():
        _refuse("source_not_file")
    return resolved


@dataclass(frozen=True, slots=True)
class DiscoveredRegistrantFile:
    external_event_identifier: str
    csv_path: Path


def discover_luma_registrant_files(root: Path) -> tuple[DiscoveredRegistrantFile, ...]:
    """Pair each CSV with its JSON checkpoint's ``event_id``, sorted by file stem.

    Deliberately re-derives this pairing rather than importing
    ``events.importers``'s private helpers -- see the module docstring for why
    attendee-crossing code stays out of that module's boundary. Applies the
    same non-symlink, regular-file safety check to every path it opens as
    ``events.importers`` does for the aggregate-only reads.
    """

    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        _refuse("source_unavailable")
    if not resolved_root.is_dir():
        _refuse("source_not_directory")

    csv_by_stem: dict[str, Path] = {}
    json_by_stem: dict[str, Path] = {}
    for entry in sorted(resolved_root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith("."):
            continue
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            continue
        suffix = entry.suffix.casefold()
        if suffix == ".csv":
            csv_by_stem[entry.stem] = entry
        elif suffix == ".json":
            json_by_stem[entry.stem] = entry
    if set(csv_by_stem) != set(json_by_stem):
        _refuse("mismatched_luma_pair")

    discovered: list[DiscoveredRegistrantFile] = []
    for stem in sorted(csv_by_stem):
        try:
            document = json.loads(json_by_stem[stem].read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            _refuse("malformed_json")
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            _refuse("unsupported_luma_schema")
        event_id = document.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            _refuse("malformed_json")
        discovered.append(
            DiscoveredRegistrantFile(
                external_event_identifier=event_id, csv_path=csv_by_stem[stem]
            )
        )
    return tuple(discovered)


@dataclass(frozen=True, slots=True)
class RegistrantRow:
    external_registrant_identifier: str
    normalized_email: str | None
    status: str
    registered_at_raw: str


def read_luma_registrant_rows(
    csv_path: Path, *, external_event_identifier: str
) -> tuple[RegistrantRow, ...]:
    """Read one event's registrant rows.  Duplicate ``guest_id`` rows keep the first only."""

    resolved = _safe_csv_path(csv_path)
    rows: list[RegistrantRow] = []
    seen_guest_ids: set[str] = set()
    try:
        with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            headers = reader.fieldnames
            if headers is None or any(
                column not in headers for column in _REGISTRANT_REQUIRED_COLUMNS
            ):
                _refuse("unsupported_luma_schema")
            for index, row in enumerate(reader):
                if index >= MAX_ROWS_PER_EVENT:
                    _refuse("row_count_exceeded")
                if row.get("event_id") != external_event_identifier:
                    _refuse("mismatched_luma_pair")
                guest_id = (row.get("guest_id") or "").strip()
                status = (row.get("approval_status") or "").strip()
                if not guest_id or not status:
                    _refuse("malformed_csv")
                if guest_id in seen_guest_ids:
                    continue
                seen_guest_ids.add(guest_id)
                email = (row.get("email") or "").strip()
                rows.append(
                    RegistrantRow(
                        external_registrant_identifier=guest_id,
                        normalized_email=normalize_account_email(email),
                        status=status.casefold(),
                        registered_at_raw=(row.get("registered_at") or "").strip(),
                    )
                )
    except RegistrantImportError:
        raise
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise RegistrantImportError("malformed_csv") from error
    return tuple(rows)


def _parse_registered_at(raw: str) -> Any:
    if not raw:
        return None
    try:
        return parse_datetime(raw)
    except ValueError:
        return None


def _resolve_identity(normalized_email: str) -> tuple[EventRegistrantIdentity, str]:
    """Return ``(identity, match_kind)``, consolidating against accounts first.

    ``match_kind`` is one of ``"matched_account"``, ``"matched_prior_identity"``,
    ``"new_identity"`` -- this is the whole "never a duplicate profile for the
    same real person" guarantee: an account match always wins, a prior
    registrant-only identity is reused before anything new is created, and a
    genuinely new identity is only ever created for an address neither lookup
    found.
    """

    account = (
        CustomUser.objects.filter(normalized_email=normalized_email).order_by("pk").first()
    )
    if account is not None:
        identity, _ = EventRegistrantIdentity.objects.get_or_create(account=account)
        return identity, "matched_account"

    existing = (
        EventRegistrantIdentity.objects.filter(
            normalized_email=normalized_email, account__isnull=True
        )
        .order_by("id")
        .first()
    )
    if existing is not None:
        return existing, "matched_prior_identity"

    try:
        with transaction.atomic():
            identity = EventRegistrantIdentity.objects.create(normalized_email=normalized_email)
        return identity, "new_identity"
    except IntegrityError:
        # A race created it between the lookup above and this insert. The
        # unique constraint (events_registrant_identity_email_unique_unmatched)
        # is the actual guarantee; self-heal by reusing the row it protected
        # rather than failing the whole event -- same pattern as
        # accounts.services.cmp_learner_import's unique_verified_email handling.
        return (
            EventRegistrantIdentity.objects.get(
                normalized_email=normalized_email, account__isnull=True
            ),
            "matched_prior_identity",
        )


@dataclass(frozen=True, slots=True)
class EventImportOutcome:
    external_event_identifier: str
    status: str  # "completed", "already_completed", "no_identity_yet"
    rows_total: int = 0
    rows_written: int = 0
    rows_skipped: int = 0
    matched_account_total: int = 0
    matched_prior_identity_total: int = 0
    new_identity_total: int = 0


def _import_one_event(
    *, provider: str, external_event_identifier: str, csv_path: Path
) -> EventImportOutcome:
    progress, _ = EventRegistrantImportProgress.objects.get_or_create(
        provider=provider, external_event_identifier=external_event_identifier
    )
    if progress.completed:
        # Per-call reporting, deliberately -- the same convention
        # accounts.services.cmp_learner_import uses ("this call's matches
        # only -- not cumulative across a killed-and-resumed run"). Nothing
        # new happened for this event on this call, so every count here is
        # zero; the event's historical totals stay on the progress row
        # itself (events.models.EventRegistrantImportProgress), not folded
        # into this run's report.
        return EventImportOutcome(
            external_event_identifier=external_event_identifier, status="already_completed"
        )

    source = provider_source_identity(
        provider=provider, external_event_identifier=external_event_identifier
    )
    try:
        event = resolve_source_identity(
            repository=source.repository,
            revision=source.revision,
            source_key=source.source_key,
        )
    except EventIdentityNotFound:
        # Not yet discovered by events.identity.create_provider_event_identity
        # (5.2 in the ingest inventory). Reported, not created here -- this
        # module never mints an Event identity itself.
        return EventImportOutcome(
            external_event_identifier=external_event_identifier, status="no_identity_yet"
        )

    rows = read_luma_registrant_rows(csv_path, external_event_identifier=external_event_identifier)

    written = skipped = matched_account = matched_prior = created_identity = 0
    with transaction.atomic():
        for row in rows:
            if row.normalized_email is None:
                skipped += 1
                continue
            identity, match_kind = _resolve_identity(row.normalized_email)
            if match_kind == "matched_account":
                matched_account += 1
            elif match_kind == "matched_prior_identity":
                matched_prior += 1
            else:
                created_identity += 1
            EventRegistration.objects.create(
                event=event,
                identity=identity,
                provider=provider,
                status=row.status,
                registered_at=_parse_registered_at(row.registered_at_raw),
            )
            written += 1
        progress.completed = True
        progress.rows_total = len(rows)
        progress.rows_written = written
        progress.rows_skipped = skipped
        progress.matched_account_total = matched_account
        progress.matched_prior_identity_total = matched_prior
        progress.new_identity_total = created_identity
        progress.save()

    return EventImportOutcome(
        external_event_identifier=external_event_identifier,
        status="completed",
        rows_total=len(rows),
        rows_written=written,
        rows_skipped=skipped,
        matched_account_total=matched_account,
        matched_prior_identity_total=matched_prior,
        new_identity_total=created_identity,
    )


@dataclass(frozen=True, slots=True)
class RunReport:
    provider: str
    events_total: int
    events_completed: int
    events_already_completed: int
    events_awaiting_identity: int
    awaiting_identity_events: tuple[str, ...]
    rows_written: int
    rows_skipped: int
    matched_account_total: int
    matched_prior_identity_total: int
    new_identity_total: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "events_total": self.events_total,
            "events_completed": self.events_completed,
            "events_already_completed": self.events_already_completed,
            "events_awaiting_identity": self.events_awaiting_identity,
            # Provider event ids are public (part of the event's public Luma
            # URL), not attendee PII -- same treatment as
            # events.importers's own "no_metadata_events" reporting.
            "awaiting_identity_events": list(self.awaiting_identity_events),
            "rows_written": self.rows_written,
            "rows_skipped": self.rows_skipped,
            "matched_account_total": self.matched_account_total,
            "matched_prior_identity_total": self.matched_prior_identity_total,
            "new_identity_total": self.new_identity_total,
        }


def import_luma_registrants(source: Path) -> RunReport:
    """Import one Luma export's registrant rows, one event at a time.

    Global consolidation, per-event sequencing: each event's rows are
    resolved and written inside their own transaction, in file-stem order,
    but every identity lookup queries the whole database, so a person seen on
    event 3 is recognised again on event 300, whether or not the earlier
    event has finished in this same run. Safe to run repeatedly -- a
    completed event contributes nothing new on replay; see
    events.models.EventRegistrantImportProgress.
    """

    provider = EventRegistration.Provider.LUMA
    discovered = discover_luma_registrant_files(source)

    events_completed = events_already_completed = 0
    awaiting_identity: list[str] = []
    rows_written = rows_skipped = 0
    matched_account = matched_prior = new_identity = 0
    for item in discovered:
        outcome = _import_one_event(
            provider=provider,
            external_event_identifier=item.external_event_identifier,
            csv_path=item.csv_path,
        )
        if outcome.status == "no_identity_yet":
            awaiting_identity.append(item.external_event_identifier)
            continue
        if outcome.status == "already_completed":
            events_already_completed += 1
        else:
            events_completed += 1
        rows_written += outcome.rows_written
        rows_skipped += outcome.rows_skipped
        matched_account += outcome.matched_account_total
        matched_prior += outcome.matched_prior_identity_total
        new_identity += outcome.new_identity_total

    return RunReport(
        provider=provider,
        events_total=len(discovered),
        events_completed=events_completed,
        events_already_completed=events_already_completed,
        events_awaiting_identity=len(awaiting_identity),
        awaiting_identity_events=tuple(awaiting_identity),
        rows_written=rows_written,
        rows_skipped=rows_skipped,
        matched_account_total=matched_account,
        matched_prior_identity_total=matched_prior,
        new_identity_total=new_identity,
    )
