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

# CMP exports the AI Dev Tools editions as "ai-dev-tools-<year>", which
# mechanically de-suffixes to family "ai-dev-tools" -- but the site's
# canonical family slug is "ai-dev-tools-zoomcamp" (see FAMILY_SLUG_OVERRIDES
# in courses/services/curriculum_import.py for the matching correction on the
# course-repository sync side, and its comment for why the "-zoomcamp" suffix
# is kept). A reviewed override is needed here to re-add the suffix CMP's
# mechanical de-suffixing drops. Every other CMP edition slug's family already
# matches its own de-suffixed form and needs no entry here.
FAMILY_SLUG_OVERRIDES: dict[str, str] = {
    "ai-dev-tools": "ai-dev-tools-zoomcamp",
}

# CMP exports the 2026 Generative Media cohort under the un-suffixed slug
# "production-ai-agents-generative-media", whereas the site's cohort slug is
# "production-ai-agents-generative-media-2026".
COHORT_SLUG_OVERRIDES: dict[str, str] = {
    "production-ai-agents-generative-media": "production-ai-agents-generative-media-2026",
}

# Reviewed {repository_slug: cmp_slug} pairs, per course, for a repository homework
# CMP knows under another slug. On a modules or shared cohort the repository's slug
# wins (the encrypted answers and URLs are bound to it); an entry here pairs the two
# when the titles alone would not, and renames back a repository row an earlier
# version of the import gave CMP's slug.
#
# llm-zoomcamp: the 2026 repository declares homework-01..05; CMP ran them as
# hw1..hw5, and the 2026-11-26 local prep import renamed the repository rows to
# CMP's slugs before this rule existed.
#
# ai-dev-tools-zoomcamp's former hw03/hw04 entry is gone: its repository now
# declares hw3/hw4, the slugs CMP uses, and an entry for a slug the repository no
# longer has would rename the live row back to it.
HOMEWORK_SLUG_OVERRIDES: dict[str, dict[str, str]] = {
    "llm-zoomcamp": {
        "homework-01": "hw1",
        "homework-02": "hw2",
        "homework-03": "hw3",
        "homework-04": "hw4",
        "homework-05": "hw5",
    },
}

# Reviewed {repository_slug: cmp_slug} pairs, per course, for a repository-authored
# project attempt CMP runs under another slug. The repository keeps its slug and the
# definition its cohort.yaml authors (title, description, deadlines, peers); CMP's
# duplicate row is folded away when nobody submitted to it.
PROJECT_SLUG_OVERRIDES: dict[str, dict[str, str]] = {
    "production-ai-agents-generative-media": {
        "project-attempt-1": "capstone",
        "project-attempt-2": "capstone2",
    },
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
            cohort_slug_overrides=COHORT_SLUG_OVERRIDES,
            homework_slug_overrides=HOMEWORK_SLUG_OVERRIDES,
            project_slug_overrides=PROJECT_SLUG_OVERRIDES,
        )
    except CmpContentImportError as error:
        # The error carries a code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(result.summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
