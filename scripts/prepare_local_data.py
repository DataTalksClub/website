#!/usr/bin/env python3
"""Run the bounded, repeatable local production-data rehearsal.

This command composes the existing local-only preparation seams.  It writes only to an
explicit SQLite database below ``.tmp/`` and never connects to a deployed database.

The public event identity manifest and course catalog are imported into the database.  The
protected Eventbrite and Luma exports are parsed and reconciled against their recorded safe
facts, but their aggregate rows are not activated: an exact, reviewed event mapping is still
required before a registration total can become public.  This distinction keeps a successful
rehearsal from turning a provider identifier or a title/date guess into a public count.
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


def _registration_source_report(
    *,
    luma_source: Path,
    eventbrite_source: Path,
) -> dict[str, Any]:
    from events.importers import derive_eventbrite, derive_luma

    facts = _load_registration_facts()
    try:
        luma = derive_luma(luma_source, expected_checksum=facts["luma"]["tree_sha256"])
        eventbrite = derive_eventbrite(
            eventbrite_source,
            expected_checksum=facts["eventbrite"]["prepared_archive_sha256"],
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
            "database_written": False,
            "activation_state": "mapping_review_required",
        }
    return report


def run(
    *,
    database: Path,
    course_modules_input: Path,
    identity_manifest: Path,
    luma_source: Path,
    eventbrite_source: Path,
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

    migrations = _json_management_command("migrate", interactive=False)
    identities = _json_management_command(
        "import_event_identities", apply=True, manifest=identity_manifest
    )
    catalog = _json_management_command("seed_local_courses")
    modules_check = _json_management_command(
        "prepare_local_course_modules", manifest_path=course_modules_input, check=True
    )
    modules = _json_management_command(
        "prepare_local_course_modules", manifest_path=course_modules_input
    )
    registration_sources = _registration_source_report(
        luma_source=luma_source,
        eventbrite_source=eventbrite_source,
    )
    return {
        "schema_version": ORCHESTRATOR_SCHEMA_VERSION,
        "database": {"environment": "local", "sqlite": True, "fresh_requested": fresh},
        "steps": {
            "migrations": {"completed": True, "report": migrations},
            "event_identities": identities,
            "course_catalog": catalog,
            "course_modules_check": modules_check,
            "course_modules": modules,
        },
        "registration_sources": registration_sources,
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
        "--fresh",
        action="store_true",
        help="Refuse to run if the selected SQLite database or its WAL files already exist.",
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
            fresh=args.fresh,
        )
    except LocalPreparationError as error:
        print(f"local preparation refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
