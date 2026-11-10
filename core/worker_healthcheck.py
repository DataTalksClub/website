"""Stdlib-only heartbeat check for the persistent domain-job worker."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HEARTBEAT_FILE = Path(os.environ.get("JOBS_WORKER_HEARTBEAT_PATH", "/tmp/jobs-worker.heartbeat"))
HEARTBEAT_MAX_AGE_SECONDS = 90


def touch_worker_heartbeat() -> None:
    """Update the worker heartbeat; file errors fail the worker loudly."""

    HEARTBEAT_FILE.touch()


def heartbeat_is_fresh(
    *,
    now: float | None = None,
    max_age_seconds: int = HEARTBEAT_MAX_AGE_SECONDS,
) -> bool:
    """Return whether the worker updated its heartbeat within the allowed age."""

    try:
        age_seconds = (time.time() if now is None else now) - HEARTBEAT_FILE.stat().st_mtime
    except OSError:
        return False
    return age_seconds <= max_age_seconds


def main() -> int:
    if heartbeat_is_fresh():
        return 0
    print("jobs_worker heartbeat missing or stale", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
