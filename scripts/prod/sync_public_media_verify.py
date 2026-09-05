#!/usr/bin/env python3
"""Compare the configured public media store against ``media.json``.

Git-synchronized -- see ``scripts/prod/__init__.py``. Touches no database.

    uv run --frozen python scripts/prod/sync_public_media_verify.py

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

from scripts.prod.target import configure_ambient_settings  # noqa: E402

SYNC_MODEL = "git-synchronized"
BOOTSTRAPS_EMPTY_DATABASE = False


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description=__doc__)


def main(argv: list[str] | None = None) -> int:
    _parser().parse_args(argv)
    configure_ambient_settings()

    from content.media_store import media_store
    from content.media_tooling import MediaToolingError, verify_media

    try:
        report = verify_media(store=media_store())
    except MediaToolingError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
