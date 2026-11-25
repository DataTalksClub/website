#!/usr/bin/env python3
"""Import current Event identities and content into the database.

Provider registrations are owned by ``scripts/prod/import_event_registrants.py``.
Legacy source translation stays in import scripts; runtime event reads use
database rows directly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False

# These reviewed staging inputs live outside this repository, at
# ~/prod/dtc-data/content-staging/ -- the owner's explicit instruction: "let's
# not have it in our code. move it outside." (issue #253 discussion; see the
# eventbrite descriptions leg below for the earlier instance of the same move,
# and _docs/architecture/database-only-content.md for the full picture).
_CONTENT_STAGING_ROOT = Path.home() / "prod" / "dtc-data" / "content-staging"
IDENTITY_MANIFEST_PATH = _CONTENT_STAGING_ROOT / "event_identity_manifest.json"
LUMA_RELATIVE_SOURCE = Path(".local/migration-data/events/luma-aggregate-v1")

EVENT_CONTENT_PATH = _CONTENT_STAGING_ROOT / "public_projection" / "events.json"
NEW_EVENT_CONTENT_PATH = _CONTENT_STAGING_ROOT / "luma_event_descriptions.json"
# Deliberately *not* under temporary/content/, unlike every sibling staging
# artifact above -- the owner's explicit instruction: "let's not have it in
# our code. move it outside." Built by scripts/build_eventbrite_descriptions.py
# from external raw Eventbrite content that never enters this repository
# either; see that script's module docstring.
EVENTBRITE_DESCRIPTIONS_PATH = (
    Path.home()
    / "prod"
    / "dtc-data"
    / "eventbrite-content"
    / "staging"
    / "eventbrite_descriptions.json"
)


class EventImportError(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


class IncompletePublishedEvents(EventImportError):
    """The run would leave a published event without a type or a speaker.

    Carries the offending events' row ids, public ids and titles -- public
    facts about the event, nothing about anyone registered for it.
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        super().__init__("published_event_incomplete")
        self.events = events


from scripts.prod.target import main_checkout_root  # noqa: E402


def _main_checkout_root() -> Path:
    """The main checkout, where the protected registration exports live."""

    return main_checkout_root(PROJECT_ROOT, error_cls=EventImportError)


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def import_identities(
    *, manifest: Path | None = None, content: Path | None = None, apply: bool = True
) -> dict[str, Any]:
    """Import the reviewed identity manifest atomically.

    ``content`` is the reviewed content file: an event is created already
    carrying the type and speakers its record names, because no event may exist
    without them (:func:`events.identity.create_event_identity`).
    """

    from events.identity import EventIdentityError
    from scripts.prod.identity_manifest import import_identity_manifest

    try:
        report = import_identity_manifest(
            path=manifest or IDENTITY_MANIFEST_PATH,
            content_path=content or EVENT_CONTENT_PATH,
            dry_run=not apply,
        )
    except (EventIdentityError, OSError, ValueError) as error:
        raise EventImportError("identity_manifest_invalid") from error
    return {
        "events": report.event_total,
        "events_created": report.events_created,
        "events_updated": report.events_updated,
        "replayed": report.replayed,
        "applied": apply,
    }


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def import_content(*, source: Path | None = None, apply: bool = True) -> dict[str, Any]:
    """Attach the reviewed content records to the identities imported above.

    Runs after :func:`import_identities` and never before it: this reconciles
    against identities that already exist, and a record naming an identity the
    database does not hold is a refusal rather than a new event.
    """

    from events.content_import import EventContentImportError, import_event_content

    try:
        report = import_event_content(path=source or EVENT_CONTENT_PATH, dry_run=not apply)
    except (EventContentImportError, OSError, ValueError) as error:
        raise EventImportError("event_content_invalid") from error
    return {
        "events": report.total,
        # How many of the 421 carry a reviewed description at all. The rest
        # render a page with no description region, which is correct.
        "described": report.described,
        "created": report.created,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "speakers": report.speakers,
        "links": report.links,
        "replayed": report.replayed,
        "applied": apply,
    }


def import_new_content(*, source: Path | None = None, apply: bool = True) -> dict[str, Any]:
    """Attach staged descriptions to the identities discovered below.

    Runs after :func:`discover_new_provider_events`, never before it, for the
    same reason :func:`import_content` runs after the manifest: a record naming
    an identity the database does not hold is a refusal rather than a new event.

    A missing artifact is a normal state, not a failure. It exists only while
    there is new content waiting to land -- an operator builds it with
    ``scripts/build_luma_event_descriptions.py --write`` once the link review
    and the type review it reports are clean, and it is reported as ``absent``
    when nobody has.
    """

    from events.content_import import EventContentImportError, import_new_event_content

    path = source or NEW_EVENT_CONTENT_PATH
    if not path.is_file():
        return {"present": False, "applied": apply}
    try:
        report = import_new_event_content(path=path, dry_run=not apply)
    except (EventContentImportError, OSError, ValueError) as error:
        raise EventImportError("new_event_content_invalid") from error
    return {
        "present": True,
        "events": report.total,
        "described": report.described,
        "created": report.created,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "speakers": report.speakers,
        "links": report.links,
        "replayed": report.replayed,
        "applied": apply,
    }


def import_eventbrite_descriptions(
    *, source: Path | None = None, apply: bool = True
) -> dict[str, Any]:
    """Let a cleaned Eventbrite description win outright over the Jekyll one it replaces.

    Runs after :func:`import_content`, never before it: this overwrites a
    ``description_html``/``description_text`` an event's ``EventContent`` row
    already carries, so the row has to exist first. This is description
    *authoring* precedence -- a third concern from both content bootstrap above
    and the registration-count legs below -- reported under its own key rather
    than folded into either.

    The product owner's ruling: "eventbrite wins over jekyll. but we remove
    'about the speaker' part and about dtc footer too." Full replacement, not
    fill-only-if-missing -- see :func:`events.eventbrite_content.
    apply_eventbrite_descriptions` for the resolution and overwrite mechanics,
    and :mod:`events.eventbrite_content` for what is stripped and why.

    A missing artifact is a normal state, not a failure: it exists only after
    an operator has run ``scripts/build_eventbrite_descriptions.py --write``
    against the external raw Eventbrite content and identity resolution (both
    outside this repository, at ``~/prod/dtc-data/``), and is reported as
    ``absent`` when nobody has.

    Expect ``event_content``'s own report, just above this leg's in the run
    output, to show the same events as ``updated`` on every replay rather than
    ``unchanged``: that step replays the *reviewed* Jekyll-sourced record,
    which genuinely disagrees with a row this leg has already overridden, so
    it correctly restores it -- and this leg then correctly overrides it back.
    Final state after one full run is always the cleaned Eventbrite text; the
    two legs simply keep re-asserting their own layer every time, by design.
    """

    from events.eventbrite_content import (
        EventbriteDescriptionError,
        apply_eventbrite_descriptions,
    )

    path = source or EVENTBRITE_DESCRIPTIONS_PATH
    if not path.is_file():
        return {"present": False, "applied": apply}
    try:
        report = apply_eventbrite_descriptions(path=path, dry_run=not apply)
    except (EventbriteDescriptionError, OSError, ValueError) as error:
        raise EventImportError("eventbrite_descriptions_invalid") from error
    return {
        "present": True,
        "events": report.total,
        "applied_total": report.applied,
        "unchanged": report.unchanged,
        "no_identity": report.no_identity,
        "no_content_yet": report.no_content_yet,
        "no_eventbrite_description": report.no_eventbrite_description,
        "applied": apply,
    }


# --------------------------------------------------------------------------
# New-event identity discovery
# --------------------------------------------------------------------------
#
# The reviewed manifest (above) is frozen and hand-checked; it never grows on
# its own.  A genuinely new event -- one a fresh Luma/Eventbrite export names
# that neither the manifest nor any prior provider-registration run has ever
# seen -- previously had no path to becoming a real ``Event`` row at all:
# ``events.models.create_event_identity`` had zero callers anywhere outside
# tests.  This section is that path.
#
# Minting an identity is safe, reviewable plumbing -- title and a canonical
# path -- and registration rows attach to that identity in the separate
# registrant ingest.
#
# ``scripts.prod.registrant_import.ExistingEventIndex`` prevents provider
# discovery from creating a second Event for a row already in the database.


@dataclass(frozen=True, slots=True)
class EventMetadata:
    """The type and speakers a reviewed source names for one event."""

    event_type: str
    speakers: tuple[tuple[str, str], ...]


class EventMetadataIndex:
    """Type and speakers for provider events, read from ``_data/events.yaml``.

    An export event is described by the entry whose ``link`` names the same
    Luma event, or else by the single entry on the same Europe/Berlin date with
    the same normalized title.  An entry without a type or without speakers
    describes nothing, and two candidate entries describe nothing either.
    """

    def __init__(self) -> None:
        self.by_url: dict[str, EventMetadata] = {}
        self.by_day_title: dict[tuple[str, str], list[EventMetadata]] = {}

    @classmethod
    def from_events_yaml(cls, path: Path) -> EventMetadataIndex:
        import yaml

        from content.models import SyncedDocument
        from scripts.prod.registrant_import import normalize_match_title

        try:
            entries = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise EventImportError("events_yaml_unreadable") from error
        if not isinstance(entries, list):
            raise EventImportError("events_yaml_invalid")
        people_names = dict(
            SyncedDocument.objects.filter(
                source__slug="dtc-main-site", content_kind="people"
            ).values_list("slug", "title")
        )
        berlin = ZoneInfo("Europe/Berlin")
        index = cls()
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("time") is None:
                continue
            title = str(entry.get("title") or "").strip()
            event_type = str(entry.get("type") or "").strip()
            keys = [str(key).strip() for key in (entry.get("speakers") or []) if str(key).strip()]
            if not title or not event_type or not keys:
                continue
            try:
                starts_at = _parse_events_yaml_timestamp(entry["time"])
            except EventImportError:
                continue
            described = EventMetadata(
                event_type=event_type,
                speakers=tuple((key, people_names.get(key) or key) for key in keys),
            )
            url_key = _luma_url_key(entry.get("link"))
            if url_key is not None:
                index.by_url[url_key] = described
            day = starts_at.astimezone(berlin).date().isoformat()
            index.by_day_title.setdefault((day, normalize_match_title(title)), []).append(described)
        return index

    def lookup(self, item: Any) -> EventMetadata | None:
        from events.identity import provider_event_date
        from scripts.prod.registrant_import import normalize_match_title

        url_key = _luma_url_key(getattr(item, "event_url", None))
        if url_key is not None and url_key in self.by_url:
            return self.by_url[url_key]
        day = _provider_berlin_date(item.start_at) or provider_event_date(item.start_at or "")
        if day is None:
            return None
        candidates = self.by_day_title.get((day, normalize_match_title(item.title)), [])
        return candidates[0] if len(candidates) == 1 else None


def _provider_berlin_date(start_at: str | None) -> str | None:
    if not start_at:
        return None
    try:
        parsed = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat()


def discover_new_provider_events(
    *,
    provider: str,
    discovered: tuple[Any, ...],
    apply: bool = True,
    metadata: EventMetadataIndex | None = None,
) -> dict[str, Any]:
    """Create identities for provider events this database has never tracked.

    No event may exist without a type and a speaker
    (:func:`events.identity.create_event_identity`), and a provider export names
    neither.  ``metadata`` supplies them from a reviewed source -- the legacy
    ``_data/events.yaml`` entry with the same Luma link, or the same Berlin date
    and title.  A genuinely new event the source does not describe with both is
    not created: it is reported under ``pending_events`` and created by a later
    run once the source names its type and speakers.

    ``discovered`` items only need ``external_event_identifier``, ``title``,
    ``start_at`` and ``eligible_count`` attributes -- shaped for
    ``scripts.prod.registration_sources.luma_events.DiscoveredLumaEvent`` today,
    provider-agnostic by contract for whenever an Eventbrite export carries its
    own title source.

    An event is skipped, not created, when any of these is true:

    - This database already holds an ``Event`` under our own provider source
      identity (idempotent replay -- a second run creates nothing new).
    - Exactly one ``Event`` we already have shares the export event's calendar
      date and, case/whitespace-normalized, its exact title.  Reported under
      ``existing_event_total``: this is the same event, so there is nothing to
      create.  No identity is attached, no source key is rewritten and no
      registration count moves -- the export event is simply not new.
    - Several events share that date *and* that exact title, so which one it is
      cannot be proved.  Reported under ``ambiguous_total`` and left alone; a
      human resolves it, because folding two real events into one is worse than
      a duplicate.
    - The export carries no title for it (``item.title == ""`` -- a Luma event
      with zero registrations has no row to read one from).  Reported
      separately as ``no_metadata_total`` rather than silently dropped, since
      an operator needs to know an event exists that this step could not name.
    - The export's start timestamp carries no readable calendar date, so the
      match cannot even be attempted.  Reported as ``undated_total``: without a
      date, "already have it" is unanswerable, and creating would be a guess.

    Everything else is genuinely new and gets an identity, exactly as before.
    """

    from events.identity import (
        EventIdentityError,
        EventIdentityNotFound,
        canonical_detail_path,
        resolve_source_identity,
    )
    from scripts.prod.registrant_import import (
        EXISTING_EVENT_AMBIGUOUS,
        EXISTING_EVENT_DATE_UNUSABLE,
        EXISTING_EVENT_MATCHED,
        ExistingEventIndex,
        create_provider_event_identity,
        provider_source_identity,
    )

    index = ExistingEventIndex()
    created: list[dict[str, Any]] = []
    no_metadata: list[dict[str, Any]] = []
    existing_events: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    already_tracked = 0
    for item in discovered:
        source = provider_source_identity(
            provider=provider, external_event_identifier=item.external_event_identifier
        )
        try:
            existing = resolve_source_identity(
                repository=source.repository,
                revision=source.revision,
                source_key=source.source_key,
                follow_merges=True,
            )
        except EventIdentityNotFound:
            existing = None
        if existing is not None:
            already_tracked += 1
            continue
        if not item.title:
            # The provider event id is public (it is part of the event's public
            # Luma/Eventbrite URL), not attendee PII, so it is safe to report.
            no_metadata.append(
                {
                    "external_event_identifier": item.external_event_identifier,
                    "eligible_count": item.eligible_count,
                }
            )
            continue
        entry: dict[str, Any] = {
            "title": item.title,
            "start_at": item.start_at,
            "eligible_count": item.eligible_count,
        }
        match = index.match(title=item.title, start_at=item.start_at)
        if match.outcome == EXISTING_EVENT_MATCHED and match.event is not None:
            existing_events.append(
                {
                    **entry,
                    "external_event_identifier": item.external_event_identifier,
                    "matched_date": match.date,
                    "matched_event_public_id": match.event.public_id,
                    "matched_canonical_path": canonical_detail_path(match.event.content_id),
                    "reason": (
                        "Already have this event: one existing event shares this "
                        "export event's date and its exact normalized title. No "
                        "identity was created, attached or changed."
                    ),
                }
            )
            continue
        if match.outcome == EXISTING_EVENT_AMBIGUOUS:
            ambiguous.append(
                {
                    **entry,
                    "external_event_identifier": item.external_event_identifier,
                    "matched_date": match.date,
                    "candidate_event_total": match.candidate_total,
                    "reason": (
                        "Several existing events share this export event's date "
                        "and exact title, so which one it is cannot be proved. "
                        "Nothing was created; a human must decide."
                    ),
                }
            )
            continue
        if match.outcome == EXISTING_EVENT_DATE_UNUSABLE:
            undated.append(
                {
                    **entry,
                    "external_event_identifier": item.external_event_identifier,
                    "reason": (
                        "The export carries no readable calendar date for this "
                        "event, so whether we already have it cannot be "
                        "answered. Nothing was created."
                    ),
                }
            )
            continue
        # Genuinely new. Flag a same-title event on another date so an operator
        # can eyeball a rescheduled event; it does not change the decision,
        # because a different date is a different event until a human says so.
        if match.other_dates_with_this_title:
            entry["existing_event_dates_with_this_title"] = list(match.other_dates_with_this_title)
        described = metadata.lookup(item) if metadata is not None else None
        if described is None:
            # The provider id is public (it is part of the event's public URL).
            pending.append(
                {
                    **entry,
                    "external_event_identifier": item.external_event_identifier,
                    "reason": (
                        "New, but no reviewed source names its type and speakers, "
                        "and no event may exist without them. Nothing was created."
                    ),
                }
            )
            continue
        if not apply:
            created.append({**entry, "public_id": None, "canonical_path": None, "dry_run": True})
            continue
        try:
            event = create_provider_event_identity(
                provider=provider,
                external_event_identifier=item.external_event_identifier,
                title=item.title,
                start_at=item.start_at,
                event_type=described.event_type,
                speakers=described.speakers,
            )
        except EventIdentityError as error:
            raise EventImportError("provider_event_identity_invalid") from error
        created.append(
            {
                **entry,
                "public_id": event.public_id,
                "canonical_path": canonical_detail_path(event.content_id),
                "reason": (
                    "Auto-created: no reviewed identity-manifest entry, no "
                    "provider identity row, and no event sharing this date "
                    "and exact title existed at run time. Registrations attach "
                    "later through the registrant ingest."
                ),
            }
        )
    return {
        "provider": provider,
        "mechanism": "auto_created_event_identity",
        "candidate_total": len(discovered),
        "already_tracked_total": already_tracked,
        "existing_event_total": len(existing_events),
        "existing_events": existing_events,
        "ambiguous_total": len(ambiguous),
        "ambiguous_events": ambiguous,
        "undated_total": len(undated),
        "undated_events": undated,
        "no_metadata_total": len(no_metadata),
        "no_metadata_events": no_metadata,
        "pending_total": len(pending),
        "pending_events": pending,
        "created_total": len(created),
        "created_events": created,
        "applied": apply,
    }


def discover_new_luma_event_identities(
    *, luma_source: Path, apply: bool = True, events_yaml: Path | None = None
) -> dict[str, Any]:
    """Read a Luma export directory directly and create identities for new events.

    Unlike ``derive_luma`` (below), this never requires the export to match a
    previously pinned whole-tree checksum -- that pin exists to protect
    registration *counts* from silent drift, and identity creation writes no
    count.  See ``scripts.prod.registration_sources.luma_events.discover_luma_events``
    for the read and ``discover_new_provider_events`` for the create-or-skip
    decision.
    """

    from scripts.prod.registrant_import import RegistrantImportError
    from scripts.prod.registration_sources.luma_events import discover_luma_events

    try:
        discovered = discover_luma_events(luma_source)
    except RegistrantImportError as error:
        raise EventImportError("luma_discovery_failed") from error
    metadata = EventMetadataIndex.from_events_yaml(events_yaml) if events_yaml else None
    return discover_new_provider_events(
        provider="luma", discovered=discovered, apply=apply, metadata=metadata
    )


# --------------------------------------------------------------------------
# Reconciling the duplicates an earlier run already wrote
# --------------------------------------------------------------------------
#
# Fixing discovery stops new duplicates.  It does nothing for a database that
# already ran the unguarded version and holds a second ``Event``, with a real
# public id and a provisioned Q&A session, for an event the manifest already
# describes.  Those rows have to go, but deleting an Event is destructive and
# unreviewable after the fact, so this reports by default and removes only when
# an operator asks *and* the row is provably inert.
#
# "Provably inert" is narrow on purpose: no registration and either no Q&A
# session or the untouched draft session
# ``create_event_identity`` provisions -- no question, vote or co-host invite
# row.  Anything else is reported as retained with the dependent rows named,
# and a human decides.  There is no force flag: a duplicate carrying real
# dependent data is a merge, and a merge is not something this script may guess
# at.

# One reverse relation per thing that would be destroyed with the Event.
_DEPENDENT_RELATIONS = (("registrant_registrations", "registration"),)
_QNA_RELATIONS = (
    # A vote hangs off a question, so counting questions already covers it.
    ("questions", "qna_question"),
    ("cohost_invites", "qna_cohost_invite"),
)


def _dependent_row_totals(event: Any) -> dict[str, int]:
    """Count everything a delete would take with this Event. Never reads a value."""

    from event_qna.models import EventQnaSession

    totals = {label: getattr(event, relation).count() for relation, label in _DEPENDENT_RELATIONS}
    session = EventQnaSession.objects.filter(event=event).first()
    if session is None:
        return {label: total for label, total in totals.items() if total}
    if session.state != EventQnaSession.State.DRAFT:
        totals["qna_session_beyond_draft"] = 1
    for relation, label in _QNA_RELATIONS:
        totals[label] = getattr(session, relation).count()
    return {label: total for label, total in totals.items() if total}


def reconcile_duplicate_provider_identities(
    *, provider: str, discovered: tuple[Any, ...], remove: bool = False
) -> dict[str, Any]:
    """Name every provider-minted Event that duplicates one we already had.

    A duplicate is decided by the same rule discovery now uses to avoid making
    one: the provider Event's export entry shares its calendar date and its
    exact case/whitespace-normalized title with exactly one dated event we
    already have.  Anything less exact is not reported as a duplicate at all.
    """

    from community_base.events.models import Event
    from django.db import transaction

    from events.identity import (
        EventIdentityNotFound,
        canonical_detail_path,
        resolve_source_identity,
    )
    from scripts.prod.registrant_import import (
        EXISTING_EVENT_MATCHED,
        ExistingEventIndex,
        provider_source_identity,
    )

    # Provider-minted events carry no date in their source key, so they are
    # already absent from the index and cannot be matched against each other.
    index = ExistingEventIndex()
    duplicates: list[dict[str, Any]] = []
    for item in discovered:
        if not item.title:
            continue
        source = provider_source_identity(
            provider=provider, external_event_identifier=item.external_event_identifier
        )
        try:
            event = resolve_source_identity(
                repository=source.repository,
                revision=source.revision,
                source_key=source.source_key,
            )
        except EventIdentityNotFound:
            continue
        match = index.match(title=item.title, start_at=item.start_at)
        if match.outcome != EXISTING_EVENT_MATCHED or match.event is None:
            continue
        dependents = _dependent_row_totals(event)
        duplicates.append(
            {
                "external_event_identifier": item.external_event_identifier,
                "duplicate_event_id": str(event.content_id),
                "duplicate_row_id": event.pk,
                "duplicate_public_id": event.public_id,
                "duplicate_canonical_path": canonical_detail_path(event.content_id),
                "keep_event_id": str(match.event.content_id),
                "keep_public_id": match.event.public_id,
                "keep_canonical_path": canonical_detail_path(match.event.content_id),
                "matched_date": match.date,
                "dependent_rows": dependents,
                "removable": not dependents,
            }
        )
    removable = [entry for entry in duplicates if entry["removable"]]
    retained = [entry for entry in duplicates if not entry["removable"]]
    removed_total = 0
    if remove and removable:
        with transaction.atomic():
            # Re-check under the transaction: a dependent row written between
            # the report and the delete must still save the Event.
            for entry in removable:
                event = Event.objects.get(pk=entry["duplicate_row_id"])
                if _dependent_row_totals(event):
                    raise EventImportError("duplicate_identity_dependent_rows_appeared")
                event.delete()
                removed_total += 1
    return {
        "provider": provider,
        "mechanism": "duplicate_provider_identity_reconciliation",
        "provider_event_total": len(discovered),
        "duplicate_total": len(duplicates),
        "removable_total": len(removable),
        "retained_total": len(retained),
        "removed_total": removed_total,
        "removed": remove,
        "duplicates": duplicates,
        "note": (
            "A duplicate listed here shares its date and exact normalized title "
            "with keep_event_id. Only a duplicate with no dependent rows is "
            "removable; everything else needs a human decision, because "
            "deleting it would destroy the rows named in dependent_rows."
        ),
    }


def reconcile_duplicate_luma_identities(
    *, luma_source: Path, remove: bool = False
) -> dict[str, Any]:
    """Read a Luma export and reconcile the duplicates a previous run minted."""

    from scripts.prod.registrant_import import RegistrantImportError
    from scripts.prod.registration_sources.luma_events import discover_luma_events

    try:
        discovered = discover_luma_events(luma_source)
    except RegistrantImportError as error:
        raise EventImportError("luma_discovery_failed") from error
    return reconcile_duplicate_provider_identities(
        provider="luma", discovered=discovered, remove=remove
    )


def _parse_events_yaml_timestamp(value: Any) -> datetime:
    berlin = ZoneInfo("Europe/Berlin")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError as error:
            raise EventImportError("events_yaml_timestamp_invalid") from error
    else:
        raise EventImportError("events_yaml_timestamp_invalid")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=berlin)
    return parsed.astimezone(UTC)


_LUMA_HOSTS = frozenset({"luma.com", "lu.ma"})


def _luma_url_key(value: Any) -> str | None:
    """The stable provider identity a URL names: a Luma slug or ``evt-`` id, or an
    Eventbrite event id.

    ``https://luma.com/b90ue873``, ``http://lu.ma/B90UE873/`` and
    ``https://www.luma.com/b90ue873?utm=x`` are one event.  A Luma event id
    (``evt-...``) is case-sensitive and kept as written; a short slug is
    case-folded.  ``https://eventbrite.com/e/725934990097`` keys as
    ``eventbrite:725934990097`` -- the reviewed manifest rows carry their
    Eventbrite link in ``materials``, which is how a legacy entry finds a row
    whose source key no longer matches its title.  Anything else has no key.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("evt-"):
        return text
    split = urlsplit(text)
    host = split.netloc.lower().removeprefix("www.")
    if split.scheme not in {"http", "https"}:
        return None
    parts = [part for part in split.path.split("/") if part]
    if not parts:
        return None
    slug = parts[-1]
    if host.startswith("eventbrite."):
        # ``/e/725934990097`` and ``/e/some-title-tickets-725934990097`` are the
        # same listing: its numeric event id is the identity.
        digits = re.search(r"(\d{6,})$", slug)
        return f"eventbrite:{digits.group(1)}" if digits else None
    if host not in _LUMA_HOSTS:
        return None
    if slug.startswith("evt-"):
        return slug
    # An event page is a single path segment (``luma.com/<slug>``); anything
    # deeper -- ``luma.com/user/<x>``, ``/calendar/...`` -- names no event.
    return slug.casefold() if len(parts) == 1 else None


def run(
    *,
    identity_manifest: Path | None = None,
    event_content_source: Path | None = None,
    new_event_content_source: Path | None = None,
    eventbrite_descriptions_source: Path | None = None,
    luma_source: Path,
    events_yaml: Path | None = None,
) -> dict[str, Any]:
    """Import current Event rows and content in one transaction.

    ``events_yaml`` is the legacy site's ``_data/events.yaml`` (the
    ``dtc-main-site`` checkout's copy), read only as the type and speakers a
    discovered provider event is created with. It creates no event of its own
    and writes no materials.
    """

    if not luma_source.is_dir():
        raise EventImportError("registration_source_unavailable")

    from django.db import transaction

    from events.publication import incomplete_published_events

    with transaction.atomic():
        report = _run_legs(
            identity_manifest=identity_manifest,
            event_content_source=event_content_source,
            new_event_content_source=new_event_content_source,
            eventbrite_descriptions_source=eventbrite_descriptions_source,
            luma_source=luma_source,
            events_yaml=events_yaml,
        )
        # A published event must carry a type and a speaker (events.publication).
        # Refusing here rolls the whole run back rather than landing a database
        # whose public events list mixes complete and bare rows.
        incomplete = incomplete_published_events()
        if incomplete:
            raise IncompletePublishedEvents(incomplete)
        return report


def _run_legs(
    *,
    identity_manifest: Path | None,
    event_content_source: Path | None,
    new_event_content_source: Path | None,
    eventbrite_descriptions_source: Path | None,
    luma_source: Path,
    events_yaml: Path | None = None,
) -> dict[str, Any]:
    """The event legs in their fixed order. Only :func:`run` may call this.

    Split out purely so the transaction boundary is one unmissable line in
    :func:`run` rather than an indent level wrapped around a hundred of them.
    """

    identities = import_identities(
        manifest=identity_manifest, content=event_content_source, apply=True
    )
    content = import_content(source=event_content_source, apply=True)
    # Description *authoring* precedence, distinct from content bootstrap
    # above: runs only after `content` so the EventContent row it overwrites
    # already exists. See import_eventbrite_descriptions's own docstring.
    eventbrite_descriptions = import_eventbrite_descriptions(
        source=eventbrite_descriptions_source, apply=True
    )
    # Distinct top-level key, deliberately never merged into `identities` (the
    # reviewed-manifest replay) -- an operator reading the report must not
    # mistake an automatic creation for the reviewed manifest changing.
    new_event_identities = {
        "luma": discover_new_luma_event_identities(
            luma_source=luma_source, apply=True, events_yaml=events_yaml
        ),
    }
    # Straight after the identities it depends on, and under its own key: the
    # 421 reviewed records and the staged descriptions for discovered events are
    # different artifacts with different provenance, and merging their counts
    # would hide which of the two moved.
    new_event_content = import_new_content(source=new_event_content_source, apply=True)
    return {
        "identities": identities,
        "new_event_identities": new_event_identities,
        "event_content": content,
        "new_event_content": new_event_content,
        "eventbrite_descriptions": eventbrite_descriptions,
    }


def _parser() -> argparse.ArgumentParser:
    main_root = _main_checkout_root()
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--identity-manifest", type=Path, default=IDENTITY_MANIFEST_PATH)
    parser.add_argument("--event-content", type=Path, default=EVENT_CONTENT_PATH)
    parser.add_argument(
        "--new-event-content",
        type=Path,
        default=NEW_EVENT_CONTENT_PATH,
        help=(
            "Staged descriptions for events discovered in a provider export, built by "
            "scripts/build_luma_event_descriptions.py. Absent until an operator has "
            "cleared the link and type reviews that builder reports."
        ),
    )
    parser.add_argument(
        "--eventbrite-descriptions",
        type=Path,
        default=EVENTBRITE_DESCRIPTIONS_PATH,
        help=(
            "Cleaned Eventbrite descriptions staged by "
            "scripts/build_eventbrite_descriptions.py --write, outside this "
            "repository (~/prod/dtc-data/eventbrite-content/staging/ by "
            "default on both ends). When present, wins outright over the "
            "Jekyll-sourced description for every event whose Eventbrite id "
            "resolves. Absent until an operator has built it."
        ),
    )
    parser.add_argument("--luma-source", type=Path, default=main_root / LUMA_RELATIVE_SOURCE)
    parser.add_argument(
        "--discover-new-events-only",
        action="store_true",
        help=(
            "Import identities, then discover and create identities for new Luma "
            "events only. Skips the content import; registrations are always "
            "handled separately by import_event_registrants.py."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "With --discover-new-events-only, report exactly what a fresh export "
            "would create and change nothing. The answer to 'which events are new "
            "since last time' -- created_events are the ones this database has "
            "never seen. Point it at the database you would apply to: the decision "
            "is made against the events that database already holds."
        ),
    )
    parser.add_argument(
        "--report-duplicate-identities",
        action="store_true",
        help=(
            "Report, and change nothing, every Luma-minted Event that duplicates "
            "an event this database already had -- same calendar date, same exact "
            "normalized title. Names each duplicate's public id and canonical "
            "path alongside the event it duplicates, and the rows a delete would "
            "destroy."
        ),
    )
    parser.add_argument(
        "--remove-duplicate-identities",
        action="store_true",
        help=(
            "Report the same duplicates and delete only those carrying no "
            "registration, Q&A question or co-host invite. "
            "Any duplicate with dependent rows is reported and kept."
        ),
    )
    parser.add_argument(
        "--event-metadata-yaml",
        type=Path,
        default=None,
        help=(
            "With --discover-new-events-only: a legacy _data/events.yaml read only "
            "for the type and speakers a discovered provider event is created with "
            "(same Luma event link, or same Berlin date and title). It creates no "
            "event of its own and writes no materials; without it nothing is created."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _parser()
        args = parser.parse_args(argv)
        if args.dry_run and not args.discover_new_events_only:
            # The complete content import is transactional; discovery is the
            # only mode with a useful read-only plan.
            parser.error("--dry-run applies to --discover-new-events-only")
        configure_target(parser, args)
        report: dict[str, Any]
        if args.report_duplicate_identities or args.remove_duplicate_identities:
            if not args.luma_source.resolve().is_dir():
                raise EventImportError("registration_source_unavailable")
            report = {
                "duplicate_event_identities": {
                    "luma": reconcile_duplicate_luma_identities(
                        luma_source=args.luma_source.resolve(),
                        remove=args.remove_duplicate_identities,
                    ),
                },
            }
        elif args.discover_new_events_only:
            if not args.luma_source.resolve().is_dir():
                raise EventImportError("registration_source_unavailable")
            # --dry-run makes this the "what is new since last time" question an
            # operator asks before committing to a fresh export. Every leg takes
            # the same apply flag, so the reported decision is the one the
            # applying run would make, not a separate approximation of it.
            apply = not args.dry_run
            report = {
                "applied": apply,
                "identities": import_identities(
                    manifest=args.identity_manifest.resolve(),
                    content=args.event_content.resolve(),
                    apply=apply,
                ),
                "new_event_identities": {
                    # The type and speakers a new event must be created with
                    # come from --event-metadata-yaml; without it nothing is created.
                    "luma": discover_new_luma_event_identities(
                        luma_source=args.luma_source.resolve(),
                        apply=apply,
                        events_yaml=(
                            args.event_metadata_yaml.resolve() if args.event_metadata_yaml else None
                        ),
                    ),
                },
                # The identities this mode creates are exactly what the staged
                # descriptions attach to, so landing them here is what makes the
                # mode a complete pass for a fresh export rather than half of one.
                "new_event_content": import_new_content(
                    source=args.new_event_content.resolve(), apply=apply
                ),
            }
        else:
            report = run(
                identity_manifest=args.identity_manifest.resolve(),
                event_content_source=args.event_content.resolve(),
                new_event_content_source=args.new_event_content.resolve(),
                eventbrite_descriptions_source=args.eventbrite_descriptions.resolve(),
                luma_source=args.luma_source.resolve(),
            )
    except IncompletePublishedEvents as error:
        print(
            json.dumps({"error": str(error), "incomplete_published_events": error.events}, indent=2)
        )
        return 1
    except EventImportError as error:
        # The error carries a condition code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
