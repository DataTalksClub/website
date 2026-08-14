from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict, cast

import pytest

from ci import screenshot_runtime


class ScreenshotInputs(TypedDict):
    repository: Path
    controller_repository: Path
    plan: Path
    output: Path
    server_log: Path


class FakeRuntime:
    is_owner = True
    _owner_token = "fake-owner-token"

    def __init__(self) -> None:
        self.cleanup_calls = 0

    def cleanup(self) -> None:
        self.cleanup_calls += 1


class FakeProcess:
    _next_pid = 1000

    def __init__(self, returncode: int = 0) -> None:
        self.pid = self._next_pid
        type(self)._next_pid += 1
        self.returncode = returncode
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self.returncode if self.wait_calls else None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        return self.returncode


def _inputs(tmp_path: Path) -> ScreenshotInputs:
    repository = tmp_path / "release"
    controller = tmp_path / "controller"
    repository.mkdir()
    controller.mkdir()
    plan = controller / "plan.json"
    plan.write_text("{}\n", encoding="utf-8")
    return {
        "repository": repository,
        "controller_repository": controller,
        "plan": plan,
        "output": controller / "output",
        "server_log": controller / "output" / "server.log",
    }


def _record(calls: list[str], name: str, value: Any) -> Callable[..., Any]:
    def callback(*_args: object, **_kwargs: object) -> Any:
        calls.append(name)
        return value

    return callback


def test_signal_cleanup_kills_child_groups_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = cast(Any, SimpleNamespace(pid=1234))
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        screenshot_runtime.os,
        "killpg",
        lambda pid, signum: signals.append((pid, signum)),
    )

    screenshot_runtime._kill_process_groups(process)

    assert signals == [
        (1234, screenshot_runtime.signal.SIGTERM),
        (1234, screenshot_runtime.signal.SIGKILL),
    ]


def test_run_capture_keeps_one_owner_across_migration_server_and_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    runtime = FakeRuntime()
    migration = FakeProcess()
    server = FakeProcess()
    capture = FakeProcess()
    calls: list[str] = []

    monkeypatch.setattr(
        screenshot_runtime.TestRuntime,
        "acquire",
        lambda _repository: runtime,
    )
    monkeypatch.setattr(
        screenshot_runtime,
        "_run_migration",
        _record(calls, "migration", migration),
    )
    monkeypatch.setattr(
        screenshot_runtime,
        "_run_server",
        _record(calls, "server", server),
    )
    monkeypatch.setattr(
        screenshot_runtime,
        "_wait_for_server",
        _record(calls, "health", None),
    )
    monkeypatch.setattr(
        screenshot_runtime,
        "_run_capture",
        _record(calls, "capture", capture),
    )
    monkeypatch.setattr(
        screenshot_runtime,
        "_terminate_processes",
        _record(calls, "stop", None),
    )

    result = screenshot_runtime.run_capture(**inputs)

    assert result == 0
    assert calls == ["migration", "server", "health", "capture", "stop"]
    assert runtime.cleanup_calls == 1


def test_run_capture_returns_migration_failure_and_never_starts_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    runtime = FakeRuntime()
    migration = FakeProcess(returncode=17)
    calls: list[str] = []

    monkeypatch.setattr(
        screenshot_runtime.TestRuntime,
        "acquire",
        lambda _repository: runtime,
    )
    monkeypatch.setattr(
        screenshot_runtime,
        "_run_migration",
        _record(calls, "migration", migration),
    )
    monkeypatch.setattr(
        screenshot_runtime,
        "_run_server",
        _record(calls, "server", FakeProcess()),
    )
    monkeypatch.setattr(
        screenshot_runtime,
        "_terminate_processes",
        _record(calls, "stop", None),
    )

    result = screenshot_runtime.run_capture(**inputs)

    assert result == 17
    assert calls == ["migration", "stop"]
    assert runtime.cleanup_calls == 1


def test_run_capture_rejects_inherited_child_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    runtime = SimpleNamespace(is_owner=False, cleanup=lambda: pytest.fail("child cleaned owner"))
    monkeypatch.setattr(
        screenshot_runtime.TestRuntime,
        "acquire",
        lambda _repository: runtime,
    )

    with pytest.raises(
        screenshot_runtime.ScreenshotRuntimeError,
        match="must own its test runtime",
    ):
        screenshot_runtime.run_capture(**inputs)


@pytest.mark.parametrize(
    ("url", "host", "port"),
    [
        ("https://127.0.0.1:8000", "127.0.0.1", 8000),
        ("http://example.invalid:8000", "127.0.0.1", 8000),
        ("http://127.0.0.1:8001", "127.0.0.1", 8000),
        ("http://user:pass@127.0.0.1:8000", "127.0.0.1", 8000),
        ("http://127.0.0.1:8000", "0.0.0.0", 8000),
    ],
)
def test_local_endpoint_guard_rejects_non_loopback_capture_targets(
    url: str,
    host: str,
    port: int,
) -> None:
    with pytest.raises(
        screenshot_runtime.ScreenshotRuntimeError,
        match="loopback",
    ):
        screenshot_runtime._validate_local_endpoint(
            base_url=url,
            host=host,
            port=port,
        )
