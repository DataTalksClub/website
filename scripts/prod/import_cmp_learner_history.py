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
column on a live model -- see ``--claims-dir``.

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
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYNC_MODEL = "one-time"
# Every row it writes hangs off a cohort, a homework or an account another
# importer wrote. On an empty database it resolves nothing and imports nothing,
# which is the silent no-op scripts/prod/__init__.py exists to warn about.
BOOTSTRAPS_EMPTY_DATABASE = False


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
    parser.add_argument(
        "--claims-dir",
        type=Path,
        default=None,
        help=(
            "Where this importer records which target row it created for a "
            "given CMP source id, one file per table (default: the service's "
            "own default, project-local .tmp/). Durable resumability state -- "
            "keep it alongside --database across a kill-and-resume, never "
            "delete it between runs of the same import."
        ),
    )
    parser.add_argument(
        "--user-claims-file",
        type=Path,
        default=None,
        help=(
            "The claims file import_cmp_learners.py wrote, mapping a CMP "
            "account id to the account it created (default: that importer's "
            "own default path). Every row here resolves its learner through it."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure(args.database.resolve())

    from accounts.services.cmp_learner_import import (
        DEFAULT_CLAIMS_PATH as DEFAULT_USER_CLAIMS_PATH,
    )
    from accounts.services.cmp_learner_import import CmpClaimsStore, CmpLearnerImportError
    from courses.services.cmp_learner_history_import import (
        DEFAULT_BATCH_SIZE,
        DEFAULT_CLAIMS_DIRECTORY,
        CmpHistoryImportError,
        dry_run_counts,
        import_cmp_learner_history,
        progress_status,
    )

    claims_directory = (
        args.claims_dir.resolve()
        if args.claims_dir is not None
        else (PROJECT_ROOT / DEFAULT_CLAIMS_DIRECTORY).resolve()
    )
    user_claims_path = (
        args.user_claims_file.resolve()
        if args.user_claims_file is not None
        else (PROJECT_ROOT / DEFAULT_USER_CLAIMS_PATH).resolve()
    )
    try:
        if args.status:
            report = progress_status(claims_directory=claims_directory)
        elif args.dry_run:
            if args.source is None:
                _parser().error("--dry-run requires --source")
            report = dry_run_counts(args.source.resolve(), claims_directory=claims_directory)
        else:
            if args.source is None:
                _parser().error("--source is required unless --status is given")
            user_claims = {
                source_id: user_id
                for source_id, user_id in CmpClaimsStore.load(user_claims_path).sorted_claims()
            }
            result = import_cmp_learner_history(
                args.source.resolve(),
                user_claims=user_claims,
                batch_size=args.batch_size or DEFAULT_BATCH_SIZE,
                claims_directory=claims_directory,
                tables=args.table,
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
