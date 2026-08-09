#!/usr/bin/env python3
"""Run the target-owned, content-only local review import."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run() -> int:
    from review_import.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(run())
