#!/usr/bin/env python3
"""Run the explicit production-sourced local dataset workflows.

This module is intentionally separate from ``seed_local_data.py``.  Its inputs
are reviewed exports and course checkouts; it is never used by ``make data``.

``dataset`` (and its alias ``bootstrap``) issues exactly three commands, in the
order :data:`DATASET_STEPS` declares: the rebuild gate, ``prepare_local_data.py``
-- which alone owns ``migrate``, source registration, the legacy Zoomcamp import,
the checkout refresh, every import and the editorial sync -- and
``verify_local_dataset.py``.  Nothing here touches the dataset database itself, so
``PRODUCTION_PREP_FRESH=1`` finds it absent and ``--fresh`` holds.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.content import _python, _run  # noqa: E402

DEFAULT_DATASET_ROOT: Final = ".tmp/production-prep-dataset"
#: The commands ``dataset`` issues, in order.  ``prepare_local_data.STEP_ORDER``
#: lists what the middle one does; the runbook's step table shows both.
DATASET_STEPS: Final = (
    "scripts/rebuild_gate.py",
    "scripts/prepare_local_data.py",
    "scripts/verify_local_dataset.py",
)


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


@dataclass(frozen=True)
class DatasetValues:
    root: Path
    database: Path
    course_sources: Path
    editorial_sources: Path
    cmp_source: Path | None
    legacy_source: Path | None


def _dataset_values() -> DatasetValues:
    root = Path(os.environ.get("PRODUCTION_PREP_DATASET_ROOT", DEFAULT_DATASET_ROOT))
    database = Path(
        os.environ.get("PRODUCTION_PREP_DATASET_DATABASE", str(root / "dataset.sqlite3"))
    )
    course_sources = Path(
        os.environ.get("PRODUCTION_PREP_COURSE_SOURCE_DIR", str(root / "course-sources"))
    )
    editorial_sources = Path(
        os.environ.get("PRODUCTION_PREP_EDITORIAL_SOURCE_DIR", str(root / "editorial-sources"))
    )
    cmp_value = os.environ.get(
        "PRODUCTION_PREP_CMP_SOURCE",
        str(Path.home() / "git/course-management-platform/db/db.sqlite3"),
    )
    legacy_value = os.environ.get(
        "LEGACY_ZOOMCAMP_SOURCE", str(Path.home() / "git/zoomcamp-scoring")
    )
    return DatasetValues(
        root=root,
        database=database,
        course_sources=course_sources,
        editorial_sources=editorial_sources,
        cmp_source=Path(cmp_value) if cmp_value else None,
        legacy_source=Path(legacy_value) if legacy_value else None,
    )


def _rebuild_database(database: Path) -> None:
    _run(_python("scripts/rebuild_gate.py", str(database)))


def _prepare_local_data(values: DatasetValues) -> None:
    if values.legacy_source is not None and not values.legacy_source.is_dir():
        raise RuntimeError("LEGACY_ZOOMCAMP_SOURCE is not a checkout")
    # The checkout directories, not the database: prepare_local_data creates and
    # migrates that itself.
    values.course_sources.mkdir(parents=True, exist_ok=True)
    values.editorial_sources.mkdir(parents=True, exist_ok=True)
    command = list(
        _python(
            "scripts/prepare_local_data.py",
            "--database",
            str(values.database),
            "--course-checkout-root",
            str(values.course_sources),
            "--editorial-checkout-root",
            str(values.editorial_sources),
            "--refresh-checkouts",
        )
    )
    if values.legacy_source is not None:
        command.extend(("--legacy-zoomcamp-source", str(values.legacy_source)))
    if values.cmp_source is not None:
        command.extend(("--cmp-source-db", str(values.cmp_source)))
    if os.environ.get("PRODUCTION_PREP_FRESH"):
        command.append("--fresh")
    command.extend(shlex.split(os.environ.get("PRODUCTION_PREP_LOCAL_ARGS", "")))
    _run(tuple(command))


def _verify(database: Path) -> None:
    _run(_python("scripts/verify_local_dataset.py", "--database", str(database)))


def prepare_dataset() -> None:
    """Rebuild gate, the one-owner rehearsal, then the dataset gate."""

    values = _dataset_values()
    _rebuild_database(values.database)
    _prepare_local_data(values)
    _verify(values.database)


def prepare_bootstrap() -> None:
    """The same rebuild as ``dataset``.

    ``bootstrap`` used to add the legacy Zoomcamp import as a pre-step.  That import
    now runs inside ``prepare_local_data.py`` in its ``COURSE_CATALOGUE_ORDER``
    position for every rebuild, so the two commands are one sequence.
    """

    prepare_dataset()


def run_local(
    database: str,
    course_sources: str,
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
                args.cmp_source_db,
                args.fresh,
                args.extra,
            )
        elif args.command == "verify":
            _verify(Path(args.database))
        elif args.command == "run":
            run_server(Path(args.database or _dataset_values().database), args.port)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            # The step's own refusal (for example
            # ``editorial_sync_failed_<slug>``) is already on stderr; name the step.
            step = next((part for part in error.cmd if str(part).endswith(".py")), "")
            print(
                f"production data step failed: {step} exited {error.returncode}",
                file=sys.stderr,
            )
            return error.returncode or 1
        print(f"production data command failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
