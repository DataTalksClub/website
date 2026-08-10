from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from test_support.runtime import (
    OWNER_TOKEN_ENV,
    RUN_ID_ENV,
    TestRuntime,
    TestRuntimeSafetyError,
    stable_worktree_id,
)

REPOSITORY = Path(__file__).resolve().parents[2]


def _acquire(run_id: str) -> TestRuntime:
    environment = os.environ.copy()
    environment[RUN_ID_ENV] = run_id
    environment.pop(OWNER_TOKEN_ENV, None)
    with patch.dict(os.environ, environment, clear=True):
        return TestRuntime.acquire(REPOSITORY)


def test_runtime_module_import_is_inert_without_git_or_repository() -> None:
    scratch = REPOSITORY / ".tmp" / "runtime-import-regression" / f"run-{os.getpid()}"
    empty_path = scratch / "empty-path"
    working_directory = scratch / "outside-repository"
    empty_path.mkdir(parents=True)
    working_directory.mkdir()
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("DTC_TEST_"):
            environment.pop(name)
    environment.update(
        {
            "PATH": str(empty_path),
            "PYTHONPATH": os.fspath(REPOSITORY),
        }
    )
    command = (
        "import json; import test_support.runtime as runtime; "
        "print(json.dumps({'runtime_constructed': runtime._RUNTIME is not None}))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=working_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        shutil.rmtree(scratch)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"runtime_constructed": False}


def test_exact_worker_tree_and_database_are_project_local() -> None:
    runtime = _acquire("runtime-layout")
    try:
        first = runtime.worker("main")
        second = runtime.worker("django-1")
        assert first.database == (
            REPOSITORY
            / ".tmp"
            / "tests"
            / stable_worktree_id(REPOSITORY)
            / "runtime-layout"
            / "main"
            / "database"
            / "test.sqlite3"
        )
        assert first.database != second.database
        assert {path.name for path in first.root.iterdir()} == {
            "artifacts",
            "browser",
            "database",
            "server",
        }
    finally:
        runtime.cleanup()


def test_same_worktree_run_collision_refuses_without_mutating_owned_files() -> None:
    runtime = _acquire("runtime-collision")
    marker = runtime.worker().artifacts / "foreign-marker"
    marker.write_text("owned", encoding="utf-8")
    environment = os.environ.copy()
    environment[RUN_ID_ENV] = runtime.run_id
    environment.pop(OWNER_TOKEN_ENV, None)
    try:
        with patch.dict(os.environ, environment, clear=True):
            with pytest.raises(TestRuntimeSafetyError, match="already has an owner"):
                TestRuntime.acquire(REPOSITORY)
        assert marker.read_text(encoding="utf-8") == "owned"
    finally:
        runtime.cleanup()


def test_distinct_run_ids_and_child_owner_token_are_isolated() -> None:
    first = _acquire("runtime-first")
    second = _acquire("runtime-second")
    try:
        assert first.run_root != second.run_root
        with patch.dict(
            os.environ,
            {RUN_ID_ENV: first.run_id, OWNER_TOKEN_ENV: first._owner_token},
            clear=False,
        ):
            child = TestRuntime.acquire(REPOSITORY)
        assert not child.is_owner
        assert child.owner_pid == first.owner_pid
    finally:
        first.cleanup()
        second.cleanup()


def test_database_rejects_outside_paths_and_symlinks() -> None:
    runtime = _acquire("runtime-database-safety")
    try:
        layout = runtime.worker()
        with pytest.raises(TestRuntimeSafetyError, match="owned worker database"):
            runtime.assert_database_path(REPOSITORY / ".tmp" / "test.sqlite3")
        target = layout.root / "foreign.sqlite3"
        target.write_bytes(b"")
        layout.database.symlink_to(target)
        with pytest.raises(TestRuntimeSafetyError, match="symlink"):
            runtime.assert_database_path(layout.database)
    finally:
        runtime.cleanup()


def test_cleanup_refuses_foreign_owner_and_preserves_neighboring_run() -> None:
    first = _acquire("runtime-cleanup-first")
    second = _acquire("runtime-cleanup-second")
    owner_path = first.run_root / "owner.json"
    original = owner_path.read_text(encoding="utf-8")
    owner = json.loads(original)
    owner["pid"] += 1
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    neighbor = second.worker().artifacts / "neighbor"
    neighbor.write_text("preserve", encoding="utf-8")
    try:
        with pytest.raises(TestRuntimeSafetyError, match="ownership boundary"):
            first.cleanup()
        assert neighbor.read_text(encoding="utf-8") == "preserve"
    finally:
        owner_path.write_text(original, encoding="utf-8")
        first._cleaned = False
        first.cleanup()
        second.cleanup()


def test_loopback_port_reservations_are_kernel_allocated_and_disjoint() -> None:
    runtime = _acquire("runtime-ports")
    first = runtime.reserve_loopback_port()
    second = runtime.reserve_loopback_port()
    try:
        assert first.getsockname()[0] == "127.0.0.1"
        assert second.getsockname()[0] == "127.0.0.1"
        assert first.getsockname()[1] != second.getsockname()[1]
    finally:
        first.close()
        second.close()
        runtime.cleanup()


def test_sigterm_cleans_only_the_exact_owned_sqlite_wal_runtime() -> None:
    neighbor = _acquire(f"runtime-signal-neighbor-{os.getpid()}")
    marker = neighbor.worker().artifacts / "neighbor"
    marker.write_text("preserve", encoding="utf-8")
    environment = os.environ.copy()
    environment[RUN_ID_ENV] = f"runtime-signal-child-{os.getpid()}"
    environment.pop(OWNER_TOKEN_ENV, None)
    process = subprocess.Popen(
        [sys.executable, "-m", "test_support.runtime_signal_child"],
        cwd=REPOSITORY,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    run_root = Path(json.loads(process.stdout.readline())["run_root"])
    try:
        assert run_root.is_relative_to(REPOSITORY / ".tmp" / "tests")
        assert (run_root / "main" / "database" / "test.sqlite3").is_file()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == -signal.SIGTERM
        assert not run_root.exists()
        assert marker.read_text(encoding="utf-8") == "preserve"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        neighbor.cleanup()
