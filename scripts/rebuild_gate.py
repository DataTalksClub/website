#!/usr/bin/env python3
"""Checked removal of a dataset database before a rebuild (audit REL-09).

``production-prep-dataset`` and ``production-prep-bootstrap`` used to run a
bare ``rm -f`` over the caller-overridable database path and its ``-wal``/
``-shm`` sidecars before any check ran -- the Python confinement check in
``scripts/prepare_local_data.py`` was reached only after the deletion.  An
out-of-``.tmp`` override, a symlinked path, or a live server's WAL files could
be destroyed before anything refused the run.

This script is the one removal boundary for both rebuild recipes.  For the
resolved target it demands, in order, before touching anything:

* the resolved real path stays inside the project's ``.tmp/`` scratch root
  (a symlinked directory component pointing outside is refused here, not
  after the delete);
* a ``.sqlite3`` suffix on the final component;
* no symlink at the final component -- a later ``migrate`` would write
  through it into whatever file it names;
* an exclusive rebuild lock (``flock``, released when this process exits, so
  a crashed rebuild cannot wedge the next one) -- two concurrent rebuilds of
  one dataset would otherwise interleave their stage writes.

Only then are the database and its sidecar files removed.  A missing database
is a successful no-op: rebuilding from nothing is the normal first run.  Every
refusal is a bounded condition code -- never a reflected path.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = PROJECT_ROOT / ".tmp"
LOCK_PATH = SCRATCH_ROOT / "dataset-rebuild.lock"


class RebuildRefused(RuntimeError):
    """A fail-closed refusal naming the unmet condition, before any mutation."""


def _refuse(code: str) -> None:
    raise RebuildRefused(code)


def remove_previous_database(raw_path: str, *, scratch_root: Path = SCRATCH_ROOT) -> bool:
    """Validate the resolved target, then remove it and its SQLite sidecars.

    Returns ``True`` when files were removed, ``False`` when there was nothing
    to remove (the normal first-run case).  The exclusive lock is held only
    for the check-and-remove span, so a concurrent rebuild of the same dataset
    is refused instead of interleaved.
    """

    if not raw_path.strip():
        _refuse("empty-path")
    raw = Path(raw_path).expanduser()
    resolved = raw.resolve()
    try:
        resolved.relative_to(scratch_root)
    except ValueError:
        _refuse("outside-scratch-root")
    if resolved.suffix != ".sqlite3":
        _refuse("not-sqlite-suffix")
    # A symlink at the final component is refused even though the resolved
    # path is inside the scratch root: deleting the link is harmless, but the
    # rebuild that follows would write through it into whatever file it names.
    # resolve() follows the link, so the raw path's final component is what
    # carries the answer.
    if raw.is_symlink():
        _refuse("symlink-target")

    lock_path = scratch_root / "dataset-rebuild.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _refuse("locked-by-concurrent-rebuild")

        # Re-check the symlink policy under the lock: a concurrent process
        # could have swapped the component in between the check and the lock.
        if resolved.is_symlink():
            _refuse("symlink-target")

        sidecars = (resolved, Path(f"{resolved}-shm"), Path(f"{resolved}-wal"))
        if not any(side.exists() for side in sidecars):
            return False
        for side in sidecars:
            if side.is_symlink():
                _refuse("symlink-target")
            if side.exists():
                side.unlink()
        return True
    finally:
        os.close(lock_descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="Dataset database path to clear before a rebuild")
    arguments = parser.parse_args(argv)
    try:
        removed = remove_previous_database(arguments.database)
    except RebuildRefused as refusal:
        # The code is a bounded condition; the path is never echoed back.
        print(json_error(str(refusal)), file=sys.stderr)
        return 1
    print("removed" if removed else "absent")
    return 0


def json_error(code: str) -> str:
    import json

    return json.dumps({"error": code}, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
