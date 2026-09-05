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

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
# It creates a cohort, and its family, from the reviewed catalogue when CMP
# publishes one the database does not have, so it no longer needs a seeded
# catalogue to write into. It still reconciles everything else against the rows
# the course repositories wrote, which is why it runs last.
BOOTSTRAPS_EMPTY_DATABASE = True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--source", required=True, type=Path, help="CMP production export")
    parser.add_argument(
        "--cohort",
        action="append",
        default=None,
        help="Limit to one cohort slug. Repeatable.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_target(parser, args)

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
