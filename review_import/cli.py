"""Command-line interface for the safe local review-data workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from review_import.workflow import (
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_TARGET,
    ImportConfig,
    ImportFailure,
    ReviewImporter,
    cleanup_snapshot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a content-only local CMP review database from one named SQLite snapshot."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Validate and build a local review database.")
    build.add_argument("--source-db", type=Path, required=True)
    build.add_argument("--snapshot-id", required=True)
    build.add_argument("--target-db", type=Path, default=DEFAULT_TARGET)
    build.add_argument("--dry-run", action="store_true")
    build.add_argument(
        "--no-admin",
        action="store_true",
        help="Do not add the synthetic review administrator to an applied target.",
    )

    cleanup = commands.add_parser("cleanup", help="Remove one exact derived snapshot/report.")
    cleanup.add_argument("--snapshot-id", required=True)
    cleanup.add_argument("--target-db", type=Path, default=DEFAULT_TARGET)
    cleanup.add_argument(
        "--include-target",
        action="store_true",
        help="Also remove the explicitly named ignored local review database.",
    )
    return parser


def _build(args: argparse.Namespace) -> dict[str, object]:
    password = os.getenv("REVIEW_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
    config = ImportConfig(
        source_db=args.source_db,
        snapshot_id=args.snapshot_id,
        target_db=args.target_db,
        dry_run=args.dry_run,
        create_admin=not args.no_admin,
        admin_password=password,
    )
    return ReviewImporter().run(config)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            result: dict[str, Any] = _build(args)
        else:
            result = cleanup_snapshot(
                args.snapshot_id,
                include_target=args.include_target,
                target_db=args.target_db,
            )
    except ImportFailure as exc:
        snapshot_id = getattr(args, "snapshot_id", "-")
        print(f"snapshot_id={snapshot_id} {exc}", file=sys.stderr)
        return 2
    except Exception:
        snapshot_id = getattr(args, "snapshot_id", "-")
        print(
            f"snapshot_id={snapshot_id} review import failed: category=internal",
            file=sys.stderr,
        )
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
