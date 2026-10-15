#!/usr/bin/env python3
"""Compare the configured public media store against the synced media records.

Git-synchronized -- see ``scripts/prod/__init__.py``.

Reads its records from the database the target names: the published
``SyncedDocument`` media rows ``content.catalogue.media()`` serves (written by
``manage.py sync_content``).  ``--database`` names a SQLite file below the
project-local ``.tmp/``; ``--deployment-target NAME`` needs the shared
``--allow-production-write NAME`` opt-in.  A database with no synced media rows is
refused with exit code 2 and ``{"error": "no_synced_media_records"}``.

    uv run --frozen python scripts/prod/sync_public_media_verify.py \\
        --database .tmp/local.sqlite3

Exits non-zero when any recorded object is missing, unreadable, or checksum-mismatched,
or when the store holds an object that has no record.
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

SYNC_MODEL = "git-synchronized"
BOOTSTRAPS_EMPTY_DATABASE = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_target(parser, args, scratch_database_only=True)

    from content.media_store import media_store
    from content.media_tooling import MediaToolingError, NoSyncedMediaRecords, verify_media

    try:
        report = verify_media(store=media_store())
    except NoSyncedMediaRecords as refusal:
        print(json.dumps({"error": refusal.code}, indent=2))
        return 2
    except MediaToolingError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
