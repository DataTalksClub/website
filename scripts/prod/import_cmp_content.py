#!/usr/bin/env python3
"""Import real CMP course content into a local development database.

Reads a CMP production export read-only and replaces the seeded placeholder copy on the
local catalogue's cohorts, and brings across the registration campaign definitions that
decide what is open.  Content only: no account, enrollment, submission, answer or
learner registration row is read.

    uv run --frozen python scripts/prod/import_cmp_content.py \
        --database .tmp/production-prep-current.sqlite3 \
        --source /data/tmp/rds-export/rds-prod-20260902-012536.db
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path, help="CMP production export")
    parser.add_argument(
        "--cohort",
        action="append",
        default=None,
        help="Limit to one cohort slug. Repeatable.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure(args.database.resolve())

    from courses.services.cmp_content_import import (
        CmpContentImportError,
        import_cmp_course_content,
    )

    try:
        result = import_cmp_course_content(args.source, cohort_slugs=args.cohort)
    except CmpContentImportError as error:
        # The error carries a code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(result.summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
