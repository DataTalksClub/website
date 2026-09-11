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
# It creates a cohort, and its family, from the edition slug itself when CMP
# publishes one the database does not have, so it no longer needs a seeded
# catalogue to write into. It still reconciles everything else against the rows
# the course repositories wrote, which is why it runs last.
BOOTSTRAPS_EMPTY_DATABASE = True

# CMP exports the AI Dev Tools editions as "ai-dev-tools-<year>", which already
# mechanically de-suffixes to family "ai-dev-tools" -- the site's canonical
# family slug (see FAMILY_SLUG_OVERRIDES in courses/services/curriculum_import.py
# for the matching correction on the course-repository sync side). No override
# is needed here: every CMP edition slug's family, including this one, is
# already exactly its own de-suffixed form.
FAMILY_SLUG_OVERRIDES: dict[str, str] = {}

# A reviewed correction: ai-dev-tools's own repository still declares modules 3
# and 4's homework files under a zero-padded slug ("hw03"/"hw04", left over
# from before the assignments were finalized and re-titled for CMP) while CMP's copy of
# the same two assignments uses "hw3"/"hw4". Modules 1 and 2 already agree with CMP
# ("hw1"/"hw2") and need no correction. Every other course's repository and CMP slugs
# already agree, so no other entry is needed here.
HOMEWORK_SLUG_OVERRIDES: dict[str, dict[str, str]] = {
    "ai-dev-tools": {"hw03": "hw3", "hw04": "hw4"},
}


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
        result = import_cmp_course_content(
            args.source,
            cohort_slugs=args.cohort,
            family_slug_overrides=FAMILY_SLUG_OVERRIDES,
            homework_slug_overrides=HOMEWORK_SLUG_OVERRIDES,
        )
    except CmpContentImportError as error:
        # The error carries a code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(result.summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
