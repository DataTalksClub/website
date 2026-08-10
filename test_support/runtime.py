from __future__ import annotations

import atexit
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_ID_ENV = "DTC_TEST_RUN_ID"
OWNER_TOKEN_ENV = "DTC_TEST_OWNER_TOKEN"
WORKER_ENVIRONMENTS = ("PYTEST_XDIST_WORKER", "DJANGO_TEST_WORKER")
DEFAULT_FROZEN_AT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class TestRuntimeSafetyError(RuntimeError):
    """The requested test execution boundary is ambiguous or unsafe."""

    __test__ = False


def _validated_identifier(value: str, *, label: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise TestRuntimeSafetyError(f"{label} must be a bounded execution identifier")
    return value


def new_run_id() -> str:
    """Return an opaque execution identity; it never contributes to logical fixture data."""

    return f"run-{os.getpid()}-{secrets.token_hex(6)}"


def current_worker_id() -> str:
    for name in WORKER_ENVIRONMENTS:
        value = os.environ.get(name)
        if value:
            return _validated_identifier(value, label="worker id")
    return "main"


def _git_common_directory(repository: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", os.fspath(repository), "rev-parse", "--git-common-dir"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = Path(result.stdout.strip())
    if not value.is_absolute():
        value = repository / value
    return value.resolve(strict=True)


def stable_worktree_id(repository: Path) -> str:
    worktree = repository.resolve(strict=True)
    common = _git_common_directory(worktree)
    digest = hashlib.sha256(os.fsencode(common) + b"\0" + os.fsencode(worktree)).hexdigest()
    return digest[:20]


def _assert_no_symlink_components(path: Path, *, stop: Path) -> None:
    candidate = path.absolute()
    boundary = stop.resolve(strict=True)
    if not candidate.is_relative_to(boundary):
        raise TestRuntimeSafetyError("test path must stay below the current worktree")
    relative = candidate.relative_to(boundary)
    current = boundary
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise TestRuntimeSafetyError("test paths cannot contain symlinks")


@dataclass(frozen=True, slots=True)
class WorkerLayout:
    worker_id: str
    root: Path
    database: Path
    browser: Path
    server: Path
    artifacts: Path


class TestRuntime:
    """Own one worktree/run lock and its exact per-worker resource tree."""

    __test__ = False

    def __init__(
        self,
        *,
        repository: Path,
        worktree_id: str,
        run_id: str,
        run_root: Path,
        owner_pid: int,
        owner_token: str,
        lock_descriptor: int | None,
        is_owner: bool,
    ) -> None:
        self.repository = repository
        self.worktree_id = worktree_id
        self.run_id = run_id
        self.run_root = run_root
        self.owner_pid = owner_pid
        self._owner_token = owner_token
        self._lock_descriptor = lock_descriptor
        self.is_owner = is_owner
        self._cleaned = False

    @classmethod
    def acquire(cls, repository: Path) -> TestRuntime:
        repository = repository.resolve(strict=True)
        worktree_id = stable_worktree_id(repository)
        run_id = _validated_identifier(
            os.environ.get(RUN_ID_ENV) or new_run_id(),
            label="run id",
        )
        os.environ[RUN_ID_ENV] = run_id
        tests_root = repository / ".tmp" / "tests"
        run_root = tests_root / worktree_id / run_id
        _assert_no_symlink_components(run_root, stop=repository)
        run_root.mkdir(parents=True, exist_ok=True)

        owner_path = run_root / "owner.json"
        inherited_token = os.environ.get(OWNER_TOKEN_ENV)
        if inherited_token and owner_path.is_file() and not owner_path.is_symlink():
            owner = _read_owner(owner_path)
            expected = hashlib.sha256(inherited_token.encode("utf-8")).hexdigest()
            if owner.get("token_sha256") == expected:
                return cls(
                    repository=repository,
                    worktree_id=worktree_id,
                    run_id=run_id,
                    run_root=run_root,
                    owner_pid=int(owner["pid"]),
                    owner_token=inherited_token,
                    lock_descriptor=None,
                    is_owner=False,
                )

        lock_path = run_root / "owner.lock"
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise TestRuntimeSafetyError(
                f"test run {worktree_id}/{run_id} already has an owner"
            ) from error

        if owner_path.exists():
            os.close(descriptor)
            raise TestRuntimeSafetyError(
                f"test run {worktree_id}/{run_id} has stale owner state; choose a new run id"
            )

        owner_token = secrets.token_urlsafe(32)
        owner = {
            "pid": os.getpid(),
            "run_id": run_id,
            "schema_version": 1,
            "token_sha256": hashlib.sha256(owner_token.encode("utf-8")).hexdigest(),
            "worktree_id": worktree_id,
        }
        _write_exclusive_json(owner_path, owner)
        os.environ[OWNER_TOKEN_ENV] = owner_token
        runtime = cls(
            repository=repository,
            worktree_id=worktree_id,
            run_id=run_id,
            run_root=run_root,
            owner_pid=os.getpid(),
            owner_token=owner_token,
            lock_descriptor=descriptor,
            is_owner=True,
        )
        atexit.register(runtime.cleanup)
        _register_termination_cleanup(runtime)
        return runtime

    def worker(self, worker_id: str | None = None) -> WorkerLayout:
        identifier = _validated_identifier(worker_id or current_worker_id(), label="worker id")
        root = self.run_root / identifier
        _assert_no_symlink_components(root, stop=self.repository)
        layout = WorkerLayout(
            worker_id=identifier,
            root=root,
            database=root / "database" / "test.sqlite3",
            browser=root / "browser",
            server=root / "server",
            artifacts=root / "artifacts",
        )
        for directory in (
            layout.database.parent,
            layout.browser,
            layout.server,
            layout.artifacts,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return layout

    def assert_database_path(self, path: Path, *, worker_id: str | None = None) -> Path:
        layout = self.worker(worker_id)
        candidate = path.absolute()
        _assert_no_symlink_components(candidate, stop=self.repository)
        if candidate != layout.database or not candidate.is_relative_to(layout.root):
            raise TestRuntimeSafetyError("SQLite test database must be the owned worker database")
        if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
            raise TestRuntimeSafetyError("SQLite test database must be a regular non-symlink file")
        return candidate

    def record_server(self, *, origin: str, worker_id: str | None = None) -> Path:
        layout = self.worker(worker_id)
        record = layout.server / "owner.json"
        payload = {
            "origin": origin,
            "owner_pid": self.owner_pid,
            "run_id": self.run_id,
            "schema_version": 1,
            "worker_id": layout.worker_id,
            "worktree_id": self.worktree_id,
        }
        if record.exists():
            if _read_owner(record) != payload:
                raise TestRuntimeSafetyError("server state belongs to a different owner")
            return record
        _write_exclusive_json(record, payload)
        return record

    def reserve_loopback_port(self) -> socket.socket:
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        reservation.bind(("127.0.0.1", 0))
        reservation.listen(1)
        return reservation

    def cleanup(self) -> None:
        if self._cleaned or not self.is_owner or os.getpid() != self.owner_pid:
            return
        self._cleaned = True
        try:
            self._assert_cleanup_target()
            shutil.rmtree(self.run_root)
            parent = self.run_root.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        finally:
            if self._lock_descriptor is not None:
                os.close(self._lock_descriptor)
                self._lock_descriptor = None
            if os.environ.get(OWNER_TOKEN_ENV) == self._owner_token:
                os.environ.pop(OWNER_TOKEN_ENV, None)
            _unregister_termination_cleanup(self)

    def _assert_cleanup_target(self) -> None:
        expected = self.repository / ".tmp" / "tests" / self.worktree_id / self.run_id
        if self.run_root != expected or not self.run_root.is_relative_to(self.repository):
            raise TestRuntimeSafetyError("refusing cleanup outside the exact owned run root")
        owner_path = self.run_root / "owner.json"
        if owner_path.is_symlink() or not owner_path.is_file():
            raise TestRuntimeSafetyError("refusing cleanup without an exact owner record")
        owner = _read_owner(owner_path)
        expected_digest = hashlib.sha256(self._owner_token.encode("utf-8")).hexdigest()
        if (
            owner.get("pid") != self.owner_pid
            or owner.get("token_sha256") != expected_digest
            or owner.get("run_id") != self.run_id
            or owner.get("worktree_id") != self.worktree_id
        ):
            raise TestRuntimeSafetyError("refusing cleanup across an ownership boundary")

    def public_metadata(self) -> dict[str, Any]:
        return {
            "frozen_at": DEFAULT_FROZEN_AT.isoformat().replace("+00:00", "Z"),
            "run_id": self.run_id,
            "worktree_id": self.worktree_id,
        }


def _read_owner(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise TestRuntimeSafetyError("test owner state is malformed") from error
    if not isinstance(payload, dict):
        raise TestRuntimeSafetyError("test owner state is malformed")
    return payload


def _write_exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        content = json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n"
        os.write(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


_RUNTIME: TestRuntime | None = None
_TERMINATION_RUNTIMES: dict[str, TestRuntime] = {}
_PREVIOUS_SIGTERM_HANDLER: Any = None


def _register_termination_cleanup(runtime: TestRuntime) -> None:
    """Bind SIGTERM cleanup to exact owner-token-attributed runtimes in this process."""

    global _PREVIOUS_SIGTERM_HANDLER
    if not _TERMINATION_RUNTIMES:
        _PREVIOUS_SIGTERM_HANDLER = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _cleanup_on_sigterm)
    _TERMINATION_RUNTIMES[runtime._owner_token] = runtime


def _unregister_termination_cleanup(runtime: TestRuntime) -> None:
    global _PREVIOUS_SIGTERM_HANDLER
    registered = _TERMINATION_RUNTIMES.get(runtime._owner_token)
    if registered is runtime:
        _TERMINATION_RUNTIMES.pop(runtime._owner_token, None)
    if not _TERMINATION_RUNTIMES and signal.getsignal(signal.SIGTERM) is _cleanup_on_sigterm:
        signal.signal(signal.SIGTERM, _PREVIOUS_SIGTERM_HANDLER)
        _PREVIOUS_SIGTERM_HANDLER = None


def _cleanup_on_sigterm(signum: int, frame: Any) -> None:
    previous = _PREVIOUS_SIGTERM_HANDLER
    try:
        try:
            from django.conf import settings
            from django.db import connections

            if settings.configured:
                connections.close_all()
        except (ImportError, RuntimeError):
            pass
        for runtime in tuple(reversed(tuple(_TERMINATION_RUNTIMES.values()))):
            runtime.cleanup()
    finally:
        if callable(previous):
            previous(signum, frame)
        elif previous == signal.SIG_IGN:
            return
        else:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)


def get_test_runtime(repository: Path) -> TestRuntime:
    global _RUNTIME
    resolved = repository.resolve(strict=True)
    if _RUNTIME is None:
        _RUNTIME = TestRuntime.acquire(resolved)
    elif _RUNTIME.repository != resolved:
        raise TestRuntimeSafetyError("one process cannot own test roots for multiple worktrees")
    return _RUNTIME


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["new-run-id"]:
        raise SystemExit("usage: python -m test_support.runtime new-run-id")
    print(new_run_id())
