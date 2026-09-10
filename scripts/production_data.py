#!/usr/bin/env python3
"""Run the explicit production-sourced local dataset workflows.

This module is intentionally separate from ``seed_local_data.py``.  Its inputs
are reviewed exports and course checkouts; it is never used by ``make data``.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
UV: Final = ("uv", "run", "--frozen", "python")
DEFAULT_DATASET_ROOT: Final = ".tmp/production-prep-dataset"
DEFAULT_REGISTRATION_INPUT: Final = "_docs/migration-data/local-current-registration-input.json"


def _python(script: str, *arguments: str) -> tuple[str, ...]:
    return (*UV, script, *arguments)


def _run(command: tuple[str, ...], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True)


def _local_environment(database: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DTC_ENVIRONMENT": "local",
            "DTC_SQLITE_PATH": str(database),
            "DJANGO_SETTINGS_MODULE": "website.settings.local",
        }
    )
    return environment


def _dataset_values() -> tuple[Path, Path, Path, Path | None, Path | None]:
    root = Path(os.environ.get("PRODUCTION_PREP_DATASET_ROOT", DEFAULT_DATASET_ROOT))
    database = Path(
        os.environ.get("PRODUCTION_PREP_DATASET_DATABASE", str(root / "dataset.sqlite3"))
    )
    course_sources = Path(
        os.environ.get("PRODUCTION_PREP_COURSE_SOURCE_DIR", str(root / "course-sources"))
    )
    registration_value = os.environ.get(
        "PRODUCTION_PREP_DATASET_REGISTRATION_INPUT", DEFAULT_REGISTRATION_INPUT
    )
    registration_input = Path(registration_value) if registration_value else None
    cmp_value = os.environ.get(
        "PRODUCTION_PREP_CMP_SOURCE",
        str(Path.home() / "git/course-management-platform/db/db.sqlite3"),
    )
    cmp_source = Path(cmp_value) if cmp_value else None
    return root, database, course_sources, registration_input, cmp_source


def _rebuild_database(database: Path) -> None:
    _run(_python("scripts/rebuild_gate.py", str(database)))


def _prepare_course_sources(root: Path, database: Path, course_sources: Path) -> None:
    if database.exists():
        raise RuntimeError("dataset database must be absent before course registration")
    root.mkdir(parents=True, exist_ok=True)
    _run(
        _python("manage.py", "migrate", "--no-input"),
        environment=_local_environment(database),
    )
    _run(
        _python(
            "scripts/content.py",
            "sources",
            "--database",
            str(database),
        )
    )
    course_sources.mkdir(parents=True, exist_ok=True)
    _run(
        _python(
            "scripts/content.py",
            "checkouts",
            "--database",
            str(database),
            "--checkout-root",
            str(course_sources),
        )
    )


def _prepare_local_data(
    database: Path,
    course_sources: Path,
    registration_input: Path | None,
    cmp_source: Path | None,
) -> None:
    command = list(
        _python(
            "scripts/prepare_local_data.py",
            "--database",
            str(database),
            "--course-checkout-root",
            str(course_sources),
        )
    )
    if registration_input is not None:
        command.extend(("--current-registration-input", str(registration_input)))
    if cmp_source is not None:
        command.extend(("--cmp-source-db", str(cmp_source)))
    if os.environ.get("PRODUCTION_PREP_FRESH"):
        command.append("--fresh")
    command.extend(shlex.split(os.environ.get("PRODUCTION_PREP_LOCAL_ARGS", "")))
    _run(tuple(command))


def _verify(database: Path) -> None:
    _run(_python("scripts/verify_local_dataset.py", "--database", str(database)))


def prepare_dataset() -> None:
    root, database, course_sources, registration_input, cmp_source = _dataset_values()
    _rebuild_database(database)
    _prepare_course_sources(root, database, course_sources)
    _prepare_local_data(database, course_sources, registration_input, cmp_source)
    _verify(database)


def prepare_bootstrap() -> None:
    root, database, course_sources, registration_input, cmp_source = _dataset_values()
    legacy_source_value = os.environ.get(
        "LEGACY_ZOOMCAMP_SOURCE", str(Path.home() / "git/zoomcamp-scoring")
    )
    _rebuild_database(database)
    _prepare_course_sources(root, database, course_sources)
    if legacy_source_value:
        legacy_source = Path(legacy_source_value)
        if not legacy_source.is_dir():
            raise RuntimeError("LEGACY_ZOOMCAMP_SOURCE is not a checkout")
        command = list(
            _python(
                "scripts/prod/import_legacy_zoomcamp.py",
                "--database",
                str(database),
                "--source-repo",
                str(legacy_source),
            )
        )
        command.extend(shlex.split(os.environ.get("IMPORT_LEGACY_ZOOMCAMP_ARGS", "")))
        _run(tuple(command))
    _prepare_local_data(database, course_sources, registration_input, cmp_source)
    _verify(database)


def run_local(
    database: str,
    course_sources: str,
    current_registration_input: str | None,
    cmp_source_db: str | None,
    fresh: bool,
    extra: list[str],
) -> None:
    command = list(
        _python(
            "scripts/prepare_local_data.py",
            "--database",
            database,
            "--course-checkout-root",
            course_sources,
        )
    )
    if current_registration_input:
        command.extend(("--current-registration-input", current_registration_input))
    if cmp_source_db:
        command.extend(("--cmp-source-db", cmp_source_db))
    if fresh:
        command.append("--fresh")
    command.extend(extra)
    _run(tuple(command))


def run_server(database: Path, port: int) -> None:
    _run(
        _python("manage.py", "runserver", f"0.0.0.0:{port}"),
        environment=_local_environment(database),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("dataset")
    commands.add_parser("bootstrap")

    local = commands.add_parser("local")
    local.add_argument("--database", required=True)
    local.add_argument("--course-checkout-root", required=True)
    local.add_argument("--current-registration-input")
    local.add_argument("--cmp-source-db")
    local.add_argument("--fresh", action="store_true")
    local.add_argument("extra", nargs=argparse.REMAINDER)

    verify = commands.add_parser("verify")
    verify.add_argument("--database", required=True)

    serve = commands.add_parser("run")
    serve.add_argument("--database")
    serve.add_argument("--port", type=int, default=8001)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "dataset":
            prepare_dataset()
        elif args.command == "bootstrap":
            prepare_bootstrap()
        elif args.command == "local":
            run_local(
                args.database,
                args.course_checkout_root,
                args.current_registration_input,
                args.cmp_source_db,
                args.fresh,
                args.extra,
            )
        elif args.command == "verify":
            _verify(Path(args.database))
        elif args.command == "run":
            _root, database, _sources, _registration, _cmp = _dataset_values()
            run_server(Path(args.database or database), args.port)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            return error.returncode or 1
        print(f"production data command failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
