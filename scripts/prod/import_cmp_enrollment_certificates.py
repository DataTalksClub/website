#!/usr/bin/env python3
"""Reconcile issued CMP certificate URLs for one already-imported cohort.

Dry-run is the default.  ``--apply`` is required to write, and deployed writes
still require the shared target selector's separate production opt-in.
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

# The CMP export is frozen migration history, so this is the one-time model even
# though the run reconciles: it matches issued certificate URLs against cohorts an
# earlier import already wrote, exactly as ``import_cmp_content`` does.
SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--source", required=True, type=Path, help="bound CMP SQLite export")
    parser.add_argument(
        "--source-course",
        required=True,
        help="exact CMP cohort slug, for example ai-dev-tools-2025",
    )
    parser.add_argument(
        "--target-cohort",
        required=True,
        help="exact website cohort slug, for example ai-dev-tools-zoomcamp-2025",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write required URL updates; without this flag the command is read-only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_target(parser, args)

    from courses.services.cmp_certificate_reconciliation import (
        CmpCertificateReconciliationError,
        reconcile_cmp_enrollment_certificates,
    )

    try:
        result = reconcile_cmp_enrollment_certificates(
            args.source,
            source_course_slug=args.source_course,
            target_cohort_slug=args.target_cohort,
            apply=args.apply,
        )
    except CmpCertificateReconciliationError as error:
        print(json.dumps({"error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result.summary(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
