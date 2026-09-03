#!/usr/bin/env python3
"""Import CMP-export learner accounts into a database.

One-time import.  Reads a CMP production export read-only and writes the
learner-account layer of step 4 in ``_docs/runbooks/production-data-migration.md``:
``accounts_customuser`` (20,009 rows, measured) and ``account_emailaddress``
(20,005 rows, measured, plus synthesized rows for the four accounts the export
gives none). It does not import enrollments, submissions, answers, reviews or
course registrations -- those belong to a separate importer that reconciles
against the cohorts and homework ``import_cmp_content`` writes.

No account is ever created with a usable password, staff or superuser rights,
or a ``SocialAccount`` row -- see ``accounts.services.cmp_learner_import`` for
the full contract and why.

Resumable.  Progress is tracked per table in ``CmpLearnerImportProgress``, in
batches whose writes and watermark advance share one transaction, so a process
killed mid-run can be re-run and picks up where it left off -- see
``--status`` to check how far a run got without touching the source export.

    uv run --frozen python scripts/prod/import_cmp_learners.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --source /data/tmp/rds-export/rds-prod-20260902-012536.db

    uv run --frozen python scripts/prod/import_cmp_learners.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --source /data/tmp/rds-export/rds-prod-20260902-012536.db --dry-run

    uv run --frozen python scripts/prod/import_cmp_learners.py \\
        --database .tmp/production-prep-current.sqlite3 --status
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

SYNC_MODEL = "one-time"
# accounts_customuser has no prerequisite domain rows of its own -- it can
# populate a database with none present. (account_emailaddress and the
# synthesis pass depend only on the accounts this same run just wrote.)
BOOTSTRAPS_EMPTY_DATABASE = True


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--source", type=Path, help="CMP production export")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Rows per committed batch (default: the service's own default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report source and already-imported counts. Write nothing.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report accumulated progress. Does not open --source at all.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure(args.database.resolve())

    from accounts.services.cmp_learner_import import (
        DEFAULT_BATCH_SIZE,
        CmpLearnerImportError,
        dry_run_counts,
        import_cmp_learners,
        progress_status,
    )

    try:
        if args.status:
            report = progress_status()
        elif args.dry_run:
            if args.source is None:
                _parser().error("--dry-run requires --source")
            report = dry_run_counts(args.source.resolve())
        else:
            if args.source is None:
                _parser().error("--source is required unless --status is given")
            batch_size = args.batch_size or DEFAULT_BATCH_SIZE
            result = import_cmp_learners(args.source.resolve(), batch_size=batch_size)
            report = result.summary()
    except CmpLearnerImportError as error:
        # The error carries a condition code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
