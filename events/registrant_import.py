"""Consolidate attendee-level registrant rows into identities and registration facts.

This is the domain half of the registrant import: everything that is about
*our* records -- which person a registrant row resolves to, which rows become
``EventRegistration`` facts, which events this run has already finished.  It
opens no file and knows no provider's export format.

Reading a provider's export is ingestion work and lives beside the other
provider readers, in ``scripts/prod/registration_sources``.  A reader hands
this module already-parsed, provider-neutral :class:`RegistrantRow` values
wrapped in
:class:`PendingEventRegistrants`.  The dependency runs one way only: a reader
imports this module, and this module imports no reader -- the same direction
``events.importers`` and the aggregate-only readers already use.  Unlike that
port there is no reader *registry* here: nothing on a request path derives
registrants, so the single ingest entry point hands its reader's output
straight in rather than through global state.

``PendingEventRegistrants.read_rows`` is a callable, not a tuple, and that is
load-bearing: it is what keeps the resume rule below exactly what it was.  A
completed event is skipped without its rows ever being asked for, so the reader
never reopens that event's file.

This remains the one path in the codebase that handles attendee-level values at
all -- a :class:`RegistrantRow` carries a normalized email address --
deliberately separate from the aggregate-only readers in
``scripts/prod/registration_sources``, whose own contract is "no attendee value
crosses this module boundary".  Nothing here reuses their checksum-pinned
production-count safety net; minting an identity/fact row carries none of the
"silently corrupt a public count" risk those adapters exist to guard against.
No attendee value is ever logged, printed, or embedded in an error:
:class:`RegistrantImportError` carries a bounded condition code and nothing
else, and every number this module reports is an aggregate.

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
event is skipped without even reading its rows; an interrupted event is
retried whole, inside one transaction, on the next run.

That skip is the whole replay guarantee, and it is also why a *later* export of
the same event cannot simply be re-imported: :class:`events.models.EventRegistration`
deliberately keeps no per-attendee natural key, so a second pass over an event
would write a second row for every registrant it already holds.  ``refresh``
below is the supported way to pick up sign-ups that arrived after the export we
last read.  It replaces one event's registration facts wholesale -- delete the
provider's rows for that event, write the ones the newer export carries, in the
same transaction -- rather than appending, because a refreshed export is not
append-only: a registrant who cancels, or whom the provider deletes, disappears
from it, and an append would leave us asserting a registration that no longer
exists.  Identities are never deleted: a person we have already consolidated
stays consolidated whether or not they are still on this event's list.

Only one provider's registrants are imported today, because only one real
attendee-level export exists to build and verify a reader against.  Nothing
here has to change when a second one arrives: every type below is
provider-generic and the provider is an argument rather than a constant, so a
second provider is one more reader module in the ingestion layer, not a rework
of the model or the matching logic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from accounts.models import CustomUser

from .identity import EventIdentityNotFound, provider_source_identity, resolve_source_identity
from .models import (
    EventRegistrantIdentity,
    EventRegistrantImportProgress,
    EventRegistration,
)

__all__ = [
    "RegistrantImportError",
    "RegistrantRow",
    "PendingEventRegistrants",
    "EventImportOutcome",
    "RunReport",
    "import_registrants",
    "resolve_registrant_identity",
]


class RegistrantImportError(ValueError):
    """A bounded refusal that never embeds a source value (an email, a name, a guest id).

    Raised by the provider readers in ``scripts/prod/registration_sources`` as
    well as from here: it is this port's failure type, the way
    ``events.importers.ProtectedSourceError`` is the aggregate port's.
    """


@dataclass(frozen=True, slots=True)
class RegistrantRow:
    """One registrant's row, already parsed and stripped of provider shape.

    ``normalized_email`` is the only attendee value that crosses this boundary,
    and it exists solely as the consolidation key.  It is never logged and
    never reported; only counts derived from it leave this module.
    """

    external_registrant_identifier: str
    normalized_email: str | None
    status: str
    registered_at_raw: str


@dataclass(frozen=True, slots=True)
class PendingEventRegistrants:
    """One provider event whose rows this run may or may not need to read.

    ``read_rows`` is called at most once per run, and only after the progress
    row says the event is unfinished *and* its event identity resolves -- so a
    completed event is skipped without the reader touching its file at all.
    """

    external_event_identifier: str
    read_rows: Callable[[], tuple[RegistrantRow, ...]]


def _parse_registered_at(raw: str) -> Any:
    if not raw:
        return None
    try:
        return parse_datetime(raw)
    except ValueError:
        return None


def resolve_registrant_identity(normalized_email: str) -> tuple[EventRegistrantIdentity, str]:
    """Return ``(identity, match_kind)``, consolidating against accounts first.

    ``match_kind`` is one of ``"matched_account"``, ``"matched_prior_identity"``,
    ``"new_identity"`` -- this is the whole "never a duplicate profile for the
    same real person" guarantee: an account match always wins, a prior
    registrant-only identity is reused before anything new is created, and a
    genuinely new identity is only ever created for an address neither lookup
    found.

    Public (not module-private) because it is the exact consolidation
    discipline :mod:`events.mailchimp_tag_import` reuses rather than
    reinventing -- see that module's docstring. Both callers hand it an
    already-normalized email; this function does no normalization of its
    own.
    """

    account = CustomUser.objects.filter(normalized_email=normalized_email).order_by("pk").first()
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
    status: str  # "completed", "refreshed", "already_completed", "no_identity_yet"
    rows_total: int = 0
    rows_written: int = 0
    rows_skipped: int = 0
    rows_replaced: int = 0
    matched_account_total: int = 0
    matched_prior_identity_total: int = 0
    new_identity_total: int = 0


def _import_one_event(
    *,
    provider: str,
    external_event_identifier: str,
    read_rows: Callable[[], tuple[RegistrantRow, ...]],
    refresh: bool = False,
) -> EventImportOutcome:
    progress, _ = EventRegistrantImportProgress.objects.get_or_create(
        provider=provider, external_event_identifier=external_event_identifier
    )
    if progress.completed and not refresh:
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

    # Only here, past both gates, are the rows asked for at all: that is what
    # keeps a completed or identity-less event from reopening its source file.
    replacing = progress.completed
    rows = read_rows()

    written = skipped = matched_account = matched_prior = created_identity = 0
    replaced = 0
    with transaction.atomic():
        if replacing:
            # Wholesale, and inside the same transaction as the rewrite: a
            # refreshed export is not append-only, so the rows it no longer
            # carries have to stop being facts at the same moment the new ones
            # start being facts.  Identities are untouched.
            replaced, _ = EventRegistration.objects.filter(event=event, provider=provider).delete()
        for row in rows:
            if row.normalized_email is None:
                skipped += 1
                continue
            identity, match_kind = resolve_registrant_identity(row.normalized_email)
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
        status="refreshed" if replacing else "completed",
        rows_total=len(rows),
        rows_written=written,
        rows_skipped=skipped,
        rows_replaced=replaced,
        matched_account_total=matched_account,
        matched_prior_identity_total=matched_prior,
        new_identity_total=created_identity,
    )


@dataclass(frozen=True, slots=True)
class RunReport:
    provider: str
    events_total: int
    events_completed: int
    events_refreshed: int
    events_already_completed: int
    events_awaiting_identity: int
    awaiting_identity_events: tuple[str, ...]
    rows_written: int
    rows_skipped: int
    rows_replaced: int
    matched_account_total: int
    matched_prior_identity_total: int
    new_identity_total: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "events_total": self.events_total,
            "events_completed": self.events_completed,
            # Events whose already-imported registration facts this run replaced
            # with a newer export's. Zero unless the caller asked for a refresh.
            "events_refreshed": self.events_refreshed,
            "events_already_completed": self.events_already_completed,
            "events_awaiting_identity": self.events_awaiting_identity,
            # Provider event ids are public (part of the event's public
            # provider URL), not attendee PII -- same treatment as
            # the aggregate-only readers' own "no_metadata_events" reporting.
            "awaiting_identity_events": list(self.awaiting_identity_events),
            "rows_written": self.rows_written,
            "rows_skipped": self.rows_skipped,
            # Rows deleted to make room for the refreshed ones. A refresh that
            # replaces more than it writes means registrants left the export.
            "rows_replaced": self.rows_replaced,
            "matched_account_total": self.matched_account_total,
            "matched_prior_identity_total": self.matched_prior_identity_total,
            "new_identity_total": self.new_identity_total,
        }


def import_registrants(
    *, provider: str, pending: Iterable[PendingEventRegistrants], refresh: bool = False
) -> RunReport:
    """Import one export's registrant rows, one event at a time, in the reader's order.

    Global consolidation, per-event sequencing: each event's rows are
    resolved and written inside their own transaction, in the order the reader
    discovered them, but every identity lookup queries the whole database, so a
    person seen on event 3 is recognised again on event 300, whether or not the
    earlier event has finished in this same run. Safe to run repeatedly -- a
    completed event contributes nothing new on replay and its rows are never
    read again; see events.models.EventRegistrantImportProgress.

    ``refresh`` is for the case that replay deliberately does not cover: a newer
    export of events we have already imported. It re-reads every event the
    reader offers, including completed ones, and replaces each one's existing
    registration facts with what the newer export carries -- see the module
    docstring for why replacing rather than appending is the only correct
    reading of a provider export. It is not the default, because the default is
    resuming an interrupted run, and a resume must never touch a finished event.
    """

    events = tuple(pending)

    events_completed = events_refreshed = events_already_completed = 0
    awaiting_identity: list[str] = []
    rows_written = rows_skipped = rows_replaced = 0
    matched_account = matched_prior = new_identity = 0
    for item in events:
        outcome = _import_one_event(
            provider=provider,
            external_event_identifier=item.external_event_identifier,
            read_rows=item.read_rows,
            refresh=refresh,
        )
        if outcome.status == "no_identity_yet":
            awaiting_identity.append(item.external_event_identifier)
            continue
        if outcome.status == "already_completed":
            events_already_completed += 1
        elif outcome.status == "refreshed":
            events_refreshed += 1
        else:
            events_completed += 1
        rows_written += outcome.rows_written
        rows_skipped += outcome.rows_skipped
        rows_replaced += outcome.rows_replaced
        matched_account += outcome.matched_account_total
        matched_prior += outcome.matched_prior_identity_total
        new_identity += outcome.new_identity_total

    return RunReport(
        provider=provider,
        events_total=len(events),
        events_completed=events_completed,
        events_refreshed=events_refreshed,
        events_already_completed=events_already_completed,
        events_awaiting_identity=len(awaiting_identity),
        awaiting_identity_events=tuple(awaiting_identity),
        rows_written=rows_written,
        rows_skipped=rows_skipped,
        rows_replaced=rows_replaced,
        matched_account_total=matched_account,
        matched_prior_identity_total=matched_prior,
        new_identity_total=new_identity,
    )
