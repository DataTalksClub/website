#!/usr/bin/env python3
"""Merge reviewed duplicate Event pairs: dry run by default, ``--apply`` to write.

One-time.  A person names every pair as ``KEEP_ID:DUPLICATE_ID`` -- the two
events' database row ids, as a read-only duplicate audit reports them.  Nothing
here detects duplicates.  The dry run prints, per pair, both records side by
side (title, start, description presence, hosts, registration and Q&A counts)
and exactly what a merge would move, drop, retire and redirect; ``--apply``
then performs each pair in its own transaction through ``events.merge`` and
appends one ``core.AuditEvent`` per merged pair.

Replaying an applied pair reports ``already_merged`` and writes nothing.  A
pair whose start times are more than ``--max-days-apart`` days apart is refused
unless ``--allow-date-mismatch`` is given; the kept event's start time is left
alone unless ``--start-from-duplicate`` is given.

Rehearse against a copy:

    uv run --frozen python scripts/prod/import_event_duplicate_merges.py \\
        --database .tmp/dataset-copy.sqlite3 --pair 150:422 --pair 86:426

A reviewed set of pairs can live in a file outside the repository instead
(``--pairs-file``, see :func:`pairs_from_file`); the row ids are then checked
against each pair's content UUIDs before anything is merged.

Production, after the pairs are approved:

    uv run --frozen python scripts/prod/import_event_duplicate_merges.py \\
        --deployment-target website-production \\
        --allow-production-write website-production \\
        --pair 150:422 --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False


def _pair(value: str) -> Any:
    from events.merge import MergePair

    try:
        return MergePair.parse(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def pairs_from_file(path: Path) -> list[str]:
    """``KEEP_ID:DUPLICATE_ID`` values from a reviewed pairs file.

    The file is ``{"schema_version": 1, "pairs": [{"keep_id": 150,
    "duplicate_id": 422, "keep_content_id": "<uuid>", "duplicate_content_id":
    "<uuid>"}, ...]}``.  Row ids are what the merge takes; the content UUIDs
    guard them -- a row that exists under that id must carry that UUID, or the
    run refuses before anything is merged.  A duplicate row that no longer
    exists is passed through, so a replay reports ``already_merged``.
    """

    from community_base.events.models import Event

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"pairs_file_unreadable: {error}") from error
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("pairs_file_schema_version_unsupported")
    values: list[str] = []
    for index, pair in enumerate(data.get("pairs") or []):
        try:
            keep_id, duplicate_id = int(pair["keep_id"]), int(pair["duplicate_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"pairs_file_entry_invalid: {index}") from error
        for row_id, key in ((keep_id, "keep_content_id"), (duplicate_id, "duplicate_content_id")):
            expected = str(pair.get(key) or "").replace("-", "").lower()
            row = Event.objects.filter(pk=row_id).values_list("content_id", flat=True).first()
            if row is not None and expected and row.hex != expected:
                raise ValueError(f"pairs_file_content_id_mismatch: {index}: row {row_id}")
            if row is None and key == "keep_content_id":
                raise ValueError(f"pairs_file_keep_missing: {index}: row {row_id}")
        values.append(f"{keep_id}:{duplicate_id}")
    if not values:
        raise ValueError("pairs_file_empty")
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_target_arguments(parser)
    parser.add_argument(
        "--pair",
        dest="pairs",
        action="append",
        default=[],
        metavar="KEEP_ID:DUPLICATE_ID",
        help="one reviewed pair of event row ids; repeat for several pairs",
    )
    parser.add_argument(
        "--pairs-file",
        type=Path,
        default=None,
        help=(
            "a reviewed JSON file of pairs (outside the repository, e.g. under "
            "~/prod/dtc-data/); each pair's content UUIDs are checked against the rows"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the merges; without it the run only reports",
    )
    parser.add_argument(
        "--max-days-apart",
        type=int,
        default=1,
        help="refuse a pair whose start times differ by more than this many days (default 1)",
    )
    parser.add_argument(
        "--allow-date-mismatch",
        action="store_true",
        help="merge a pair even when its start times are more than --max-days-apart apart",
    )
    parser.add_argument(
        "--start-from-duplicate",
        action="store_true",
        help=(
            "set the kept event's start (and shift its end) to the duplicate's start; "
            "by default the kept event's time is never changed"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="also write the JSON report to this path (keep it under .tmp/)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.max_days_apart < 0:
        parser.error("--max-days-apart must not be negative")
    if not args.pairs and args.pairs_file is None:
        parser.error("name at least one pair: --pair KEEP_ID:DUPLICATE_ID or --pairs-file PATH")
    configure_target(parser, args)

    from events.merge import EventMergeRunAborted, merge_pairs

    # Django is configured only now, so the pair type is parsed late as well.
    try:
        values = list(args.pairs)
        if args.pairs_file is not None:
            values.extend(pairs_from_file(args.pairs_file.expanduser()))
        pairs = [_pair(value) for value in values]
        report = merge_pairs(
            pairs,
            apply=args.apply,
            allow_date_mismatch=args.allow_date_mismatch,
            max_days_apart=args.max_days_apart,
            start_from_duplicate=args.start_from_duplicate,
        )
    except EventMergeRunAborted as aborted:
        # Every pair before the failing one is committed; say exactly which.
        print(
            json.dumps(
                {
                    "error": "merge_run_aborted",
                    "failed_pair": aborted.failed_pair,
                    "failure": aborted.error,
                    "applied": args.apply,
                    "completed_pairs": aborted.completed,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    except (argparse.ArgumentTypeError, ValueError) as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if report["outcomes"].get("refused") else 0


if __name__ == "__main__":
    raise SystemExit(main())
