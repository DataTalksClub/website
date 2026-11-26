#!/usr/bin/env python3
"""Fill imported learner profiles from the CMP export (work data + confirmation).

Reads a CMP SQLite export read-only and fills each claimed learner's blank
``LearnerProfile`` fields -- company, years of experience, role, name, country,
links -- from the CMP user row and the learner's latest CMP registration, and
carries that registration's time over as ``profile_confirmed_at`` so the
180-day review treats a recent CMP registrant as current.  Matches through
``CmpLearnerClaim`` only.  See ``accounts/services/cmp_learner_profile_import.py``.

Dry-run is the default; ``--apply`` writes.  Run after ``import_cmp_learners``.

    uv run --frozen python scripts/prod/import_cmp_learner_profiles.py \\
        --database .tmp/local.sqlite3 \\
        --source ~/prod/dtc-data/rds-export/cmp/rds-prod-20260910-182337.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--source", required=True, type=Path, help="CMP SQLite export")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the profile updates; without this flag the command is read-only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_target(parser, args)

    from accounts.services.cmp_learner_profile_import import (
        CmpLearnerProfileImportError,
        import_cmp_learner_profiles,
    )

    try:
        result = import_cmp_learner_profiles(args.source.expanduser(), apply=args.apply)
    except CmpLearnerProfileImportError as error:
        print(json.dumps({"error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result.summary(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
