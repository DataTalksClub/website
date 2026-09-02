#!/usr/bin/env python3
"""Run the bounded, repeatable local production-data rehearsal.

This command composes the existing local-only preparation seams.  It writes only to an
explicit SQLite database below ``.tmp/`` and never connects to a deployed database.

The public event identity manifest and course catalog are imported into the database.  The
protected Eventbrite and Luma exports are parsed and reconciled against their recorded safe
facts.  Legacy candidates remain review-required; an optional explicit current-event mapping
input can stage and activate only those exact provider identities so a fresh database can render
their aggregate count without a title/date guess.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
REGISTRATION_FACTS_PATH = (
    PROJECT_ROOT / "_docs" / "migration-data" / "event-registration-sources.json"
)
LUMA_RELATIVE_SOURCE = Path(".local/migration-data/events/luma-aggregate-v1")
EVENTBRITE_RELATIVE_SOURCE = Path(".local/migration-data/events/eventbrite/aggregate-v1.zip")
ORCHESTRATOR_SCHEMA_VERSION = 1


class LocalPreparationError(RuntimeError):
    """A safe, bounded refusal to run the local rehearsal."""


def _main_checkout_root() -> Path:
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
        raise LocalPreparationError("git_common_directory_unavailable") from error
    return Path(common_dir).resolve().parent


def _local_database_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve(strict=False)
    try:
        path.relative_to((PROJECT_ROOT / ".tmp").resolve())
    except ValueError as error:
        raise LocalPreparationError("database_must_be_under_tmp") from error
    if path.suffix != ".sqlite3":
        raise LocalPreparationError("database_must_be_sqlite")
    return path


def _configure_local_environment(database: Path) -> None:
    configured_environment = os.getenv("DTC_ENVIRONMENT")
    if configured_environment not in (None, "local"):
        raise LocalPreparationError("local_environment_required")
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ["DJANGO_SETTINGS_MODULE"] = "website.settings.local"


def _json_management_command(name: str, **options: Any) -> dict[str, Any]:
    from django.core.management import call_command

    output = io.StringIO()
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        call_command(name, stdout=output, stderr=output, verbosity=0, **options)
    except Exception as error:
        # Management-command diagnostics may contain source paths.  Keep the command's
        # public CLI bounded and let the caller rerun the individual command for debugging.
        raise LocalPreparationError(f"{name}_failed") from error
    finally:
        logging.disable(previous_logging_disable)
    lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        report = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise LocalPreparationError(f"{name}_report_invalid") from error
    if not isinstance(report, dict):
        raise LocalPreparationError(f"{name}_report_invalid")
    return report


def _load_registration_facts() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(REGISTRATION_FACTS_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LocalPreparationError("registration_facts_unavailable") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LocalPreparationError("registration_facts_invalid")
    facts = {name: payload.get(name) for name in ("luma", "eventbrite")}
    if any(not isinstance(value, dict) for value in facts.values()):
        raise LocalPreparationError("registration_facts_invalid")
    return facts  # type: ignore[return-value]


def _load_current_registration_input(path: Path | None):
    if path is None:
        return None
    from events.current_registration import (
        CurrentRegistrationInputError,
        load_current_registration_input,
    )

    try:
        return load_current_registration_input(path)
    except CurrentRegistrationInputError as error:
        raise LocalPreparationError(f"current_registration_input_{error.code}") from error


def _current_mapping_bridges(current_input) -> tuple[dict[str, dict[str, dict[str, str]]], dict]:
    """Resolve input targets by exact Event source identity and build adapter bridges."""

    from events.identity import EventIdentityNotFound, resolve_source_identity

    bridges: dict[str, dict[str, dict[str, str]]] = {
        "luma": {},
        "eventbrite": {},
    }
    target_events: dict[tuple[str, str, str], Any] = {}
    for mapping in current_input.mappings:
        try:
            event = resolve_source_identity(
                repository=mapping.canonical_repository,
                revision=mapping.canonical_revision,
                source_key=mapping.canonical_source_key,
            )
        except EventIdentityNotFound as error:
            raise LocalPreparationError("current_registration_target_unavailable") from error
        target_key = mapping.canonical_identity
        if target_key in target_events and target_events[target_key].id != event.id:
            raise LocalPreparationError("current_registration_target_ambiguous")
        target_events[target_key] = event
        bridges[mapping.provider][mapping.provider_event_identity] = {
            "repository": event.source_repository,
            "revision": event.source_revision,
            "source_key": event.source_key,
            "slug": event.slug,
        }
    return bridges, target_events


def _registration_source_report(
    *,
    luma_source: Path,
    eventbrite_source: Path,
    current_input=None,
) -> dict[str, Any]:
    report, _derived = _registration_source_derivations(
        luma_source=luma_source,
        eventbrite_source=eventbrite_source,
        current_input=current_input,
    )
    return report


def _registration_source_derivations(
    *,
    luma_source: Path,
    eventbrite_source: Path,
    current_input=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from events.importers import derive_eventbrite, derive_luma

    facts = _load_registration_facts()
    bridges = {"luma": {}, "eventbrite": {}}
    if current_input is not None:
        bridges, _target_events = _current_mapping_bridges(current_input)
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
        raise LocalPreparationError("registration_source_validation_failed") from error

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
            raise LocalPreparationError(f"{name}_registration_facts_mismatch")
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


def run(
    *,
    database: Path,
    course_modules_input: Path,
    identity_manifest: Path,
    luma_source: Path,
    eventbrite_source: Path,
    current_registration_input: Path | None = None,
    cmp_source_db: Path | None = None,
    fresh: bool,
) -> dict[str, Any]:
    if fresh and any(
        path.exists()
        for path in (
            database,
            database.with_name(f"{database.name}-shm"),
            database.with_name(f"{database.name}-wal"),
        )
    ):
        raise LocalPreparationError("fresh_database_already_exists")
    if not course_modules_input.is_file() or not identity_manifest.is_file():
        raise LocalPreparationError("preparation_manifest_unavailable")
    if not luma_source.is_dir() or not eventbrite_source.is_file():
        raise LocalPreparationError("registration_source_unavailable")

    database.parent.mkdir(parents=True, exist_ok=True)
    _configure_local_environment(database)

    import django

    django.setup()

    current_input = _load_current_registration_input(current_registration_input)

    migrations = _json_management_command("migrate", interactive=False)
    identities = _json_management_command(
        "import_event_identities", apply=True, manifest=identity_manifest
    )
    cmp_content: dict[str, Any]
    if cmp_source_db is None:
        cmp_content = {"imported": False, "skipped": "source_not_supplied"}
    else:
        from courses.services.local_cmp_content_import import (
            LocalCmpContentImportError,
            import_local_cmp_content,
        )

        try:
            cmp_content = import_local_cmp_content(cmp_source_db, database).summary()
        except LocalCmpContentImportError as error:
            raise LocalPreparationError(f"cmp_content_{error}") from error
    catalog = _json_management_command("seed_local_courses")
    modules_check = _json_management_command(
        "prepare_local_course_modules", manifest_path=course_modules_input, check=True
    )
    modules = _json_management_command(
        "prepare_local_course_modules", manifest_path=course_modules_input
    )
    registration_sources, derived_sources = _registration_source_derivations(
        luma_source=luma_source,
        eventbrite_source=eventbrite_source,
        current_input=current_input,
    )
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
        _, target_events = _current_mapping_bridges(current_input)
    mapping_set_revision = current_input.mapping_set_revision if current_input else 1
    context = ServiceContext(
        correlation_id="local-production-prep",
        actor_ref="system:local-production-prep",
    )
    registration_import: dict[str, Any] = {
        "input_supplied": current_input is not None,
        "mapping_set_revision": mapping_set_revision,
        "explicit_mapping_total": len(current_input.mappings) if current_input else 0,
        "sources": {},
    }
    for provider in ("luma", "eventbrite"):
        derived = derived_sources[provider]
        run, created = stage_derived_source(
            provider=provider,
            derived=derived,
            reference_digest=source_reference_digest(f"local-production-prep-{provider}"),
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
            raise LocalPreparationError("current_registration_mapping_missing")
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
        registration_sources[provider]["activation_state"] = (
            "active" if activated else "mapping_review_required"
        )
        registration_import["sources"][provider] = {
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
            raise LocalPreparationError("current_registration_total_unavailable")
        public_counts.append(total.count)
    registration_import["public_event_total"] = len(public_counts)
    registration_import["public_count_total"] = sum(public_counts)
    if current_input is None:
        registration_import["activation_state"] = "mapping_review_required"
    else:
        registration_import["activation_state"] = (
            "active" if public_counts else "mapping_review_required"
        )
    return {
        "schema_version": ORCHESTRATOR_SCHEMA_VERSION,
        "database": {"environment": "local", "sqlite": True, "fresh_requested": fresh},
        "steps": {
            "migrations": {"completed": True, "report": migrations},
            "event_identities": identities,
            "cmp_content": cmp_content,
            "course_catalog": catalog,
            "course_modules_check": modules_check,
            "course_modules": modules,
        },
        "registration_sources": registration_sources,
        "registration_import": registration_import,
    }


def _parser() -> argparse.ArgumentParser:
    main_root = _main_checkout_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="SQLite path below this checkout's .tmp/")
    parser.add_argument("--course-modules-input", required=True, type=Path)
    parser.add_argument(
        "--identity-manifest",
        type=Path,
        default=PROJECT_ROOT / "events" / "event_identity_manifest.json",
    )
    parser.add_argument(
        "--luma-source",
        type=Path,
        default=main_root / LUMA_RELATIVE_SOURCE,
    )
    parser.add_argument(
        "--eventbrite-source",
        type=Path,
        default=main_root / EVENTBRITE_RELATIVE_SOURCE,
    )
    parser.add_argument(
        "--current-registration-input",
        type=Path,
        default=None,
        help=(
            "JSON file containing exact current provider identities and canonical Event "
            "source identities; legacy candidates remain review-required."
        ),
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Refuse to run if the selected SQLite database or its WAL files already exist.",
    )
    parser.add_argument(
        "--cmp-source-db",
        type=Path,
        default=None,
        help=(
            "Protected CMP SQLite snapshot to import as sanitized course content. "
            "The file is copied and read only; learner tables are not imported."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        database = _local_database_path(args.database)
        report = run(
            database=database,
            course_modules_input=Path(args.course_modules_input).resolve(),
            identity_manifest=Path(args.identity_manifest).resolve(),
            luma_source=Path(args.luma_source).resolve(),
            eventbrite_source=Path(args.eventbrite_source).resolve(),
            current_registration_input=(
                Path(args.current_registration_input).resolve()
                if args.current_registration_input is not None
                else None
            ),
            cmp_source_db=(
                Path(args.cmp_source_db).resolve() if args.cmp_source_db is not None else None
            ),
            fresh=args.fresh,
        )
    except LocalPreparationError as error:
        print(f"local preparation refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
