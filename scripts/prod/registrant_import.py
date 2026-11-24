"""Consolidate attendee-level registrant rows into identities and registration facts,
and mint the provider-discovered Event identities those rows attach to.

Moved out of ``events/`` (formerly ``events.registrant_import``): this is
production-ingest domain logic -- real ``uv run`` scripts read it, no live view,
serializer, or API does -- so it lives under ``scripts/prod``, the same way
``scripts/prod/legacy_zoomcamp/identity.py`` is that ingestion's own identity
domain logic rather than living inside an app package.  ``events/models.py``
keeps only what a live route, Studio, or the admin API actually resolves by
(UUID/public-ID lookup, the canonical path builders); provider discovery's own
identity minting belongs beside the rest of its domain, here.

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
So every registrant row is consolidated against ``accounts_user`` by
``normalized_email`` first, exactly the way
``accounts.services.cmp_learner_import`` consolidates a second importer's rows
against a first importer's accounts (see its ``_find_cross_source_match``).
Only when that lookup, and a lookup against a previously-seen registrant-only
identity from earlier in this same run, both come up empty does a new
registrant-only identity get created -- see
:class:`events.models.EventRegistrantIdentity` for the full contract.

Sequencing matches the owner's stated design: ingest one event's identity
(``create_provider_event_identity``, below, or the reviewed manifest import in
``scripts.prod.identity_manifest``/``scripts/prod/import_events.py``), then that event's
registrant rows here, one event at a time -- never the whole export's rows in
one pass.  Consolidation lookups are global across the run (a Django queryset
always sees every previously committed transaction), so the same person is
recognised whether they are on event 3 or event 300.

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

Two providers' registrants are imported as of 2026-09-11: Luma, and now
Eventbrite (``scripts/prod/registration_sources/eventbrite_registrants.py``).
Every type below stayed provider-generic and the provider stayed an argument
rather than a constant, exactly as designed -- adding the second reader was
one more module in the ingestion layer, not a rework of the model or the
matching logic above. The one thing it *did* need is
:attr:`PendingEventRegistrants.resolve_event`: Eventbrite's export references
events this database already holds under the *legacy manifest's* source
identity, not a provider-minted one the way Luma's discovered events are, so
its reader supplies its own resolver rather than this module growing a second
identity scheme. See that attribute's own docstring.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from accounts.models import User
from accounts_ext.models import normalized_email_of
from event_registrants.models import (
    EventRegistrantIdentity,
    EventRegistrantImportProgress,
    EventRegistration,
)
from community_base.events.models import Event
from events.identity import (
    EventIdentityError,
    EventIdentityNotFound,
    canonical_event_date,
    create_event_identity,
    normalize_event_title,
    provider_event_date,
    resolve_source_identity,
)
from scripts.prod.identity_manifest import SourceIdentity

__all__ = [
    "PROVIDER_SOURCE_REPOSITORY",
    "PROVIDER_SOURCE_REVISION",
    "provider_source_identity",
    "create_provider_event_identity",
    "EXISTING_EVENT_MATCHED",
    "EXISTING_EVENT_AMBIGUOUS",
    "EXISTING_EVENT_NONE",
    "EXISTING_EVENT_DATE_UNUSABLE",
    "ExistingEventMatch",
    "ExistingEventIndex",
    "OCCURRENCE_MATCH_START_AND_TITLE",
    "OCCURRENCE_MATCH_DATE_AND_TITLE",
    "OccurrenceMatch",
    "EventOccurrenceIndex",
    "normalize_match_title",
    "parse_event_instant",
    "FallbackMatch",
    "resolve_pending_events",
    "RegistrantImportError",
    "RegistrantRow",
    "PendingEventRegistrants",
    "EventImportOutcome",
    "RunReport",
    "import_registrants",
    "resolve_registrant_identity",
]


# --------------------------------------------------------------------------
# Provider identity minting
# --------------------------------------------------------------------------
#
# Moved from ``events/identity.py`` (formerly ``events.identity.provider_source_identity``
# / ``create_provider_event_identity`` / ``PROVIDER_SOURCE_REPOSITORY`` /
# ``PROVIDER_SOURCE_REVISION``): pure import/backfill plumbing that mints the Event
# identity a provider-discovered event's registrant rows (below) then attach to.
# ``events.models`` keeps ``create_event_identity`` -- the shared, provider-agnostic
# allocator-safe primitive both the reviewed manifest import and this module call --
# and the UUID/date-title helpers ``events.services.resolve_unmatched_aggregates``
# (live application logic, not ingestion) also imports; only the provider-specific
# wrapping moved.

# Provenance recorded for an identity minted from a live provider export rather
# than the reviewed legacy-site manifest.  Distinct per provider so a Luma event
# id and an Eventbrite event id can never collide on the same source identity.
PROVIDER_SOURCE_REPOSITORY = {
    "luma": "dtc-historical-source/luma",
    "eventbrite": "dtc-historical-source/eventbrite",
}
PROVIDER_SOURCE_REVISION = {
    "luma": "luma-aggregate-v1",
    "eventbrite": "eventbrite-aggregate-v1",
}


def provider_source_identity(*, provider: str, external_event_identifier: str) -> SourceIdentity:
    """The source identity a provider-discovered event is created and looked up under."""

    if provider not in PROVIDER_SOURCE_REPOSITORY:
        raise EventIdentityError("unsupported_provider")
    return SourceIdentity(
        repository=PROVIDER_SOURCE_REPOSITORY[provider],
        revision=PROVIDER_SOURCE_REVISION[provider],
        source_key=external_event_identifier,
    )


def create_provider_event_identity(
    *,
    provider: str,
    external_event_identifier: str,
    title: str,
    event_type: str,
    speakers: Any,
    start_at: str | None = None,
) -> Event:
    """Mint an Event identity for one provider event, using the shared allocator.

    A provider export names no type and no speakers, and no event may exist
    without them, so the caller supplies both from a reviewed source
    (``scripts/prod/import_events.EventMetadataIndex``).  It calls
    :func:`events.identity.create_event_identity` -- the only creation path --
    rather than re-deriving a public ID or canonical path here.

    Callers own idempotency: check
    :func:`events.identity.resolve_source_identity` with
    :func:`provider_source_identity` first, and skip creation if it already
    resolves. This function always inserts.
    """

    source = provider_source_identity(
        provider=provider, external_event_identifier=external_event_identifier
    )
    return create_event_identity(
        title=title,
        event_type=event_type,
        speakers=speakers,
        starts_at=datetime.fromisoformat(start_at) if start_at else None,
        source_repository=source.repository,
        source_revision=source.revision,
        source_key=source.source_key,
    )


# --------------------------------------------------------------------------
# Duplicate-creation guard for provider discovery
# --------------------------------------------------------------------------
#
# Moved from ``events/identity.py`` (formerly ``events.identity.ExistingEventIndex``):
# its only real consumer is ``scripts/prod/import_events.py``, the same ingestion
# domain this module already belongs to.
#
# This is not an identity attachment and does not weaken the reviewed-manifest rule
# in ``scripts.prod.identity_manifest``.  Nothing here writes, rewrites or infers an ``Event``'s
# source identity: a provider event that matches an existing event is never given
# that event's source key, alias, UUID or public ID, and no registration count moves.
# The index answers one narrower question -- "does this database already describe
# this event?" -- so provider discovery can decline to mint a *second* row for
# an event the reviewed manifest already describes under its legacy
# ``_data/events.yaml`` source key.  Declining to create is the only effect.
#
# The rule is the one this repository already uses for exactly this problem in
# ``events.services.resolve_unmatched_aggregates``: case/whitespace-normalized
# title equality plus the same calendar date, exact on both axes, with anything
# ambiguous left unresolved and reported rather than guessed.  No fuzzy,
# ranked, partial or scored matching exists here or may be added.

EXISTING_EVENT_MATCHED = "existing_event_matched"
EXISTING_EVENT_AMBIGUOUS = "existing_event_ambiguous"
EXISTING_EVENT_NONE = "no_existing_event"
EXISTING_EVENT_DATE_UNUSABLE = "provider_event_date_unusable"


@dataclass(frozen=True, slots=True)
class ExistingEventMatch:
    """What the index found for one provider event. Never a ranked guess."""

    outcome: str
    event: Event | None
    date: str | None
    candidate_total: int
    other_dates_with_this_title: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return self.outcome == EXISTING_EVENT_MATCHED


class ExistingEventIndex:
    """Events already in this database, keyed by their exact date and title.

    Built once per discovery run so a 166-event export does not issue a query
    per candidate.
    """

    def __init__(self, events: Any = None) -> None:
        self._by_dated_title: dict[tuple[str, str], list[Event]] = {}
        self._dates_by_title: dict[str, set[str]] = {}
        for event in Event.objects.select_related("source_identity") if events is None else events:
            date = canonical_event_date(event.source_identity.source_key)
            if date is None:
                continue
            title = normalize_event_title(event.title)
            self._by_dated_title.setdefault((date, title), []).append(event)
            self._dates_by_title.setdefault(title, set()).add(date)

    def match(self, *, title: str, start_at: str) -> ExistingEventMatch:
        """Find the one existing event a provider event's date and title prove.

        Exactly one candidate resolves.  Several events sharing both the date
        and the normalized title is ambiguous: the caller reports it and creates
        nothing, because a wrong pick here would silently fold two real events
        into one.  Zero candidates means genuinely new, and the caller mints an
        identity as before.
        """

        normalized = normalize_event_title(title)
        other_dates = tuple(sorted(self._dates_by_title.get(normalized, ())))
        date = provider_event_date(start_at)
        if date is None:
            return ExistingEventMatch(EXISTING_EVENT_DATE_UNUSABLE, None, None, 0, other_dates)
        candidates = self._by_dated_title.get((date, normalized), [])
        remaining = tuple(sorted(other for other in other_dates if other != date))
        if len(candidates) == 1:
            return ExistingEventMatch(EXISTING_EVENT_MATCHED, candidates[0], date, 1, remaining)
        if len(candidates) > 1:
            return ExistingEventMatch(
                EXISTING_EVENT_AMBIGUOUS, None, date, len(candidates), remaining
            )
        return ExistingEventMatch(EXISTING_EVENT_NONE, None, date, 0, remaining)


# --------------------------------------------------------------------------
# One occurrence, matched by its start and its title
# --------------------------------------------------------------------------
#
# ``ExistingEventIndex`` above can only see an event whose *source key* carries a
# date, so every provider-minted event (``evt-...``) and every older podcast-style
# manifest entry is invisible to it.  This index reads the ``Event`` row's own
# ``start_datetime`` instead, so it sees every event in the database.  It answers
# "is this the same occurrence?" for two import boundaries that need exactly that:
# the legacy ``_data/events.yaml`` leg (``scripts/prod/import_events.py``) and the
# registrant importer's fallback for a provider event with no identity of its own.
#
# Two tiers, tried in order, each exact on both of its axes:
#
# * the same start instant and the same normalized title;
# * the same Europe/Berlin calendar date and the same normalized title -- the
#   legacy site wrote a nominal ``17:00`` for events Luma actually holds at
#   another hour on that day, and production's registrant adapter matched those
#   by date and title for the same reason.
#
# A tier with more than one candidate is ambiguous and resolves nothing; the
# next tier is not consulted, because a looser rule must never break a tie the
# stricter one could not.  Nothing is fuzzy, ranked or scored.

OCCURRENCE_MATCH_START_AND_TITLE = "start_and_title"
OCCURRENCE_MATCH_DATE_AND_TITLE = "date_and_title"
OCCURRENCE_MATCH_AMBIGUOUS = "ambiguous"
OCCURRENCE_MATCH_NONE = "none"
OCCURRENCE_MATCH_UNDATED = "undated"

#: The calendar every DataTalks.Club event date is read in.
EVENT_CALENDAR_TIMEZONE = ZoneInfo("Europe/Berlin")

#: Punctuation to ignore. ``+`` and ``#`` are kept: "C++", "C#" and "C" are three
#: different subjects, not three spellings of one.
_NON_WORD = re.compile(r"[^\w\s+#]|_")


def normalize_match_title(title: str) -> str:
    """Fold a title to what two spellings of one event's title share.

    Case, punctuation (``:``, ``?``, ``&``, typographic apostrophes, hyphens --
    but not ``+`` or ``#``), whitespace and a single leading "The" are ignored -- the differences seen
    between Luma's and the legacy site's spelling of the same event ("The Data
    Consultant Playbook From Code ..." against "Data Consultant Playbook: From
    Code ...").  Every word is otherwise kept, in order.
    """

    folded = unicodedata.normalize("NFKC", title).casefold()
    words = _NON_WORD.sub(" ", folded).split()
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def parse_event_instant(value: Any) -> datetime | None:
    """An aware instant from a provider/legacy start value, or ``None``.

    An offset-less value is not guessed at here: callers that know their
    source's timezone make it aware before they ask.
    """

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


@dataclass(frozen=True, slots=True)
class OccurrenceMatch:
    outcome: str
    event: Event | None = None
    candidate_total: int = 0

    @property
    def matched(self) -> bool:
        return self.event is not None


class EventOccurrenceIndex:
    """Every event in this database, keyed by start instant/calendar date and title."""

    def __init__(self, events: Any = None) -> None:
        self._by_start: dict[tuple[datetime, str], list[Event]] = {}
        self._by_date: dict[tuple[str, str], list[Event]] = {}
        for event in Event.objects.all() if events is None else events:
            self.add(event)

    def add(self, event: Event) -> None:
        start = event.start_datetime
        if start is None or start.tzinfo is None:
            return
        title = normalize_match_title(event.title)
        if not title:
            return
        self._by_start.setdefault((start, title), []).append(event)
        local_date = start.astimezone(EVENT_CALENDAR_TIMEZONE).date().isoformat()
        self._by_date.setdefault((local_date, title), []).append(event)

    def match(self, *, title: str, start: Any) -> OccurrenceMatch:
        instant = parse_event_instant(start)
        normalized = normalize_match_title(title)
        if instant is None or not normalized:
            return OccurrenceMatch(OCCURRENCE_MATCH_UNDATED)
        tiers = (
            (OCCURRENCE_MATCH_START_AND_TITLE, self._by_start.get((instant, normalized), [])),
            (
                OCCURRENCE_MATCH_DATE_AND_TITLE,
                self._by_date.get(
                    (
                        instant.astimezone(EVENT_CALENDAR_TIMEZONE).date().isoformat(),
                        normalized,
                    ),
                    [],
                ),
            ),
        )
        for outcome, candidates in tiers:
            distinct = {event.pk: event for event in candidates}
            if len(distinct) == 1:
                return OccurrenceMatch(outcome, next(iter(distinct.values())), 1)
            if len(distinct) > 1:
                return OccurrenceMatch(OCCURRENCE_MATCH_AMBIGUOUS, None, len(distinct))
        return OccurrenceMatch(OCCURRENCE_MATCH_NONE)


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

    ``resolve_event`` is how that identity resolves, and it is optional for a
    reason: every event this module has ever resolved against so far --
    Luma's -- was minted with its own provider source identity
    (:func:`provider_source_identity`, above), so the default (``None``)
    keeps doing exactly what it always did, unchanged, for every existing
    caller. A provider whose export instead references events this database
    already holds under a *different* source identity (Eventbrite's numeric
    ids resolve against the reviewed legacy manifest triple, not a
    provider-minted one -- see ``scripts/prod/registration_sources/
    eventbrite_registrants.py``) supplies its own resolver here rather than
    forcing a second identity scheme into this module or a second copy
    of the consolidation logic below. Returns ``None`` for "no identity yet",
    the same outcome an unresolved default lookup reports.
    """

    external_event_identifier: str
    read_rows: Callable[[], tuple[RegistrantRow, ...]]
    resolve_event: Callable[[], Event | None] | None = None
    #: ``(title, start)`` of the provider event itself -- public event-level facts,
    #: never an attendee value.  Asked for only when the event's own identity
    #: does not resolve, to try :func:`resolve_pending_events`' title-and-start
    #: fallback.  ``None`` means the provider export carries no such facts
    #: (Eventbrite's registrant CSVs do not), so there is no fallback to try.
    read_event_metadata: Callable[[], tuple[str, str]] | None = None


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

    account = (
        User.objects.filter(identity__normalized_email=normalized_email).order_by("pk").first()
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


def _resolve_event(
    *,
    provider: str,
    external_event_identifier: str,
    resolve_event: Callable[[], Event | None] | None,
) -> Event | None:
    """The default provider-source-identity lookup, or a reader-supplied override.

    Both call the same two functions on the default path, so a caller that
    never sets ``resolve_event`` sees this behave exactly as it always did.
    """

    if resolve_event is not None:
        return resolve_event()
    source = provider_source_identity(
        provider=provider, external_event_identifier=external_event_identifier
    )
    try:
        return resolve_source_identity(
            repository=source.repository,
            revision=source.revision,
            source_key=source.source_key,
            follow_merges=True,
        )
    except EventIdentityNotFound:
        return None


@dataclass(frozen=True, slots=True)
class FallbackMatch:
    """One provider event attached to an existing event by its title and start.

    Event-level, public facts only: the provider's own event id (part of its
    public URL), the event title and start, and the event it matched.
    """

    external_event_identifier: str
    title: str
    start_at: str
    matched_by: str
    public_id: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "external_event_identifier": self.external_event_identifier,
            "title": self.title,
            "start_at": self.start_at,
            "matched_by": self.matched_by,
            "matched_event_public_id": self.public_id,
        }


@dataclass(frozen=True, slots=True)
class PendingResolution:
    """Which ``Event`` each pending provider event resolves to, decided once per run."""

    events: dict[str, Event | None]
    fallback_matches: tuple[FallbackMatch, ...] = ()
    fallback_refusals: tuple[dict[str, Any], ...] = ()


def resolve_pending_events(
    *, provider: str, pending: Iterable[PendingEventRegistrants], refresh: bool = False
) -> PendingResolution:
    """Resolve every pending event: its own identity first, then the fallback.

    The fallback is production's registrant adapter's rule brought into the
    reviewed importer (``~/prod/events/registrations-2026-11-15-v2/
    production-run/import-adapter.py``): a provider event whose own identity
    does not resolve -- because discovery recognised it as an event the reviewed
    manifest already describes, and so minted nothing -- attaches to the one
    existing event with the same start and normalized title
    (:class:`EventOccurrenceIndex`).  It is refused, and the event stays under
    ``awaiting_identity_events``, when

    * the match is ambiguous (several candidates in the deciding tier);
    * the matched event is also resolved directly by another event in this run,
      or matched by a second fallback -- a ``--refresh`` replaces one event's
      provider rows wholesale, so two sources writing one event would erase
      each other.

    Every accepted and every refused fallback is reported; none is silent.
    Only identity lookups and the event-level metadata reader run here: no
    registrant row is read, and an event a resumed run will skip (completed,
    without ``refresh``) is not asked for its metadata either, so its file
    stays closed.
    """

    items = tuple(pending)
    skipped: set[str] = set()
    if not refresh:
        skipped = set(
            EventRegistrantImportProgress.objects.filter(
                provider=provider,
                completed=True,
                external_event_identifier__in=[item.external_event_identifier for item in items],
            ).values_list("external_event_identifier", flat=True)
        )
    events: dict[str, Event | None] = {}
    for item in items:
        events[item.external_event_identifier] = _resolve_event(
            provider=provider,
            external_event_identifier=item.external_event_identifier,
            resolve_event=item.resolve_event,
        )
    claimed = Counter(event.pk for event in events.values() if event is not None)

    index: EventOccurrenceIndex | None = None
    proposals: list[tuple[PendingEventRegistrants, str, str, Event, str]] = []
    refusals: list[dict[str, Any]] = []
    for item in items:
        if events[item.external_event_identifier] is not None:
            continue
        if item.read_event_metadata is None or item.external_event_identifier in skipped:
            continue
        title, start_at = item.read_event_metadata()
        if not title or not start_at:
            continue
        if index is None:
            index = EventOccurrenceIndex()
        match = index.match(title=title, start=start_at)
        if match.event is not None:
            proposals.append((item, title, start_at, match.event, match.outcome))
        elif match.outcome == OCCURRENCE_MATCH_AMBIGUOUS:
            refusals.append(
                {
                    "external_event_identifier": item.external_event_identifier,
                    "title": title,
                    "start_at": start_at,
                    "reason": "ambiguous",
                    "candidate_event_total": match.candidate_total,
                }
            )

    proposed = Counter(event.pk for _, _, _, event, _ in proposals)
    # A provider event that already holds its target from an earlier run keeps
    # it: a newcomer proposing the same event is what gets refused, not both.
    incumbents = {
        (external_event_identifier, event_pk)
        for external_event_identifier, event_pk in EventRegistrantImportProgress.objects.filter(
            provider=provider,
            event__isnull=False,
            external_event_identifier__in=[
                item.external_event_identifier for item, *_ in proposals
            ],
        ).values_list("external_event_identifier", "event_id")
    }
    accepted: list[FallbackMatch] = []
    for item, title, start_at, event, matched_by in proposals:
        incumbent = (item.external_event_identifier, event.pk) in incumbents
        if (
            claimed[event.pk]
            or (proposed[event.pk] > 1 and not incumbent)
            or _held_by_another_source(
                event=event,
                provider=provider,
                external_event_identifier=item.external_event_identifier,
            )
        ):
            refusals.append(
                {
                    "external_event_identifier": item.external_event_identifier,
                    "title": title,
                    "start_at": start_at,
                    "reason": "event_claimed_by_another_source",
                    "matched_event_public_id": event.public_id,
                }
            )
            continue
        events[item.external_event_identifier] = event
        accepted.append(
            FallbackMatch(
                external_event_identifier=item.external_event_identifier,
                title=title,
                start_at=start_at,
                matched_by=matched_by,
                public_id=event.public_id,
            )
        )
    return PendingResolution(
        events=events,
        fallback_matches=tuple(accepted),
        fallback_refusals=tuple(refusals),
    )


def _rows_owned_by(*, event: Event, provider: str, external_event_identifier: str) -> Any:
    """The provider rows on ``event`` a refresh of this provider event replaces."""

    return EventRegistration.objects.filter(
        event=event,
        provider=provider,
        source_event_identifier__in=(external_event_identifier, ""),
    )


def _held_by_another_source(*, event: Event, provider: str, external_event_identifier: str) -> bool:
    """Whether another provider event already holds ``event``, in any earlier run.

    A completed progress row naming ``event`` for a different provider event id
    is a claim even when that event is skipped on this run; so is any provider
    row on ``event`` not attributed to this provider event (an unattributed row
    cannot be proved to be ours).
    """

    claimed = (
        EventRegistrantImportProgress.objects.filter(provider=provider, event=event)
        .exclude(external_event_identifier=external_event_identifier)
        .exists()
    )
    if claimed:
        return True
    return (
        EventRegistration.objects.filter(event=event, provider=provider)
        .exclude(source_event_identifier=external_event_identifier)
        .exists()
    )


def _already_resolved(event: Event | None) -> Callable[[], Event | None]:
    """A ``resolve_event`` that returns what :func:`resolve_pending_events` decided."""

    def resolve() -> Event | None:
        return event

    return resolve


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
    resolve_event: Callable[[], Event | None] | None = None,
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

    event = _resolve_event(
        provider=provider,
        external_event_identifier=external_event_identifier,
        resolve_event=resolve_event,
    )
    if event is None:
        # Not yet discovered by create_provider_event_identity, above
        # (5.2 in the ingest inventory), or -- for a reader with its own
        # resolver -- not resolved by that mapping. Reported, not created
        # here -- this module never mints an Event identity itself.
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
            # Wholesale for *this provider event*, and inside the same
            # transaction as the rewrite: a refreshed export is not append-only,
            # so the rows it no longer carries have to stop being facts at the
            # same moment the new ones start being facts.  Rows another provider
            # event wrote to the same Event are not this export's to replace;
            # unattributed rows (written before attribution existed) are, as
            # they always were.  Identities are untouched.
            replaced, _ = _rows_owned_by(
                event=event, provider=provider, external_event_identifier=external_event_identifier
            ).delete()
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
                source_event_identifier=external_event_identifier,
                status=row.status,
                registered_at=_parse_registered_at(row.registered_at_raw),
            )
            written += 1
        progress.completed = True
        progress.event = event
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
    fallback_matched_events: tuple[FallbackMatch, ...] = ()
    fallback_refused_events: tuple[dict[str, Any], ...] = ()

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
            **_fallback_report(self.fallback_matched_events, self.fallback_refused_events),
        }


def _fallback_report(
    matches: tuple[FallbackMatch, ...], refusals: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    """Every title-and-start fallback this run accepted or refused, by name."""

    return {
        "fallback_matched_total": len(matches),
        "fallback_matched_events": [match.as_dict() for match in matches],
        "fallback_refused_total": len(refusals),
        "fallback_refused_events": list(refusals),
    }


#: A refresh may lose at most this many people from one provider event ...
REFRESH_EVENT_REMOVAL_FLOOR = 5
#: ... or this share of the people it holds, whichever is larger,
REFRESH_EVENT_REMOVAL_SHARE = 0.10
#: and at most this share of everyone the refreshed events hold overall,
#: unless the operator passes ``allow_large_removal``.
REFRESH_TOTAL_REMOVAL_SHARE = 0.02


def _event_removal_is_large(*, existing: int, removed: int) -> bool:
    return removed > max(REFRESH_EVENT_REMOVAL_FLOOR, existing * REFRESH_EVENT_REMOVAL_SHARE)


def _removal_guard_report(plan: PlanReport) -> dict[str, Any]:
    total_large = plan.registrations_removed > max(
        REFRESH_EVENT_REMOVAL_FLOOR, plan.registrations_existing * REFRESH_TOTAL_REMOVAL_SHARE
    )
    return {
        "would_refuse": bool(plan.large_removal_events) or total_large,
        "total_removal_exceeds_share": total_large,
        "event_removal_floor": REFRESH_EVENT_REMOVAL_FLOOR,
        "event_removal_share": REFRESH_EVENT_REMOVAL_SHARE,
        "total_removal_share": REFRESH_TOTAL_REMOVAL_SHARE,
        "large_removal_events": list(plan.large_removal_events),
    }


class RefreshRemovalRefused(RegistrantImportError):
    """A refresh that would lose more registrations than the guard allows.

    Carries the dry-run plan (counts and provider event ids only) so the caller
    can report which events tripped it.
    """

    def __init__(self, plan: PlanReport) -> None:
        super().__init__("refresh_removal_exceeds_threshold")
        self.plan = plan


def import_registrants(
    *,
    provider: str,
    pending: Iterable[PendingEventRegistrants],
    refresh: bool = False,
    allow_large_removal: bool = False,
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
    if refresh and not allow_large_removal:
        # A refresh deletes what it does not re-read. Plan it first -- the same
        # readers and gates, no writes -- and refuse before touching a row if
        # the new export would lose more people than a refresh plausibly should
        # (an empty or partial export, a wrong directory).
        plan = plan_registrants(provider=provider, pending=events, refresh=True)
        if _removal_guard_report(plan)["would_refuse"]:
            raise RefreshRemovalRefused(plan)
    resolution = resolve_pending_events(provider=provider, pending=events, refresh=refresh)

    events_completed = events_refreshed = events_already_completed = 0
    awaiting_identity: list[str] = []
    rows_written = rows_skipped = rows_replaced = 0
    matched_account = matched_prior = new_identity = 0
    for item in events:
        resolved = resolution.events.get(item.external_event_identifier)
        outcome = _import_one_event(
            provider=provider,
            external_event_identifier=item.external_event_identifier,
            read_rows=item.read_rows,
            resolve_event=_already_resolved(resolved),
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
        fallback_matched_events=resolution.fallback_matches,
        fallback_refused_events=resolution.fallback_refusals,
    )


@dataclass(frozen=True, slots=True)
class EventPlanOutcome:
    """What one event's dry-run decided, mirroring :func:`_import_one_event`.

    Same gates in the same order: a completed event (without refresh) is
    ``already_completed`` and its rows are never read; an event whose identity
    has not been discovered is ``no_identity_yet`` and its rows are never read;
    only past both gates is the real reader invoked, so a malformed file fails
    here exactly as it would during apply.  Nothing is written: identities are
    classified with the same lookups :func:`resolve_registrant_identity` uses,
    minus the create, and refresh diffs are computed from reads.
    """

    external_event_identifier: str
    status: str  # "planned", "already_completed", "no_identity_yet"
    rows_total: int = 0
    rows_writable: int = 0
    rows_skipped: int = 0
    #: Rows a refresh deletes and rewrites: every row this provider event owns.
    #: Gross, not a loss -- an unchanged person is replaced by an identical row.
    registrations_replaced: int = 0
    #: People this provider event holds that the new export no longer carries:
    #: the registrations a refresh would really lose.
    registrations_removed: int = 0
    registrations_existing: int = 0
    registrations_unchanged: int = 0
    registrations_changed: int = 0
    matched_account_total: int = 0
    matched_prior_identity_total: int = 0
    new_identity_total: int = 0


def _plan_one_event(
    *,
    provider: str,
    external_event_identifier: str,
    read_rows: Callable[[], tuple[RegistrantRow, ...]],
    resolve_event: Callable[[], Event | None] | None = None,
    refresh: bool = False,
) -> EventPlanOutcome:
    progress = EventRegistrantImportProgress.objects.filter(
        provider=provider, external_event_identifier=external_event_identifier
    ).first()
    if progress is not None and progress.completed and not refresh:
        return EventPlanOutcome(
            external_event_identifier=external_event_identifier,
            status="already_completed",
        )

    event = _resolve_event(
        provider=provider,
        external_event_identifier=external_event_identifier,
        resolve_event=resolve_event,
    )
    if event is None:
        return EventPlanOutcome(
            external_event_identifier=external_event_identifier,
            status="no_identity_yet",
        )

    rows = read_rows()

    writable = skipped = matched_account = matched_prior = new_identity = 0
    proposed: dict[str, str] = {}
    for row in rows:
        email = row.normalized_email
        if email is None:
            skipped += 1
            continue
        writable += 1
        account = User.objects.filter(identity__normalized_email=email).order_by("pk").first()
        if account is not None:
            matched_account += 1
            # An account's normalized_email is the consolidation key; the
            # empty fallback can never match a proposed row (none is empty).
            key = normalized_email_of(account) or email
        else:
            prior = (
                EventRegistrantIdentity.objects.filter(normalized_email=email, account__isnull=True)
                .order_by("id")
                .first()
            )
            if prior is not None:
                matched_prior += 1
            else:
                new_identity += 1
            key = email
        proposed[key] = row.status

    replaced = removed = existing_total = unchanged = changed = 0
    if progress is not None and progress.completed and refresh:
        existing: dict[str, str] = {}
        for registration in _rows_owned_by(
            event=event, provider=provider, external_event_identifier=external_event_identifier
        ).select_related("identity__account"):
            identity = registration.identity
            if identity.account_id is not None and identity.account is not None:
                existing_key = normalized_email_of(identity.account)
            else:
                existing_key = identity.normalized_email
            replaced += 1
            if existing_key:
                existing[existing_key] = registration.status
        existing_total = len(existing)
        removed = len(set(existing) - set(proposed))
        for email, status in proposed.items():
            if existing.get(email) == status:
                unchanged += 1
            else:
                changed += 1

    return EventPlanOutcome(
        external_event_identifier=external_event_identifier,
        status="planned",
        rows_total=len(rows),
        rows_writable=writable,
        rows_skipped=skipped,
        registrations_replaced=replaced,
        registrations_removed=removed,
        registrations_existing=existing_total,
        registrations_unchanged=unchanged,
        registrations_changed=changed,
        matched_account_total=matched_account,
        matched_prior_identity_total=matched_prior,
        new_identity_total=new_identity,
    )


@dataclass(frozen=True, slots=True)
class PlanReport:
    """The dry-run's aggregate: counts and event ids, never attendee values."""

    provider: str
    events_total: int
    events_planned: int
    events_already_completed: int
    events_awaiting_identity: int
    awaiting_identity_events: tuple[str, ...]
    rows_total: int
    rows_writable: int
    rows_skipped: int
    matched_account_total: int
    matched_prior_identity_total: int
    new_identity_total: int
    registrations_removed: int
    registrations_unchanged: int
    registrations_changed: int
    registrations_replaced: int = 0
    registrations_existing: int = 0
    large_removal_events: tuple[dict[str, Any], ...] = ()
    fallback_matched_events: tuple[FallbackMatch, ...] = ()
    fallback_refused_events: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "events_total": self.events_total,
            "events_planned": self.events_planned,
            "events_already_completed": self.events_already_completed,
            "events_awaiting_identity": self.events_awaiting_identity,
            "awaiting_identity_events": list(self.awaiting_identity_events),
            "rows_total": self.rows_total,
            "rows_writable": self.rows_writable,
            "rows_skipped": self.rows_skipped,
            "matched_account_total": self.matched_account_total,
            "matched_prior_identity_total": self.matched_prior_identity_total,
            "new_identity_total": self.new_identity_total,
            # Gross: rows a refresh deletes and rewrites, unchanged people included.
            "registrations_replaced": self.registrations_replaced,
            # Net: people currently held whom the new export no longer carries.
            "registrations_removed": self.registrations_removed,
            "registrations_existing": self.registrations_existing,
            "refresh_removal_guard": _removal_guard_report(self),
            "registrations_unchanged": self.registrations_unchanged,
            "registrations_changed": self.registrations_changed,
            **_fallback_report(self.fallback_matched_events, self.fallback_refused_events),
        }


def plan_registrants(
    *, provider: str, pending: Iterable[PendingEventRegistrants], refresh: bool = False
) -> PlanReport:
    """Dry-run companion to :func:`import_registrants`: validate, compute, write nothing.

    Every gate and every reader call is the apply path's own -- the same
    progress skip, the same identity resolution, the same row reader -- so a
    malformed export or unresolved target is refused here with the identical
    refusal apply would raise, and a valid refresh plan's aggregate diff
    (added as ``registrations_changed`` minus re-presented unchanged rows,
    plus genuinely new addresses; removed; unchanged) is what apply would
    carry out.  Only reads happen: no identity is created, no registration or
    progress row is touched.
    """

    items = tuple(pending)
    resolution = resolve_pending_events(provider=provider, pending=items, refresh=refresh)
    outcomes = [
        _plan_one_event(
            provider=provider,
            external_event_identifier=pending_event.external_event_identifier,
            read_rows=pending_event.read_rows,
            resolve_event=_already_resolved(
                resolution.events.get(pending_event.external_event_identifier)
            ),
            refresh=refresh,
        )
        for pending_event in items
    ]
    awaiting = tuple(
        outcome.external_event_identifier
        for outcome in outcomes
        if outcome.status == "no_identity_yet"
    )
    return PlanReport(
        provider=provider,
        events_total=len(outcomes),
        events_planned=sum(1 for outcome in outcomes if outcome.status == "planned"),
        events_already_completed=sum(
            1 for outcome in outcomes if outcome.status == "already_completed"
        ),
        events_awaiting_identity=len(awaiting),
        awaiting_identity_events=awaiting,
        rows_total=sum(outcome.rows_total for outcome in outcomes),
        rows_writable=sum(outcome.rows_writable for outcome in outcomes),
        rows_skipped=sum(outcome.rows_skipped for outcome in outcomes),
        matched_account_total=sum(outcome.matched_account_total for outcome in outcomes),
        matched_prior_identity_total=sum(
            outcome.matched_prior_identity_total for outcome in outcomes
        ),
        new_identity_total=sum(outcome.new_identity_total for outcome in outcomes),
        registrations_removed=sum(outcome.registrations_removed for outcome in outcomes),
        registrations_unchanged=sum(outcome.registrations_unchanged for outcome in outcomes),
        registrations_changed=sum(outcome.registrations_changed for outcome in outcomes),
        registrations_replaced=sum(outcome.registrations_replaced for outcome in outcomes),
        registrations_existing=sum(outcome.registrations_existing for outcome in outcomes),
        large_removal_events=tuple(
            {
                "external_event_identifier": outcome.external_event_identifier,
                "registrations_existing": outcome.registrations_existing,
                "registrations_removed": outcome.registrations_removed,
            }
            for outcome in outcomes
            if _event_removal_is_large(
                existing=outcome.registrations_existing, removed=outcome.registrations_removed
            )
        ),
        fallback_matched_events=resolution.fallback_matches,
        fallback_refused_events=resolution.fallback_refusals,
    )
