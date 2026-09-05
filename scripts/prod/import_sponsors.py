#!/usr/bin/env python3
"""Import the reviewed public sponsor directory into a database.

One-time import.  The four featured sponsors and every other organization
DataTalks.Club has publicly thanked are frozen, reviewed facts, checked into
``core/sponsor_directory.json``.  Nothing upstream is going to move -- once
they are in the database an editor curates name, URL, tagline, lifecycle and
placement in Studio exactly as they do for an events_hub sponsor; only the
directory description and logo stay import-managed, so re-running this
script (after an edit to the reviewed file) is how those two change. See
``scripts/prod/__init__.py`` for what the two sync models mean.

This is production *content*, not a fixture: none of these companies is
invented, and every write goes through ``core.sponsors``' shared
create/update/archive/reactivate service -- the same one Studio and the admin
API use -- so an import gets the same revisioning and audit trail a Studio
edit does.

Replaying is safe.  Every row is keyed on its ``key``, so a second run with an
unchanged reviewed file reports ``replayed`` and creates nothing, and a
sponsor an editor added by hand is never touched.

    uv run --frozen python scripts/prod/import_sponsors.py \\
        --database .tmp/local.sqlite3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = True

REVIEWED_PATH = PROJECT_ROOT / "temporary" / "content" / "sponsor_directory.json"


class SponsorDirectoryImportFailure(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def run(*, path: Path | None = None, apply: bool = True) -> dict[str, Any]:
    from core.sponsors import (
        SponsorDirectoryImportError,
        import_public_sponsor_directory,
        load_reviewed_sponsor_directory,
    )

    try:
        source = path or REVIEWED_PATH
        entries = load_reviewed_sponsor_directory(source)
        if not apply:
            return {"total": len(entries), "created": 0, "updated": 0, "applied": False}
        report = import_public_sponsor_directory(source)
    except SponsorDirectoryImportError as error:
        raise SponsorDirectoryImportFailure(str(error)) from error
    return {
        "total": report.total,
        "created": report.created,
        "updated": report.updated,
        "replayed": report.replayed,
        "applied": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--reviewed-file", type=Path, default=REVIEWED_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the reviewed file and write nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        configure_target(parser, args)
        report = run(path=args.reviewed_file.resolve(), apply=not args.dry_run)
    except SponsorDirectoryImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
