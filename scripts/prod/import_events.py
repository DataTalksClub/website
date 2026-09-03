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

What lands in the database
--------------------------

**Identity** (``events/event_identity_manifest.json``, 421 events, 1684
aliases): the uuid, public id, slug, title, source pointer and alias paths that
make an event addressable.  Replaying reports ``replayed`` and creates nothing.

**Registration aggregates** (Luma and Eventbrite): *counts only*.  No attendee
row is read into the database by any path here -- the adapters reduce each
export to a per-event total, reconcile it against the recorded safe facts in
``_docs/migration-data/event-registration-sources.json``, and store a
``HistoricalRegistrationAggregateRevision``.  A count only becomes public once
its provider event is explicitly mapped to a canonical event.

**Coverage is the interesting number.**  The adapters work; the mappings are
the backlog.  Every run reports ``activation_coverage`` so an operator sees
"3 of 383 provider events activated, 380 awaiting mapping review" rather than a
silent success.  Unmapped events render no registration count at all.

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
                else "mapping_review_required"
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
            auto_map_explicit=current_input is not None,
        )
        selected_external_ids = tuple(
            candidate.external_event_identifier
            for candidate in derived.candidates
            if candidate.proposal is not None
        )
        mapping_ids = tuple(
            run.aggregate_revisions.filter(
                mapping__external_event_identifier__in=selected_external_ids,
            ).values_list("mapping_id", flat=True)
        )
        if len(mapping_ids) != len(selected_external_ids):
            raise EventImportError("current_registration_mapping_missing")
        activated = False
        if mapping_ids:
            run = activate_explicit_current_source(
                run.id,
                mapping_ids=mapping_ids,
                reason_code="current_event_activation",
                actor=None,
                context=context,
            )
            activated = True
        source_report[provider]["activation_state"] = (
            "active" if activated else "mapping_review_required"
        )
        result["sources"][provider] = {
            "run_created": created,
            "run_state": run.state,
            "explicit_mapping_total": len(mapping_ids),
            "legacy_review_required_total": run.aggregate_revisions.filter(
                mapping__state="review_required"
            ).count(),
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
        "active" if current_input is not None and public_counts else "mapping_review_required"
    )
    return result


def activation_coverage(
    *, source_report: dict[str, Any], staged: dict[str, Any]
) -> dict[str, Any]:
    """Say plainly how much of the registration history is actually public.

    The adapters are not the gap -- the per-event mapping decisions are.  An
    operator reading a run should see the ratio, not a bare success.
    """

    from events.models import Event

    provider_events = sum(source_report[provider]["events"] for provider in PROVIDERS)
    activated = sum(
        staged["sources"][provider]["explicit_mapping_total"] for provider in PROVIDERS
    )
    review_required = sum(
        staged["sources"][provider]["legacy_review_required_total"] for provider in PROVIDERS
    )
    return {
        "canonical_events": Event.objects.count(),
        "provider_events": provider_events,
        "activated": activated,
        "review_required": review_required,
        "summary": (
            f"{activated} of {provider_events} provider event mappings activated; "
            f"{review_required} await mapping review and render no registration count"
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
    return {
        "identities": identities,
        "registration_sources": source_report,
        "registration_import": staged,
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
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        _configure(args.database.resolve())
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
