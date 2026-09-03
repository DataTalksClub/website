#!/usr/bin/env python3
"""Import the reviewed homepage testimonials into a database.

One-time import.  The six quotes are frozen: real posts by named members, each
one recorded with the public link it was taken from, checked into
``courses/homepage_testimonials.json``.  Nothing upstream is going to move --
once they are in the database an editor curates them in the admin, and this
script is only the way the first six get there.  See ``scripts/prod/__init__.py``
for what the two sync models mean.

This is production *content*, not a fixture: none of these people or quotes is
invented.  A local database that wants obviously-fake testimonials should get
them from a seeder, not from here.

Replaying is safe.  Every row is keyed on its ``source_url``, so a second run
reports ``replayed`` and creates nothing, and a testimonial an editor added by
hand is never touched.

    uv run --frozen python scripts/prod/import_testimonials.py \\
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

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = True

REVIEWED_PATH = PROJECT_ROOT / "courses" / "homepage_testimonials.json"


class TestimonialImportFailure(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def run(*, path: Path | None = None, apply: bool = True) -> dict[str, Any]:
    from courses.services.testimonials import (
        TestimonialImportError,
        import_homepage_testimonials,
        load_reviewed_homepage_testimonials,
    )

    try:
        entries = load_reviewed_homepage_testimonials(path)
        if not apply:
            return {"total": len(entries), "created": 0, "updated": 0, "applied": False}
        report = import_homepage_testimonials(path)
    except TestimonialImportError as error:
        raise TestimonialImportFailure(str(error)) from error
    return {
        "total": report.total,
        "created": report.created,
        "updated": report.updated,
        "replayed": report.replayed,
        "applied": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--reviewed-file", type=Path, default=REVIEWED_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the reviewed file and write nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _configure(args.database.resolve())
        report = run(path=args.reviewed_file.resolve(), apply=not args.dry_run)
    except TestimonialImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
