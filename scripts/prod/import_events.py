#!/usr/bin/env python3
"""Import event identities and the historical registration aggregates.

One-time import.  Both sources are frozen: the reviewed identity manifest is
checked into this repository, and the Luma and Eventbrite exports are archives
of platforms we no longer publish through.  See ``scripts/prod/__init__.py``
for what the two sync models mean.

This gathers what used to be in two places -- ``manage.py
import_event_identities`` for identity, and a block buried inside
``scripts/prepare_local_data.py`` for the aggregates.  The orchestrator now
calls this module, so there is one implementation rather than two that drift.
The management command itself is retired: it had no caller left once
``scripts/prepare_local_data.py`` was repointed at :func:`import_identities`
directly, the same function this script's own identity import calls.

What lands in the database
--------------------------

**Identity** (``events/event_identity_manifest.json``, 421 events, 1684
aliases): the uuid, public id, slug, title, source pointer and alias paths that
make an event addressable.  Replaying reports ``replayed`` and creates nothing.

**Registration aggregates** (Luma and Eventbrite): *counts only*.  No attendee
row is read into the database by any path here -- the adapters reduce each
export to a per-event total, reconcile it against the recorded safe facts in
``_docs/migration-data/event-registration-sources.json``, and store a
``HistoricalRegistrationAggregateRevision``.  There is no separate mapping
model or review-state row: the aggregate revision either resolves directly to
a canonical ``Event`` (its ``event`` field, set once) or it does not.  A count
only becomes public once its aggregate is resolved *and* separately activated
(``dry-run``/``validate``/``activate``, or ``activate_explicit_current_source``
below) -- resolution and public-display activation are still two different
gates, exactly as they were before.

**Coverage is the interesting number.**  The adapters work; resolving each
provider event to a canonical Event is the backlog.  Every run reports
``activation_coverage`` so an operator sees "3 of 383 provider events
resolved, 380 still unresolved" rather than a silent success.  An unresolved
event renders no registration count at all.

**New event identities** (``new_event_identities`` in the report): a genuinely
new event -- one a fresh Luma export names that neither the reviewed manifest
nor any prior provider-registration run has ever seen -- gets a real
``Event`` row here, via ``events.identity.create_event_identity``.  This is
title and a canonical path only; it never resolves or activates a
registration count, and it never touches ``events/event_identity_manifest.json``.
"Genuinely new" is decided against every event we already have: an export event
whose calendar date and exact case/whitespace-normalized title belong to one
event already in the database is that event, and creates nothing
(``existing_event_total``).  See ``discover_new_luma_event_identities`` and
``discover_new_provider_events``.  Run with ``--discover-new-events-only`` to do
just this against a fresh export that has not yet been reconciled into
``event-registration-sources.json``.
**Resolution** happens in two ways, both applied every run, neither a
persistent review queue:

1. *Explicit*: staging an aggregate (``stage_derived_source``, called below)
   resolves it immediately when ``--current-registration-input`` names its
   exact provider identity, and re-resolves an already-staged, still-null
   aggregate on replay when the file has since been extended.  See
   ``_docs/migration-data/local-current-registration-input.json``.
2. *Automatic* (``aggregate_auto_resolution`` in the report): a narrower,
   additional pass over whatever is still unresolved after staging.  For each
   Luma provider event still unresolved, it resolves the aggregate -- via
   ``events.services.resolve_unmatched_aggregates`` -- only when exactly one
   canonical ``Event`` shares its date and that event's
   case/whitespace-normalized title exactly equals the provider event's
   normalized title.  No fuzzy or ranked matching: a date with zero or
   several plausible canonical events, or a title that is merely similar, is
   left unresolved and reported under its own reason.  Eventbrite exports
   carry no event-level title or date at all, so every unresolved Eventbrite
   row is reported unmatched for that reason.  See
   ``activate_unambiguous_mappings`` and
   ``events.services.resolve_unmatched_aggregates``.

Neither tier is a Studio page or a separate model: a human resolves an
ambiguous case by adding the exact pair to the current-registration-input
JSON file and re-running this script.

What does not land yet
----------------------

**Event content** -- title, dates, description, speakers and links for all 421
events -- is *not* in the database.  ``events.Event`` is deliberately thin, and
the content is served from ``content/public_projection/events.json``, which
``scripts/build_public_projection.py`` builds from ``_data/events.yaml`` in
``DataTalksClub/datatalksclub.github.io``.  The owner has ruled that this
repository must function without that legacy site, so that source is going
away and event content needs a new home.  That decision is pending; see
:data:`EVENT_CONTENT`.  This script names the gap and refuses to guess.

Note that the identity manifest *records* the legacy repository as provenance
(all 421 events carry ``source_repository = DataTalksClub/datatalksclub.github.io``).
That is history written into a checked-in file, not a live dependency: importing
it reads nothing outside this repository.

    uv run --frozen python scripts/prod/import_events.py \\
        --database .tmp/local.sqlite3 \\
        --current-registration-input _docs/migration-data/local-current-registration-input.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False

REGISTRATION_FACTS_PATH = (
    PROJECT_ROOT / "_docs" / "migration-data" / "event-registration-sources.json"
)
IDENTITY_MANIFEST_PATH = PROJECT_ROOT / "events" / "event_identity_manifest.json"
LUMA_RELATIVE_SOURCE = Path(".local/migration-data/events/luma-aggregate-v1")
EVENTBRITE_RELATIVE_SOURCE = Path(".local/migration-data/events/eventbrite/aggregate-v1.zip")

PROVIDERS = ("luma", "eventbrite")

# Event content has no database importer yet, and its only current source is the
# legacy GitHub Pages repository the owner is retiring. Naming the gap here keeps
# it out of the "quietly missing" category until the replacement source is chosen.
EVENT_CONTENT = {
    "imported": False,
    "reason": "source_decision_pending",
    "detail": (
        "Event title, dates, description, speakers and links are served from the "
        "checked public projection, built from _data/events.yaml in the legacy "
        "DataTalksClub/datatalksclub.github.io repository. That repository is not a "
        "permitted content source, so event content needs a new home before it can "
        "be imported here."
    ),
}


class EventImportError(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _main_checkout_root() -> Path:
    """The main checkout, where the protected registration exports live."""

    try:
        common_dir = subprocess.run(
            ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"),
            check=True,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise EventImportError("git_common_directory_unavailable") from error
    return Path(common_dir).resolve().parent


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def import_identities(*, manifest: Path | None = None, apply: bool = True) -> dict[str, Any]:
    """Import the reviewed identity/alias manifest atomically."""

    from events.identity import EventIdentityError, import_identity_manifest

    try:
        report = import_identity_manifest(path=manifest, dry_run=not apply)
    except (EventIdentityError, OSError, ValueError) as error:
        raise EventImportError("identity_manifest_invalid") from error
    return {
        "events": report.event_total,
        "aliases": report.alias_total,
        "events_created": report.events_created,
        "events_updated": report.events_updated,
        "aliases_created": report.aliases_created,
        "replayed": report.replayed,
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
# ``events.identity.create_event_identity`` had zero callers anywhere outside
# tests.  This section is that path.
#
# It is deliberately kept separate from ``activation_coverage`` below.  Minting
# an identity is safe, reviewable plumbing -- title and a canonical path,
# nothing a visitor's registration count depends on -- so it is fine to
# automate.  Resolving that event's registration *count* to a canonical Event,
# and separately activating it for public display, are different,
# already-gated decisions (see s.6 of the ingest inventory) and stay exactly
# as gated as they are today: this section never resolves or activates a
# ``HistoricalRegistrationAggregateRevision``.
#
# The aggregate-revision guard below used to be the only thing standing between
# this step and a duplicate row, and on a database built in production order it
# is empty when this step runs: identities are imported, then discovery runs,
# and the aggregates are staged only afterwards.  So an export event the
# reviewed manifest already describes -- under its legacy ``_data/events.yaml``
# source key, which discovery has no way to guess -- got a second ``Event`` with
# a second public id.  ``events.identity.ExistingEventIndex`` closes that: an
# export event whose date and normalized title exactly match one event we
# already have is recognised as that event and creates nothing.


def discover_new_provider_events(
    *, provider: str, discovered: tuple[Any, ...], apply: bool = True
) -> dict[str, Any]:
    """Create identities for provider events this database has never tracked.

    ``discovered`` items only need ``external_event_identifier``, ``title``,
    ``start_at`` and ``eligible_count`` attributes -- shaped for
    ``events.importers.DiscoveredLumaEvent`` today, provider-agnostic by
    contract for whenever an Eventbrite export carries its own title source.

    An event is skipped, not created, when any of these is true:

    - This database already holds an ``Event`` under our own provider source
      identity (idempotent replay -- a second run creates nothing new).
    - A ``HistoricalRegistrationAggregateRevision`` row already exists for
      ``(provider, external_event_identifier)`` -- resolved or not.  This is
      what keeps the step from racing ahead of the existing, separately
      tracked resolution backlog.  It only ever fires on a database whose
      aggregates were staged first, so it cannot be the only duplicate guard.
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
        EXISTING_EVENT_AMBIGUOUS,
        EXISTING_EVENT_DATE_UNUSABLE,
        EXISTING_EVENT_MATCHED,
        EventIdentityError,
        EventIdentityNotFound,
        ExistingEventIndex,
        canonical_detail_path,
        create_provider_event_identity,
        provider_source_identity,
        resolve_source_identity,
    )
    from events.models import HistoricalRegistrationAggregateRevision

    index = ExistingEventIndex()
    created: list[dict[str, Any]] = []
    no_metadata: list[dict[str, Any]] = []
    existing_events: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    already_tracked = 0
    for item in discovered:
        already_mapped = HistoricalRegistrationAggregateRevision.objects.filter(
            source_run__provider=provider,
            external_event_identifier=item.external_event_identifier,
        ).exists()
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
        if already_mapped or existing is not None:
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
                    "matched_canonical_path": canonical_detail_path(match.event.id),
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
            )
        except EventIdentityError as error:
            raise EventImportError("provider_event_identity_invalid") from error
        created.append(
            {
                **entry,
                "public_id": event.public_id,
                "canonical_path": canonical_detail_path(event.id),
                "reason": (
                    "Auto-created: no reviewed identity-manifest entry, no "
                    "aggregate revision row, and no event sharing this date "
                    "and exact title existed at run time -- title and canonical "
                    "path only, no registration count was resolved or "
                    "activated."
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
    count.  See ``events.importers.discover_luma_events`` for the read and
    ``discover_new_provider_events`` for the create-or-skip decision.
    """

    from events.importers import ProtectedSourceError, discover_luma_events

    try:
        discovered = discover_luma_events(luma_source)
    except ProtectedSourceError as error:
        raise EventImportError("luma_discovery_failed") from error
    return discover_new_provider_events(provider="luma", discovered=discovered, apply=apply)


# --------------------------------------------------------------------------
# Registration aggregates
# --------------------------------------------------------------------------


def load_registration_facts() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(REGISTRATION_FACTS_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EventImportError("registration_facts_unavailable") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise EventImportError("registration_facts_invalid")
    facts = {name: payload.get(name) for name in PROVIDERS}
    if any(not isinstance(value, dict) for value in facts.values()):
        raise EventImportError("registration_facts_invalid")
    return facts  # type: ignore[return-value]


def load_current_registration_input(path: Path | None):
    if path is None:
        return None
    from events.current_registration import (
        CurrentRegistrationInputError,
        load_current_registration_input as _load,
    )

    try:
        return _load(path)
    except CurrentRegistrationInputError as error:
        raise EventImportError(f"current_registration_input_{error.code}") from error


def mapping_bridges(current_input) -> tuple[dict[str, dict[str, dict[str, str]]], dict]:
    """Resolve input targets by exact Event source identity and build adapter bridges."""

    from events.identity import EventIdentityNotFound, resolve_source_identity

    bridges: dict[str, dict[str, dict[str, str]]] = {name: {} for name in PROVIDERS}
    target_events: dict[tuple[str, str, str], Any] = {}
    for mapping in current_input.mappings:
        try:
            event = resolve_source_identity(
                repository=mapping.canonical_repository,
                revision=mapping.canonical_revision,
                source_key=mapping.canonical_source_key,
            )
        except EventIdentityNotFound as error:
            raise EventImportError("current_registration_target_unavailable") from error
        target_key = mapping.canonical_identity
        if target_key in target_events and target_events[target_key].id != event.id:
            raise EventImportError("current_registration_target_ambiguous")
        target_events[target_key] = event
        bridges[mapping.provider][mapping.provider_event_identity] = {
            "repository": event.source_repository,
            "revision": event.source_revision,
            "source_key": event.source_key,
            "slug": event.slug,
        }
    return bridges, target_events


def derive_registration_sources(
    *,
    luma_source: Path,
    eventbrite_source: Path,
    current_input=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse both protected exports and reconcile them against the recorded facts."""

    from events.importers import derive_eventbrite, derive_luma

    facts = load_registration_facts()
    bridges: dict[str, dict[str, dict[str, str]]] = {name: {} for name in PROVIDERS}
    if current_input is not None:
        bridges, _target_events = mapping_bridges(current_input)
    try:
        luma = derive_luma(
            luma_source,
            expected_checksum=facts["luma"]["tree_sha256"],
            mapping_bridge=bridges["luma"],
            allow_partial_mapping=current_input is not None,
        )
        eventbrite = derive_eventbrite(
            eventbrite_source,
            expected_checksum=facts["eventbrite"]["prepared_archive_sha256"],
            mapping_bridge=bridges["eventbrite"],
            allow_partial_mapping=current_input is not None,
        )
    except Exception as error:
        # Never echo provider identifiers, paths, or protected parser diagnostics.
        raise EventImportError("registration_source_validation_failed") from error

    sources = (("luma", luma, facts["luma"]), ("eventbrite", eventbrite, facts["eventbrite"]))
    report: dict[str, Any] = {}
    for name, derived, expected in sources:
        observed = {
            "events": derived.manifest_event_total,
            "rows": derived.parsed_row_total,
            "eligible": derived.eligible_row_total,
            "excluded": derived.excluded_row_total,
            "quarantined_events": derived.quarantined_event_total,
        }
        expected_values = {
            "events": expected["event_total"],
            "rows": expected["row_total"],
            "eligible": expected["registration_total"],
            "excluded": expected.get("excluded_registration_total", 0),
            "quarantined_events": 0,
        }
        if observed != expected_values:
            raise EventImportError(f"{name}_registration_facts_mismatch")
        report[name] = {
            **observed,
            "validated": True,
            "database_written": True,
            "activation_state": (
                "explicit_current_event_pending"
                if current_input is not None and bridges[name]
                else "unresolved"
            ),
        }
    return report, {"luma": luma, "eventbrite": eventbrite}


def stage_registration_aggregates(
    *,
    derived_sources: dict[str, Any],
    source_report: dict[str, Any],
    current_input=None,
    correlation_id: str = "prod-import-events",
) -> dict[str, Any]:
    """Stage each derived source and activate only the explicitly mapped events."""

    from core.services import ServiceContext
    from events.identity import event_projection_record
    from events.importers import source_reference_digest
    from events.services import (
        activate_explicit_current_source,
        public_registration_total,
        stage_derived_source,
    )

    target_events: dict[tuple[str, str, str], Any] = {}
    if current_input is not None:
        _, target_events = mapping_bridges(current_input)
    mapping_set_revision = current_input.mapping_set_revision if current_input else 1
    context = ServiceContext(
        correlation_id=correlation_id,
        actor_ref=f"system:{correlation_id}",
    )
    result: dict[str, Any] = {
        "input_supplied": current_input is not None,
        "mapping_set_revision": mapping_set_revision,
        "explicit_mapping_total": len(current_input.mappings) if current_input else 0,
        "sources": {},
    }
    for provider in PROVIDERS:
        derived = derived_sources[provider]
        run, created = stage_derived_source(
            provider=provider,
            derived=derived,
            reference_digest=source_reference_digest(f"{correlation_id}-{provider}"),
            mapping_set_revision=mapping_set_revision,
            actor=None,
            context=context,
        )
        selected_external_ids = tuple(
            candidate.external_event_identifier
            for candidate in derived.candidates
            if candidate.proposal is not None
        )
        activated = False
        if selected_external_ids:
            run = activate_explicit_current_source(
                run.id,
                external_event_identifiers=selected_external_ids,
                reason_code="current_event_activation",
                actor=None,
                context=context,
            )
            activated = True
        source_report[provider]["activation_state"] = "active" if activated else "unresolved"
        result["sources"][provider] = {
            "run_created": created,
            "run_state": run.state,
            "explicit_mapping_total": len(selected_external_ids),
            "unresolved_total": run.aggregate_revisions.filter(event__isnull=True).count(),
            "activated": activated,
        }
    public_counts: list[int] = []
    for event in target_events.values():
        total = public_registration_total(event_projection_record(event))
        if total is None:
            raise EventImportError("current_registration_total_unavailable")
        public_counts.append(total.count)
    result["public_event_total"] = len(public_counts)
    result["public_count_total"] = sum(public_counts)
    result["activation_state"] = (
        "active" if current_input is not None and public_counts else "unresolved"
    )
    return result


def activate_unambiguous_mappings(
    *, luma_source: Path, correlation_id: str = "prod-import-events"
) -> dict[str, Any]:
    """Resolve only the still-unresolved aggregates an exact title+date proves.

    This calls ``events.services.resolve_unmatched_aggregates`` -- a plain
    resolution pass, not a state-machine transition; there is no separate
    mapping model or review row to change.  It never touches the reviewed
    identity manifest or
    ``_docs/migration-data/local-current-registration-input.json``; both stay
    exactly as they are.  A still-unresolved aggregate resolves only when
    exactly one canonical ``Event`` shares its date and that event's
    case/whitespace-normalized title equals the provider event's normalized
    title exactly -- no fuzzy or ranked match.  Everything else stays
    unresolved, reported here under the specific reason it did not qualify,
    same as it would if this step did not exist.

    Eventbrite's export carries no event-level title or date at all (only order- and
    attendee-level columns), so every still-unresolved Eventbrite row is reported
    as unmatched with ``provider_event_metadata_unavailable`` -- there is no evidence
    to match on, not an unexamined gap.
    """

    from core.services import ServiceContext
    from events.importers import ProtectedSourceError, discover_luma_events
    from events.services import ProviderEventMetadata, resolve_unmatched_aggregates

    try:
        discovered = discover_luma_events(luma_source)
    except ProtectedSourceError as error:
        raise EventImportError("luma_discovery_failed") from error
    luma_metadata = {
        item.external_event_identifier: ProviderEventMetadata(
            external_event_identifier=item.external_event_identifier,
            title=item.title,
            start_at=item.start_at,
        )
        for item in discovered
    }
    context = ServiceContext(
        correlation_id=correlation_id,
        actor_ref=f"system:{correlation_id}",
    )
    return {
        provider: resolve_unmatched_aggregates(
            provider=provider,
            provider_metadata=luma_metadata if provider == "luma" else {},
            actor=None,
            context=context,
        )
        for provider in PROVIDERS
    }


def activation_coverage(
    *, source_report: dict[str, Any], staged: dict[str, Any]
) -> dict[str, Any]:
    """Say plainly how much of the registration history is actually public.

    The adapters are not the gap -- resolving each provider event to a
    canonical Event is.  An operator reading a run should see the ratio, not a
    bare success.
    """

    from events.models import Event

    provider_events = sum(source_report[provider]["events"] for provider in PROVIDERS)
    resolved = sum(
        staged["sources"][provider]["explicit_mapping_total"] for provider in PROVIDERS
    )
    unresolved = sum(staged["sources"][provider]["unresolved_total"] for provider in PROVIDERS)
    return {
        "canonical_events": Event.objects.count(),
        "provider_events": provider_events,
        "resolved": resolved,
        "unresolved": unresolved,
        "summary": (
            f"{resolved} of {provider_events} provider events resolved to a canonical event; "
            f"{unresolved} remain unresolved and render no registration count"
        ),
    }


def run(
    *,
    identity_manifest: Path | None = None,
    luma_source: Path,
    eventbrite_source: Path,
    current_registration_input: Path | None = None,
    correlation_id: str = "prod-import-events",
) -> dict[str, Any]:
    if not luma_source.is_dir() or not eventbrite_source.is_file():
        raise EventImportError("registration_source_unavailable")

    identities = import_identities(manifest=identity_manifest, apply=True)
    # Distinct top-level key, deliberately never merged into `identities` (the
    # reviewed-manifest replay) or `activation_coverage` (the registration-count
    # gate) -- an operator reading the report must not mistake an automatic
    # creation for either the reviewed manifest changing or a count activating.
    new_event_identities = {
        "luma": discover_new_luma_event_identities(luma_source=luma_source, apply=True),
    }
    current_input = load_current_registration_input(current_registration_input)
    source_report, derived_sources = derive_registration_sources(
        luma_source=luma_source,
        eventbrite_source=eventbrite_source,
        current_input=current_input,
    )
    staged = stage_registration_aggregates(
        derived_sources=derived_sources,
        source_report=source_report,
        current_input=current_input,
        correlation_id=correlation_id,
    )
    # A distinct top-level key, deliberately never merged into `registration_import`
    # (what the explicit current-registration-input path resolved) or
    # `activation_coverage` (which reports that same explicit-only ratio) -- an
    # operator must be able to see exactly which additional aggregates this narrower,
    # automatic pass resolved, and why every other one is still unresolved.
    aggregate_auto_resolution = activate_unambiguous_mappings(
        luma_source=luma_source, correlation_id=correlation_id
    )
    return {
        "identities": identities,
        "new_event_identities": new_event_identities,
        "registration_sources": source_report,
        "registration_import": staged,
        "aggregate_auto_resolution": aggregate_auto_resolution,
        "activation_coverage": activation_coverage(
            source_report=source_report, staged=staged
        ),
        "event_content": EVENT_CONTENT,
    }


def _parser() -> argparse.ArgumentParser:
    main_root = _main_checkout_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--identity-manifest", type=Path, default=IDENTITY_MANIFEST_PATH)
    parser.add_argument("--luma-source", type=Path, default=main_root / LUMA_RELATIVE_SOURCE)
    parser.add_argument(
        "--eventbrite-source", type=Path, default=main_root / EVENTBRITE_RELATIVE_SOURCE
    )
    parser.add_argument(
        "--current-registration-input",
        type=Path,
        default=None,
        help=(
            "JSON file naming exact current provider identities and their canonical "
            "Event source identities. Everything it does not name stays "
            "review-required and renders no count."
        ),
    )
    parser.add_argument(
        "--discover-new-events-only",
        action="store_true",
        help=(
            "Import identities, then discover and create identities for new Luma "
            "events only. Skips registration-aggregate derivation entirely, so it "
            "does not require --eventbrite-source and does not require "
            "--luma-source to match the pinned checksum in "
            "event-registration-sources.json -- use this to land a fresh export "
            "the registration pipeline has not been reconciled against yet."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        _configure(args.database.resolve())
        if args.discover_new_events_only:
            if not args.luma_source.resolve().is_dir():
                raise EventImportError("registration_source_unavailable")
            report = {
                "identities": import_identities(
                    manifest=args.identity_manifest.resolve(), apply=True
                ),
                "new_event_identities": {
                    "luma": discover_new_luma_event_identities(
                        luma_source=args.luma_source.resolve(), apply=True
                    ),
                },
            }
        else:
            report = run(
                identity_manifest=args.identity_manifest.resolve(),
                luma_source=args.luma_source.resolve(),
                eventbrite_source=args.eventbrite_source.resolve(),
                current_registration_input=(
                    args.current_registration_input.resolve()
                    if args.current_registration_input is not None
                    else None
                ),
            )
    except EventImportError as error:
        # The error carries a condition code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
