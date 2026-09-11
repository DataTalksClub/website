#!/usr/bin/env python3
"""Import the CMP export's learner history into a database.

One-time import, and the second half of step 4 in
``_docs/runbooks/production-data-migration.md``.
``import_cmp_learners.py`` moves the accounts; this moves everything that hangs
off one -- course registrations, enrollments, homework submissions and answers,
project submissions, peer reviews, criteria responses, evaluation scores and
per-user Wrapped statistics.

It **reconciles and never invents**.  Cohorts, homework, questions, projects and
criteria come from ``import_cmp_content``; accounts come from
``import_cmp_learners``.  A row whose parent is not there is counted under a
named bucket and skipped -- never given a placeholder parent, which would turn a
reportable gap into data that looks real.  So the run order is
``import_cmp_content``, then ``import_cmp_learners``, then this.

Run order matters for a second reason: this reads the account claims
``import_cmp_learners`` wrote, through ``--user-claims-file``.  Point it at the
same file that run used.

Resumable.  Progress is tracked per table in ``CmpHistoryImportProgress``, in
batches whose writes and watermark advance share one transaction, so a process
killed mid-run can be re-run and picks up where it left off -- see ``--status``
to check how far a run got without touching the source export.  Which target row
this importer created for a given CMP source id is script-owned state, not a
column on a live model -- one ``CmpHistoryClaim`` row per imported source id,
in this same database, committed with the batch that created it.

Reports carry counts and bounded codes only.  The payload is learner answers,
names and addresses, and none of it is ever printed or logged.

    uv run --frozen python scripts/prod/import_cmp_learner_history.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --source /data/tmp/rds-export/cmp/rds-prod-20260905-182754.db

    uv run --frozen python scripts/prod/import_cmp_learner_history.py \\
        --database .tmp/production-prep-current.sqlite3 \\
        --source /data/tmp/rds-export/cmp/rds-prod-20260905-182754.db --dry-run

    uv run --frozen python scripts/prod/import_cmp_learner_history.py \\
        --database .tmp/production-prep-current.sqlite3 --status
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
# Every row it writes hangs off a cohort, a homework or an account another
# importer wrote. On an empty database it resolves nothing and imports nothing,
# which is the silent no-op scripts/prod/__init__.py exists to warn about.
BOOTSTRAPS_EMPTY_DATABASE = False

# The same reviewed correction import_cmp_content.py applies: CMP still exports the AI
# Dev Tools editions as "ai-dev-tools-<year>", but their real, locally-stored family is
# "ai-dev-tools-zoomcamp". Without it, this importer's own cohort lookup misses every
# ai-dev-tools-zoomcamp edition and reports its whole learner history unresolved.
FAMILY_SLUG_OVERRIDES: dict[str, str] = {
    "ai-dev-tools": "ai-dev-tools-zoomcamp",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_target_arguments(parser)
    parser.add_argument("--source", type=Path, help="CMP production export")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Rows per committed batch (default: the service's own default).",
    )
    parser.add_argument(
        "--table",
        action="append",
        default=None,
        help="Limit to one source table. Repeatable. Dependency order still applies.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report source and already-claimed counts. Write nothing.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report accumulated progress. Does not open --source at all.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_target(parser, args)

    from accounts.models import CmpLearnerClaim
    from accounts.services.cmp_learner_import import CmpLearnerImportError
    from courses.services.cmp_learner_history_import import (
        DEFAULT_BATCH_SIZE,
        CmpHistoryImportError,
        dry_run_counts,
        import_cmp_learner_history,
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
            # The account claims import_cmp_learners.py recorded in this same
            # database. Reading them from the database -- not from a file --
            # is what keeps this importer from reconciling against another
            # database's accounts (audit REL-03).
            user_claims = dict(
                CmpLearnerClaim.objects.values_list("source_id", "user_id")
            )
            result = import_cmp_learner_history(
                args.source.resolve(),
                user_claims=user_claims,
                batch_size=args.batch_size or DEFAULT_BATCH_SIZE,
                tables=args.table,
                family_slug_overrides=FAMILY_SLUG_OVERRIDES,
            )
            report = result.summary()
    except (CmpHistoryImportError, CmpLearnerImportError) as error:
        # The error carries a condition code, never a source value.
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
