#!/usr/bin/env python3
"""Import current Event identities and content into the database.

Provider registrations are owned by ``scripts/prod/import_event_registrants.py``.
Legacy source translation stays in import scripts; runtime event reads use
database rows directly.
"""

from __future__ import annotations

import argparse
import hashlib
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


from scripts.prod.target import main_checkout_root  # noqa: E402


def _main_checkout_root() -> Path:
    """The main checkout, where the protected registration exports live."""

    return main_checkout_root(PROJECT_ROOT, error_cls=EventImportError)


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def import_identities(*, manifest: Path | None = None, apply: bool = True) -> dict[str, Any]:
    """Import the reviewed identity manifest atomically."""

    from events.identity import EventIdentityError
    from scripts.prod.identity_manifest import import_identity_manifest

    try:
        report = import_identity_manifest(
            path=manifest or IDENTITY_MANIFEST_PATH, dry_run=not apply
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


def discover_new_provider_events(
    *, provider: str, discovered: tuple[Any, ...], apply: bool = True
) -> dict[str, Any]:
    """Create identities for provider events this database has never tracked.

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
        if not apply:
            created.append({**entry, "public_id": None, "canonical_path": None, "dry_run": True})
            continue
        try:
            event = create_provider_event_identity(
                provider=provider,
                external_event_identifier=item.external_event_identifier,
                title=item.title,
                start_at=item.start_at,
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
        "created_total": len(created),
        "created_events": created,
        "applied": apply,
    }


def discover_new_luma_event_identities(*, luma_source: Path, apply: bool = True) -> dict[str, Any]:
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
    return discover_new_provider_events(provider="luma", discovered=discovered, apply=apply)


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


LEGACY_EVENTS_YAML_REPOSITORY = "DataTalksClub/datatalksclub.github.io"
LEGACY_EVENTS_YAML_PATH = "_data/events.yaml"


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


def _events_yaml_materials(entry: dict[str, Any]) -> list[dict[str, str]]:
    materials: list[dict[str, str]] = []
    candidates = (
        (entry.get("link"), None),
        (entry.get("youtube") or entry.get("Youtube"), "Watch on YouTube"),
        (entry.get("anchor"), "Listen on Anchor"),
    )
    for raw_url, fixed_label in candidates:
        if not isinstance(raw_url, str):
            continue
        url = raw_url.strip()
        if not url:
            continue
        split = urlsplit(url)
        if split.scheme not in {"http", "https"} or not split.netloc:
            continue
        if fixed_label is not None:
            label = fixed_label
        elif "luma.com" in split.netloc or "lu.ma" in split.netloc:
            label = "Register on Luma"
        else:
            label = "Event link"
        materials.append({"label": label, "url": url})
    return materials


_LUMA_HOSTS = frozenset({"luma.com", "lu.ma"})

#: How a ``_data/events.yaml`` entry was recognised as an event we already hold.
YAML_MATCH_LEGACY_SOURCE_KEY = "legacy_source_key"
YAML_MATCH_LUMA_URL = "luma_url"
YAML_MATCH_EVENTBRITE_URL = "eventbrite_url"
YAML_MATCH_EARLIER_ENTRY = "earlier_entry_in_this_file"


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


def _luma_url_index(luma_source: Path | None) -> dict[str, str]:
    """``{luma url key: luma event id}`` from a Luma export's checkpoints.

    The export's JSON checkpoint names each event's public URL beside its event
    id, which is what lets a legacy ``link: https://luma.com/<slug>`` find the
    event discovery minted under ``evt-...``.  No export, no URL tier: matching
    falls through to start and title.
    """

    if luma_source is None or not luma_source.is_dir():
        return {}
    from scripts.prod.registrant_import import RegistrantImportError
    from scripts.prod.registration_sources.luma_events import discover_luma_events

    try:
        discovered = discover_luma_events(luma_source)
    except RegistrantImportError as error:
        raise EventImportError("luma_discovery_failed") from error
    urls: dict[str, str] = {}
    for item in discovered:
        key = _luma_url_key(item.event_url)
        if key is not None:
            urls[key] = item.external_event_identifier
    return urls


@dataclass(slots=True)
class _PlannedYamlEvent:
    """A dry-run stand-in for an event this file would create, so a later entry
    naming the same occurrence is reported as matching it, exactly as apply would."""

    pk: int
    title: str
    start_datetime: datetime
    public_id: int | None = None
    content_id: Any = None


class _EventsYamlIndex:
    """Everything an events.yaml entry can be recognised by, built once per run.

    Tiers, in order, each resolving only an unambiguous single event:

    1. the entry's own legacy source key (a reviewed-manifest or earlier
       events.yaml row);
    2. its provider identity -- the entry's ``link`` as a Luma slug/id or an
       Eventbrite event id (:func:`_luma_url_key`), matched against the Luma
       event discovery minted for that URL, or against any event whose
       materials carry the same link -- held by exactly one event on the same
       Europe/Berlin calendar date, because a reused listing is not the same
       occurrence;
    3. the same start instant and normalized title
       (:class:`scripts.prod.registrant_import.EventOccurrenceIndex`);
    4. the same Europe/Berlin calendar date and normalized title.

    Tiers 3 and 4 read the ``Event`` row's own ``start_datetime``, so an event
    whose source key carries no date -- every Luma-discovered ``evt-...`` row and
    every podcast-style manifest entry -- is matched like any other.  That blind
    spot is what let the first version of this leg create 79 duplicates.
    """

    def __init__(self, *, luma_urls: dict[str, str]) -> None:
        from community_base.events.models import Event

        from scripts.prod.registrant_import import (
            PROVIDER_SOURCE_REPOSITORY,
            EventOccurrenceIndex,
        )

        events = list(Event.objects.select_related("source_identity"))
        self.occurrences = EventOccurrenceIndex(events)
        self.by_legacy_key: dict[str, Any] = {}
        self.by_luma: dict[str, dict[int, Any]] = {}
        self.provider_event_pks: set[int] = set()
        provider_repositories = set(PROVIDER_SOURCE_REPOSITORY.values())
        luma_repository = PROVIDER_SOURCE_REPOSITORY["luma"]
        by_luma_event_id: dict[str, Any] = {}
        for event in events:
            source = getattr(event, "source_identity", None)
            if source is not None:
                if source.repository == LEGACY_EVENTS_YAML_REPOSITORY:
                    self.by_legacy_key.setdefault(source.source_key, event)
                if source.repository in provider_repositories:
                    self.provider_event_pks.add(event.pk)
                if source.repository == luma_repository:
                    by_luma_event_id[source.source_key] = event
                    self._add_luma(source.source_key, event)
            for material in event.materials or []:
                if isinstance(material, dict):
                    self._add_luma(_luma_url_key(material.get("url")), event)
        for url_key, luma_event_id in luma_urls.items():
            event = by_luma_event_id.get(luma_event_id)
            if event is not None:
                self._add_luma(url_key, event)

    def _add_luma(self, key: str | None, event: Any) -> None:
        if key is not None:
            self.by_luma.setdefault(key, {})[event.pk] = event

    def add(self, event: Any, *, legacy_key: str, luma_key: str | None) -> None:
        self.by_legacy_key.setdefault(legacy_key, event)
        self._add_luma(luma_key, event)
        self.occurrences.add(event)

    def match(
        self,
        *,
        title: str,
        starts_at: datetime,
        legacy_key: str,
        luma_key: str | None,
        day_level: bool = True,
    ) -> tuple[str, Any, dict[str, Any]]:
        """``(outcome, event, detail)``. ``event`` is ``None`` unless matched.

        ``day_level=False`` (the file names this title twice on this day) skips
        the two tiers that only know the day -- the legacy key is
        ``<date>-<slug>`` -- so only a provider link or the exact start decides,
        and a date-only match is reported as ambiguous instead of folding two
        sessions into one row.
        """

        from scripts.prod.registrant_import import (
            EVENT_CALENDAR_TIMEZONE,
            OCCURRENCE_MATCH_AMBIGUOUS,
            OCCURRENCE_MATCH_DATE_AND_TITLE,
        )

        detail: dict[str, Any] = {}
        legacy = self.by_legacy_key.get(legacy_key) if day_level else None
        if legacy is not None:
            return YAML_MATCH_LEGACY_SOURCE_KEY, legacy, detail
        if luma_key is not None:
            candidates = list(self.by_luma.get(luma_key, {}).values())
            day = starts_at.astimezone(EVENT_CALENDAR_TIMEZONE).date()
            same_day = [
                candidate
                for candidate in candidates
                if candidate.start_datetime.astimezone(EVENT_CALENDAR_TIMEZONE).date() == day
            ]
            if len(same_day) == 1:
                outcome = (
                    YAML_MATCH_EVENTBRITE_URL
                    if luma_key.startswith("eventbrite:")
                    else YAML_MATCH_LUMA_URL
                )
                return outcome, same_day[0], detail
            # No same-day holder, or a reused listing naming several events on
            # that day: the URL proves nothing, so start and title decide.
            if candidates and not same_day:
                detail["provider_url_on_another_date_public_id"] = candidates[0].public_id
            elif len(same_day) > 1:
                detail["provider_url_same_day_candidate_total"] = len(same_day)
        occurrence = self.occurrences.match(title=title, start=starts_at)
        if occurrence.event is not None and (
            day_level or occurrence.outcome != OCCURRENCE_MATCH_DATE_AND_TITLE
        ):
            return occurrence.outcome, occurrence.event, detail
        if occurrence.event is not None:
            return "ambiguous", None, detail
        if occurrence.outcome == OCCURRENCE_MATCH_AMBIGUOUS:
            detail["candidate_event_total"] = occurrence.candidate_total
            return "ambiguous", None, detail
        return "none", None, detail


def _attach_yaml_speakers(event: Any, speaker_keys: list[str], people_names: dict) -> None:
    from community_base.events.models import EventHost, Host

    for order, speaker_key in enumerate(speaker_keys):
        host, _ = Host.objects.get_or_create(
            slug=speaker_key,
            defaults={
                "name": people_names.get(speaker_key) or speaker_key,
                "kind": "speaker",
                "external_ref": speaker_key,
            },
        )
        EventHost.objects.get_or_create(
            event=event,
            host=host,
            role="speaker",
            defaults={"position": order},
        )


def _yaml_enrichment(
    event: Any,
    *,
    event_type: str | None,
    season: int | None,
    episode: int | None,
    speaker_keys: list[str],
    materials: list[dict[str, str]],
) -> list[str]:
    """Which empty fields of a provider-discovered event this entry would fill.

    Fill-only: a field that already holds anything -- a reviewed type, a speaker
    list, materials -- is never touched, so a description review or a later
    ``dtc events update`` always wins over the legacy file.
    """

    from events.identity import decode_event_tags

    current_type, current_season, current_episode = decode_event_tags(event.tags)
    filled: list[str] = []
    if event_type and current_type is None:
        filled.append("type")
    if season is not None and current_season is None:
        filled.append("season")
    if episode is not None and current_episode is None:
        filled.append("episode")
    if speaker_keys and not event.event_host_links.exists():
        filled.append("speakers")
    if materials and not event.materials:
        filled.append("materials")
    return filled


def _apply_yaml_enrichment(
    event: Any,
    filled: list[str],
    *,
    event_type: str | None,
    season: int | None,
    episode: int | None,
    speaker_keys: list[str],
    materials: list[dict[str, str]],
    people_names: dict,
) -> None:
    from events.identity import (
        decode_display_tags,
        decode_event_tags,
        encode_event_tags,
        package_kind_for_type,
    )

    current_type, current_season, current_episode = decode_event_tags(event.tags)
    update_fields: list[str] = []
    if {"type", "season", "episode"} & set(filled):
        new_type = event_type if "type" in filled else current_type
        event.tags = encode_event_tags(
            event_type=new_type,
            season=season if "season" in filled else current_season,
            episode=episode if "episode" in filled else current_episode,
            display_tags=decode_display_tags(event.tags),
        )
        update_fields.append("tags")
        if "type" in filled and event.kind == "standard":
            event.kind = package_kind_for_type(new_type)
            update_fields.append("kind")
    if "materials" in filled:
        event.materials = materials
        update_fields.append("materials")
    if update_fields:
        event.save(update_fields=[*update_fields, "updated_at"])
    if "speakers" in filled:
        _attach_yaml_speakers(event, speaker_keys, people_names)


def import_events_yaml(
    *,
    path: Path,
    revision: str = "HEAD",
    apply: bool = True,
    luma_source: Path | None = None,
) -> dict[str, Any]:
    """Import the events a legacy ``_data/events.yaml`` names that we do not hold.

    Each entry is first matched against every event already in the database
    (see :class:`_EventsYamlIndex` for the tiers); only an entry that matches
    nothing creates an event, so one occurrence never gets a second row.  An
    ambiguous match creates nothing and is reported.  ``luma_source`` (a Luma
    export directory) enables the Luma-URL tier.

    A matched event is never rewritten, with one exception: a Luma-discovered
    event -- minted from a provider export with only a title and a start -- has
    its *empty* type, season, episode, speakers and materials filled from the
    entry.  That is how production's upcoming podcasts carry their type and
    speakers, and a rebuild must reproduce it.  Reviewed manifest rows and rows
    this leg created earlier are matched and left exactly as they are.

    The dry run (``apply=False``) makes every decision apply would make -- an
    entry repeated later in the file matches the first one's planned row -- and
    reports matches, enrichments and creations separately.
    """

    import yaml
    from django.db import transaction
    from django.utils import timezone

    from content.models import SyncedDocument
    from events.identity import EventIdentityError, canonical_detail_path, create_event_identity
    from events.slugs import event_title_slug
    from scripts.prod.registrant_import import normalize_match_title

    try:
        raw_bytes = path.read_bytes()
        entries = yaml.safe_load(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise EventImportError("events_yaml_unreadable") from error

    if not isinstance(entries, list):
        raise EventImportError("events_yaml_invalid")

    checksum = hashlib.sha256(raw_bytes).hexdigest()
    berlin = ZoneInfo("Europe/Berlin")
    now = timezone.now()
    luma_urls = _luma_url_index(luma_source)

    people_names = dict(
        SyncedDocument.objects.filter(
            source__slug="dtc-main-site",
            content_kind="people",
        ).values_list("slug", "title")
    )

    def _path(event: Any) -> str | None:
        return canonical_detail_path(event.content_id) if event.content_id else None

    def _apply() -> dict[str, Any]:
        index = _EventsYamlIndex(luma_urls=luma_urls)
        created: list[dict[str, Any]] = []
        matched: list[dict[str, Any]] = []
        matched_by: dict[str, int] = {}
        enriched: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []
        url_date_conflicts: list[dict[str, Any]] = []
        provider_duplicates: list[dict[str, Any]] = []
        skipped_invalid = 0
        planned_pk = 0
        created_pks: set[int] = set()
        # Same-day, same-title entries in the file itself: two sessions of one
        # series on one day are two events, so the date tier may not fold one
        # into the other (or both into one database row). Exact start still can.
        file_day_titles: dict[tuple[str, str], int] = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("time") is None:
                continue
            entry_title = str(entry.get("title") or "").strip()
            if not entry_title:
                continue
            try:
                entry_start = _parse_events_yaml_timestamp(entry["time"])
            except EventImportError:
                continue
            day_title = (
                entry_start.astimezone(berlin).date().isoformat(),
                normalize_match_title(entry_title),
            )
            file_day_titles[day_title] = file_day_titles.get(day_title, 0) + 1

        for entry in entries:
            if not isinstance(entry, dict):
                skipped_invalid += 1
                continue
            title = str(entry.get("title") or "").strip()
            if not title or entry.get("time") is None:
                skipped_invalid += 1
                continue

            starts_at = _parse_events_yaml_timestamp(entry["time"])
            ends_at = _parse_events_yaml_timestamp(entry["end"]) if entry.get("end") else None
            local_date = starts_at.astimezone(berlin).date().isoformat()
            source_key = f"{local_date}-{event_title_slug(title)}"
            luma_key = _luma_url_key(entry.get("link"))
            event_type = str(entry.get("type") or "").strip() or None
            season = int(entry["season"]) if entry.get("season") is not None else None
            episode = int(entry["episode"]) if entry.get("episode") is not None else None
            materials = _events_yaml_materials(entry)
            speaker_keys = [
                str(s).strip() for s in (entry.get("speakers") or []) if str(s).strip()
            ]
            described = {"title": title, "starts_at": starts_at.isoformat()}

            same_day_entries = file_day_titles.get((local_date, normalize_match_title(title)), 0)
            outcome, event, detail = index.match(
                title=title,
                starts_at=starts_at,
                legacy_key=source_key,
                luma_key=luma_key,
                day_level=same_day_entries < 2,
            )
            if "provider_url_on_another_date_public_id" in detail:
                url_date_conflicts.append({**described, "link": entry.get("link"), **detail})
            if outcome == "ambiguous" and same_day_entries > 1:
                detail = {**detail, "same_day_same_title_entries": same_day_entries}
            if outcome == "ambiguous":
                ambiguous.append({**described, "date": local_date, **detail})
                continue
            if event is not None and event.pk in created_pks:
                # The same occurrence named twice in one file: the first entry
                # created (or, dry, would create) it; this one is that row.
                outcome = YAML_MATCH_EARLIER_ENTRY
            if event is not None:
                matched_by[outcome] = matched_by.get(outcome, 0) + 1
                matched.append(
                    {
                        **described,
                        "matched_by": outcome,
                        "public_id": event.public_id,
                        "canonical_path": _path(event),
                    }
                )
                twin = None
                if event.pk in index.provider_event_pks and outcome == YAML_MATCH_LUMA_URL:
                    # The Luma URL found the provider row; if start and title also
                    # prove a *different* event (the reviewed row an earlier
                    # discovery duplicated), that row is the occurrence and the
                    # provider row is a duplicate to reconcile, not to dress up.
                    occurrence = index.occurrences.match(title=title, start=starts_at)
                    if occurrence.event is not None and occurrence.event.pk != event.pk:
                        twin = occurrence.event
                        provider_duplicates.append(
                            {
                                **described,
                                "provider_public_id": event.public_id,
                                "also_matches_public_id": twin.public_id,
                            }
                        )
                if event.pk in index.provider_event_pks and twin is None:
                    metadata: dict[str, Any] = {
                        "event_type": event_type,
                        "season": season,
                        "episode": episode,
                        "speaker_keys": speaker_keys,
                        "materials": materials,
                    }
                    filled = _yaml_enrichment(event, **metadata)
                    if filled:
                        if apply:
                            _apply_yaml_enrichment(
                                event, filled, people_names=people_names, **metadata
                            )
                        enriched.append(
                            {
                                **described,
                                "public_id": event.public_id,
                                "canonical_path": _path(event),
                                "filled": filled,
                            }
                        )
                continue

            status = "upcoming" if starts_at >= now else "completed"
            if not apply:
                planned_pk -= 1
                created_pks.add(planned_pk)
                index.add(
                    _PlannedYamlEvent(pk=planned_pk, title=title, start_datetime=starts_at),
                    legacy_key=source_key,
                    luma_key=luma_key,
                )
                created.append(
                    {
                        **described,
                        "status": status,
                        "public_id": None,
                        "canonical_path": None,
                        "dry_run": True,
                    }
                )
                continue

            try:
                event = create_event_identity(
                    title=title,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    source_repository=LEGACY_EVENTS_YAML_REPOSITORY,
                    source_revision=revision,
                    source_key=source_key,
                    source_path=LEGACY_EVENTS_YAML_PATH,
                    source_checksum=checksum,
                    event_type=event_type,
                    season=season,
                    episode=episode,
                    status=status,
                )
            except EventIdentityError as error:
                raise EventImportError("events_yaml_identity_invalid") from error

            event.timezone = "Europe/Berlin"
            event.materials = materials
            event.save(update_fields=["timezone", "materials"])
            _attach_yaml_speakers(event, speaker_keys, people_names)
            index.add(event, legacy_key=source_key, luma_key=luma_key)
            created_pks.add(event.pk)

            created.append(
                {
                    **described,
                    "status": status,
                    "public_id": event.public_id,
                    "canonical_path": canonical_detail_path(event.content_id),
                }
            )

        return {
            "source": str(path),
            "revision": revision,
            "total": len(entries),
            "luma_url_index": {"present": bool(luma_urls), "urls": len(luma_urls)},
            "existing_event_total": len(matched),
            "matched_by_totals": dict(sorted(matched_by.items())),
            "matched_events": matched,
            "enriched_total": len(enriched),
            "enriched_events": enriched,
            "ambiguous_total": len(ambiguous),
            "ambiguous_events": ambiguous,
            "provider_url_date_conflict_total": len(url_date_conflicts),
            "provider_url_date_conflicts": url_date_conflicts,
            # A Luma-discovered row that duplicates another event this entry also
            # matches: neither enriched nor touched here. Reconcile it with
            # --report-duplicate-identities / --remove-duplicate-identities.
            "provider_duplicate_total": len(provider_duplicates),
            "provider_duplicates": provider_duplicates,
            "skipped_invalid_total": skipped_invalid,
            "created_total": len(created),
            "created_events": created,
            "applied": apply,
        }

    if apply:
        with transaction.atomic():
            return _apply()
    return _apply()


def run(
    *,
    identity_manifest: Path | None = None,
    event_content_source: Path | None = None,
    new_event_content_source: Path | None = None,
    eventbrite_descriptions_source: Path | None = None,
    luma_source: Path,
    events_yaml: Path | None = None,
    events_yaml_revision: str = "HEAD",
) -> dict[str, Any]:
    """Import current Event rows and content in one transaction.

    ``events_yaml`` is the legacy site's ``_data/events.yaml`` (the
    ``dtc-main-site`` checkout's copy). When given, its leg runs last -- after
    discovery has minted every provider identity, so it matches those rather
    than racing them -- and reports under ``events_yaml``.
    """

    if not luma_source.is_dir():
        raise EventImportError("registration_source_unavailable")

    from django.db import transaction

    with transaction.atomic():
        return _run_legs(
            identity_manifest=identity_manifest,
            event_content_source=event_content_source,
            new_event_content_source=new_event_content_source,
            eventbrite_descriptions_source=eventbrite_descriptions_source,
            luma_source=luma_source,
            events_yaml=events_yaml,
            events_yaml_revision=events_yaml_revision,
        )


def _run_legs(
    *,
    identity_manifest: Path | None,
    event_content_source: Path | None,
    new_event_content_source: Path | None,
    eventbrite_descriptions_source: Path | None,
    luma_source: Path,
    events_yaml: Path | None = None,
    events_yaml_revision: str = "HEAD",
) -> dict[str, Any]:
    """The event legs in their fixed order. Only :func:`run` may call this.

    Split out purely so the transaction boundary is one unmissable line in
    :func:`run` rather than an indent level wrapped around a hundred of them.
    """

    identities = import_identities(manifest=identity_manifest, apply=True)
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
        "luma": discover_new_luma_event_identities(luma_source=luma_source, apply=True),
    }
    # Straight after the identities it depends on, and under its own key: the
    # 421 reviewed records and the staged descriptions for discovered events are
    # different artifacts with different provenance, and merging their counts
    # would hide which of the two moved.
    new_event_content = import_new_content(source=new_event_content_source, apply=True)
    # Last: the legacy file's events after the reviewed manifest, so every event
    # it names that a provider export also named is matched rather than created
    # twice, and its type/speakers/materials fill what discovery could not know.
    # Reviewed staged content above has already landed, and fill-only never
    # overrides it.
    events_yaml_report: dict[str, Any]
    if events_yaml is None:
        events_yaml_report = {"present": False, "applied": True}
    elif not events_yaml.is_file():
        raise EventImportError("events_yaml_unreadable")
    else:
        events_yaml_report = {
            "present": True,
            **import_events_yaml(
                path=events_yaml,
                revision=events_yaml_revision,
                apply=True,
                luma_source=luma_source,
            ),
        }
    return {
        "identities": identities,
        "new_event_identities": new_event_identities,
        "event_content": content,
        "new_event_content": new_event_content,
        "eventbrite_descriptions": eventbrite_descriptions,
        "events_yaml": events_yaml_report,
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
        "--events-yaml",
        type=Path,
        default=None,
        help=(
            "Import the events a legacy _data/events.yaml names that this database "
            "does not hold. Each entry matches an existing event first -- by legacy "
            "source key, Luma or Eventbrite link (Luma via --luma-source when present), start and "
            "normalized title, then Berlin date and title -- and only an unmatched "
            "entry creates one. A matched Luma-discovered event has its empty type, "
            "speakers and materials filled; nothing else is modified. With --dry-run, "
            "reports matches, enrichments and creations without writing."
        ),
    )
    parser.add_argument(
        "--events-yaml-revision",
        default="HEAD",
        help="Git revision to record on EventSource rows created from --events-yaml.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _parser()
        args = parser.parse_args(argv)
        if args.dry_run and not (args.discover_new_events_only or args.events_yaml is not None):
            # The complete content import is transactional; discovery is the
            # only mode with a useful read-only plan.
            parser.error("--dry-run applies to --discover-new-events-only or --events-yaml")
        configure_target(parser, args)
        report: dict[str, Any]
        if args.events_yaml is not None:
            luma_source = args.luma_source.resolve()
            report = {
                "events_yaml": import_events_yaml(
                    path=args.events_yaml.resolve(),
                    revision=args.events_yaml_revision,
                    apply=not args.dry_run,
                    # Optional here: without an export the Luma-URL tier is
                    # simply absent and matching falls through to start/title.
                    luma_source=luma_source if luma_source.is_dir() else None,
                )
            }
        elif args.report_duplicate_identities or args.remove_duplicate_identities:
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
                    manifest=args.identity_manifest.resolve(), apply=apply
                ),
                "new_event_identities": {
                    "luma": discover_new_luma_event_identities(
                        luma_source=args.luma_source.resolve(), apply=apply
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
    except EventImportError as error:
        # The error carries a condition code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
