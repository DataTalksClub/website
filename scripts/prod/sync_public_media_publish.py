#!/usr/bin/env python3
"""Upload the recorded public projection media objects to the configured object store.

Git-synchronized -- see ``scripts/prod/__init__.py``. Touches no database: every
record and every byte lives in files or the configured object store.

    PUBLIC_MEDIA_STORE_BACKEND=s3 PUBLIC_MEDIA_S3_BUCKET=<bucket> \\
        uv run --frozen python scripts/prod/sync_public_media_publish.py

Only objects that have a ``media.json`` record are uploaded, each with the recorded
content type and a SHA-256 checksum.  An object already present with a matching checksum
is skipped.  A file in the source root with no record is reported as an orphan and
deliberately left unpublished, so it keeps returning 404.
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

from scripts.prod.target import configure_ambient_settings  # noqa: E402

SYNC_MODEL = "git-synchronized"
BOOTSTRAPS_EMPTY_DATABASE = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be uploaded without writing to the store",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_ambient_settings()

    from content.media_store import S3MediaStore, media_store, media_store_config
    from content.media_tooling import MediaToolingError, publish_media

    config = media_store_config()
    store = media_store()
    if not isinstance(store, S3MediaStore):
        print(
            json.dumps(
                {
                    "error": (
                        "publishing requires PUBLIC_MEDIA_STORE_BACKEND=s3 and a configured bucket"
                    )
                },
                indent=2,
            )
        )
        return 1
    try:
        report = publish_media(
            source_root=args.source_root or config.local_root,
            store=store,
            maximum_object_bytes=config.maximum_object_bytes,
            dry_run=args.dry_run,
        )
    except MediaToolingError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
