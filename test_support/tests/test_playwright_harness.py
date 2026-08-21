from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

import conftest as harness
from test_support.runtime import OWNER_TOKEN_ENV, RUN_ID_ENV

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _FakeConnection:
    def __init__(self) -> None:
        self.shared = 0

    def inc_thread_sharing(self) -> None:
        self.shared += 1

    def dec_thread_sharing(self) -> None:
        self.shared -= 1


class _FakeReady:
    def __init__(self, *, ready: bool) -> None:
        self.ready = ready
        self.timeout: float | None = None

    def wait(self, timeout: float | None = None) -> bool:
        self.timeout = timeout
        return self.ready

    def is_set(self) -> bool:
        return self.ready


class _FakeThread:
    def __init__(self, *, ready: bool, alive: bool) -> None:
        self.connections_override: dict[str, _FakeConnection] = {}
        self.is_ready = _FakeReady(ready=ready)
        self.error = None
        self.ident = 206
        self.port = 43210
        self._alive = alive

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self._alive = False


class _FakeServer:
    def __init__(self, thread: _FakeThread) -> None:
        self.thread = thread


def test_live_server_readiness_diagnostic_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _FakeConnection()
    thread = _FakeThread(ready=False, alive=True)
    thread.connections_override = {"default": connection}
    server = _FakeServer(thread)
    monkeypatch.setattr(harness, "_LIVE_SERVER_READY_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(RuntimeError, match="live-server readiness timed out"):
        harness._start_live_server(server)  # type: ignore[arg-type]

    assert thread.is_ready.timeout == 0.01
    assert connection.shared == 0


def test_live_server_shutdown_diagnostic_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _FakeConnection()
    connection.shared = 1
    thread = _FakeThread(ready=True, alive=True)
    thread.connections_override = {"default": connection}
    thread.terminate = lambda: None  # type: ignore[method-assign]
    server = _FakeServer(thread)
    monkeypatch.setattr(harness, "_LIVE_SERVER_SHUTDOWN_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(RuntimeError, match="live-server shutdown timed out"):
        harness._stop_live_server(server, connections=(connection,))  # type: ignore[arg-type]

    assert connection.shared == 1


_CHILD_CONFTEST = r"""
import json
import os
import threading
from pathlib import Path

from django.test.testcases import LiveServerThread


def pytest_sessionfinish(session, exitstatus):
    del session
    active = [
        thread.name
        for thread in threading.enumerate()
        if isinstance(thread, LiveServerThread)
    ]
    Path(os.environ["DTC_HARNESS_THREAD_REPORT"]).write_text(
        json.dumps({"exitstatus": int(exitstatus), "live_server_threads": active}) + "\n",
        encoding="utf-8",
    )
"""

_CHILD_TEST = r"""
import pytest
from playwright.sync_api import Page

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]


def test_sequential_db_backed_navigation_includes_events(page: Page, live_server) -> None:
    for path in ("/", "/events"):
        response = page.goto(f"{live_server.url}{path}", wait_until="domcontentloaded")
        assert response is not None and response.status == 200


def test_failing_browser_test_still_cleans_its_context(page: Page, live_server) -> None:
    response = page.goto(f"{live_server.url}/events", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert False, "intentional lifecycle regression failure"
"""


def test_live_server_child_session_has_ordered_cleanup_and_terminal_summary() -> None:
    temporary_root = REPOSITORY_ROOT / ".tmp"
    temporary_root.mkdir(exist_ok=True)
    harness_directory = Path(
        tempfile.mkdtemp(prefix="issue206-live-server-", dir=os.fspath(temporary_root))
    )
    try:
        (harness_directory / "conftest.py").write_text(_CHILD_CONFTEST, encoding="utf-8")
        test_path = harness_directory / "test_lifecycle.py"
        test_path.write_text(_CHILD_TEST, encoding="utf-8")
        thread_report = harness_directory / "thread-report.json"

        environment = os.environ.copy()
        environment[RUN_ID_ENV] = f"issue206-child-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        environment.pop(OWNER_TOKEN_ENV, None)
        environment["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        environment["DTC_HARNESS_THREAD_REPORT"] = os.fspath(thread_report)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    os.fspath(test_path),
                    "-m",
                    "core",
                    "-vv",
                    "--setup-show",
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired as error:
            output = "\n".join(
                part.decode() if isinstance(part, bytes) else str(part)
                for part in (error.stdout, error.stderr)
                if part
            )
            pytest.fail(f"child lifecycle session exceeded 60 seconds\n{output}")
        output = result.stdout + result.stderr

        assert result.returncode == pytest.ExitCode.TESTS_FAILED, output
        assert "1 failed, 1 passed" in output, output
        assert "test_sequential_db_backed_navigation_includes_events" in output, output
        assert "test_failing_browser_test_still_cleans_its_context" in output, output
        assert output.index("SETUP    S django_db_setup") < output.index("SETUP    S live_server")
        assert output.index("TEARDOWN S live_server") < output.index("TEARDOWN S django_db_setup")

        report = json.loads(thread_report.read_text(encoding="utf-8"))
        assert report == {"exitstatus": 1, "live_server_threads": []}
    finally:
        shutil.rmtree(harness_directory)
