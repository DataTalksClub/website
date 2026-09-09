"""The rebuild removal gate never destroys an unvalidated target (audit REL-09).

The rebuild recipes used to ``rm -f`` the caller-overridable database and its
WAL/SHM sidecars before any check ran.  ``scripts/rebuild_gate.py`` now demands
scratch-root containment, a ``.sqlite3`` suffix, no symlink components, and an
exclusive rebuild lock before removing anything.  These tests use real files
under the project ``.tmp/`` only, with sentinel files proving an escape would
have been destructive and is not.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest

from scripts.rebuild_gate import RebuildRefused, remove_previous_database

SCRATCH = Path(".tmp")


@pytest.fixture()
def scratch_dir() -> Generator[Path]:
    """A real directory under the project .tmp/, as the gate requires."""
    scratch_root = (SCRATCH / "rebuild-gate-tests").resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    directory = scratch_root / f"gate-{os.getpid()}-{id(scratch_root) % 100000}"
    directory.mkdir(parents=True, exist_ok=True)
    yield directory
    for side in directory.iterdir():
        side.unlink()
    directory.rmdir()


def _sidecars(database: Path) -> tuple[Path, Path, Path]:
    return (database, Path(f"{database}-shm"), Path(f"{database}-wal"))


def _touch(path: Path, content: str = "sentinel\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_removes_the_database_and_both_sidecars(scratch_dir: Path) -> None:
    database = scratch_dir / "dataset.sqlite3"
    for side in _sidecars(database):
        _touch(side)

    removed = remove_previous_database(str(database))

    assert removed is True
    assert not any(side.exists() for side in _sidecars(database))


def test_a_missing_database_is_a_successful_noop(scratch_dir: Path) -> None:
    assert remove_previous_database(str(scratch_dir / "dataset.sqlite3")) is False


def test_a_path_outside_the_scratch_root_is_refused_before_any_mutation(
    tmp_path: Path,
) -> None:
    # A genuinely outside path: pytest's tmp_path sits inside the project
    # .tmp/, so a fixed /tmp location is what the containment check refuses.
    sentinel = Path(f"/tmp/dtc-rel09-outside-{os.getpid()}.sqlite3")
    try:
        _touch(sentinel)

        with pytest.raises(RebuildRefused, match="outside-scratch-root"):
            remove_previous_database(str(sentinel))

        # The refusal preceded the delete: the outside file is intact.
        assert sentinel.read_text() == "sentinel\n"
    finally:
        if sentinel.exists():
            sentinel.unlink()


def test_a_non_sqlite_suffix_is_refused(scratch_dir: Path) -> None:
    other = scratch_dir / "dataset.db"
    _touch(other)

    with pytest.raises(RebuildRefused, match="not-sqlite-suffix"):
        remove_previous_database(str(other))

    assert other.read_text() == "sentinel\n"


def test_a_symlink_final_component_is_refused_and_its_target_survives(
    scratch_dir: Path,
) -> None:
    # The link target is inside the scratch root on purpose: containment and
    # suffix both pass, so the final-component symlink policy is the only
    # thing standing between the rebuild and a write-through link.
    sentinel = scratch_dir / "real-database.sqlite3"
    _touch(sentinel)
    link = scratch_dir / "dataset.sqlite3"
    link.symlink_to(sentinel)

    with pytest.raises(RebuildRefused, match="symlink-target"):
        remove_previous_database(str(link))

    assert sentinel.read_text() == "sentinel\n"
    assert link.is_symlink()


def test_a_symlinked_directory_component_is_refused(scratch_dir: Path, tmp_path: Path) -> None:
    # A directory under .tmp/ that links to a genuinely outside directory:
    # the resolved path lands outside the scratch root, so the containment
    # check must refuse before anything is deleted.  (pytest's own tmp_path
    # is inside the project .tmp/, so an "outside" target has to be a fixed
    # /tmp path with a unique name.)
    outside = Path(f"/tmp/dtc-rel09-outside-{os.getpid()}")
    sentinel = outside / "dataset.sqlite3"
    link = scratch_dir / "linked-dir"
    try:
        if link.exists() or link.is_symlink():
            link.unlink()
        outside.mkdir(parents=True, exist_ok=True)
        _touch(sentinel)
        link.symlink_to(outside, target_is_directory=True)

        with pytest.raises(RebuildRefused, match="outside-scratch-root"):
            remove_previous_database(str(link / "dataset.sqlite3"))

        assert sentinel.read_text() == "sentinel\n"
    finally:
        if link.is_symlink():
            link.unlink()
        if sentinel.exists():
            sentinel.unlink()
        if outside.is_dir():
            outside.rmdir()


def test_a_concurrent_rebuild_is_refused_while_the_lock_is_held(
    scratch_dir: Path,
) -> None:
    import fcntl

    database = scratch_dir / "dataset.sqlite3"
    _touch(database)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(SCRATCH / "dataset-rebuild.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with pytest.raises(RebuildRefused, match="locked-by-concurrent-rebuild"):
            remove_previous_database(str(database))

        # The refused run deleted nothing.
        assert database.read_text() == "sentinel\n"
    finally:
        os.close(lock_descriptor)


def test_an_empty_path_is_refused() -> None:
    with pytest.raises(RebuildRefused, match="empty-path"):
        remove_previous_database("   ")
