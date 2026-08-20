"""Keep the owned SQLite runtime alive while CI captures screenshots.

The release application's test settings deliberately own an ephemeral SQLite
runtime per Python process.  CI needs migration, the local server, and the
controller-side browser capture to share that runtime, so this module is the
long-lived owner and supervises each child process.  The coordinator accepts
only the authorized ``website.settings.test`` settings module, pins it into an
explicit child environment, and never relies on ``manage.py``'s local default.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

from test_support import runtime as test_runtime
from test_support.runtime import TestRuntime, TestRuntimeSafetyError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_HEALTH_PATH = "/health/live"
DEFAULT_HEALTH_ATTEMPTS = 60
DEFAULT_HEALTH_INTERVAL_SECONDS = 1.0
DEFAULT_HTTP_TIMEOUT_SECONDS = 1.0
AUTHORIZED_DJANGO_SETTINGS_MODULE = "website.settings.test"


class ScreenshotRuntimeError(RuntimeError):
    """The screenshot process boundary could not be established safely."""


def _terminate_process(
    process: subprocess.Popen[str] | None,
    *,
    grace_seconds: float = 5.0,
) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait()


def _terminate_processes(*processes: subprocess.Popen[str] | None) -> None:
    for process in processes:
        _terminate_process(process)


def _kill_process_groups(*processes: subprocess.Popen[str] | None) -> None:
    """Stop child groups without waiting on a possibly interrupted Popen wait."""

    for process in processes:
        if process is None:
            continue
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _server_is_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=DEFAULT_HTTP_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def _validate_local_endpoint(*, base_url: str, host: str, port: int) -> None:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port != port
        or host not in {"127.0.0.1", "localhost"}
    ):
        raise ScreenshotRuntimeError("screenshot server must use an unauthenticated loopback URL")


def _wait_for_server(
    *,
    process: subprocess.Popen[str],
    base_url: str,
    attempts: int = DEFAULT_HEALTH_ATTEMPTS,
    interval_seconds: float = DEFAULT_HEALTH_INTERVAL_SECONDS,
) -> None:
    health_url = f"{base_url.rstrip('/')}{DEFAULT_HEALTH_PATH}"
    for attempt in range(attempts):
        if process.poll() is not None:
            raise ScreenshotRuntimeError(
                f"server exited before liveness became ready (exit {process.returncode})"
            )
        if _server_is_ready(health_url):
            return
        if attempt + 1 < attempts:
            time.sleep(interval_seconds)
    raise ScreenshotRuntimeError(f"server did not become ready at {health_url}")


def _build_child_environment() -> dict[str, str]:
    configured = os.environ.get("DJANGO_SETTINGS_MODULE")
    if configured is not None and configured != AUTHORIZED_DJANGO_SETTINGS_MODULE:
        raise ScreenshotRuntimeError(
            "screenshot runtime requires "
            f"DJANGO_SETTINGS_MODULE={AUTHORIZED_DJANGO_SETTINGS_MODULE}; "
            "refusing to spawn children"
        )
    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = AUTHORIZED_DJANGO_SETTINGS_MODULE
    return environment


def _run_migration(repository: Path, *, environment: Mapping[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["uv", "run", "--frozen", "python", "manage.py", "migrate", "--noinput"],
        cwd=repository,
        env=dict(environment),
        text=True,
        start_new_session=True,
    )


def _run_server(
    repository: Path,
    *,
    host: str,
    port: int,
    log_path: Path,
    environment: Mapping[str, str],
) -> subprocess.Popen[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(
            [
                "uv",
                "run",
                "--frozen",
                "python",
                "manage.py",
                "runserver",
                f"{host}:{port}",
                "--noreload",
            ],
            cwd=repository,
            env=dict(environment),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except BaseException:
        log_file.close()
        raise
    finally:
        log_file.close()


def _run_capture(
    controller_repository: Path,
    *,
    plan: Path,
    output: Path,
    base_url: str,
    environment: Mapping[str, str],
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            "uv",
            "run",
            "--frozen",
            "python",
            "-m",
            "ci.screenshot_capture",
            "--plan",
            os.fspath(plan),
            "--base-url",
            base_url,
            "--output",
            os.fspath(output),
        ],
        cwd=controller_repository,
        env=dict(environment),
        text=True,
        start_new_session=True,
    )


def _print_server_log(log_path: Path) -> None:
    try:
        contents = log_path.read_text(encoding="utf-8")
    except OSError:
        return
    if contents:
        print(contents, file=sys.stderr, end="")


def run_capture(
    *,
    repository: str | Path,
    controller_repository: str | Path,
    plan: str | Path,
    output: str | Path,
    server_log: str | Path,
    base_url: str = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> int:
    """Run migration, server, and capture under one owned test-runtime lease."""

    repository = Path(repository).resolve(strict=True)
    controller_repository = Path(controller_repository).resolve(strict=True)
    plan = Path(plan).resolve(strict=True)
    output = Path(output).resolve(strict=False)
    server_log = Path(server_log).resolve(strict=False)
    if not controller_repository.is_dir():
        raise ScreenshotRuntimeError("screenshot controller repository is not a directory")
    if not plan.is_file() or plan.is_symlink():
        raise ScreenshotRuntimeError("verification plan is not a regular file")
    if not (1 <= port <= 65_535):
        raise ScreenshotRuntimeError("server port is outside the valid range")
    try:
        _validate_local_endpoint(base_url=base_url, host=host, port=port)
    except ValueError as error:
        raise ScreenshotRuntimeError("screenshot server URL is malformed") from error
    environment = _build_child_environment()
    print(f"screenshot runtime: using DJANGO_SETTINGS_MODULE={AUTHORIZED_DJANGO_SETTINGS_MODULE}")
    output.mkdir(parents=True, exist_ok=True)

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        runtime = TestRuntime.acquire(repository)
    except TestRuntimeSafetyError as error:
        raise ScreenshotRuntimeError(str(error)) from error
    if not runtime.is_owner:
        raise ScreenshotRuntimeError("screenshot coordinator must own its test runtime")

    migration: subprocess.Popen[str] | None = None
    server: subprocess.Popen[str] | None = None
    capture: subprocess.Popen[str] | None = None
    interrupted_signal: int | None = None

    def handle_signal(signum: int, _frame: object) -> NoReturn:
        nonlocal interrupted_signal
        interrupted_signal = signum
        # A signal can interrupt Popen.wait while it holds that Popen's internal
        # wait lock.  Calling wait() again from this handler would deadlock, so
        # terminate each process group directly and let the coordinator exit.
        _kill_process_groups(capture, server, migration)
        runtime.cleanup()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
        raise AssertionError("signal did not terminate the coordinator")

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    try:
        migration = _run_migration(repository, environment=environment)
        migration_result = migration.wait()
        if migration_result != 0:
            return migration_result

        server = _run_server(
            repository,
            host=host,
            port=port,
            log_path=server_log,
            environment=environment,
        )
        try:
            _wait_for_server(process=server, base_url=base_url)
        except ScreenshotRuntimeError:
            _terminate_process(server)
            _print_server_log(server_log)
            raise

        capture = _run_capture(
            controller_repository,
            plan=plan,
            output=output,
            base_url=base_url,
            environment=environment,
        )
        return capture.wait()
    finally:
        _terminate_processes(capture, server, migration)
        runtime.cleanup()
        test_runtime._unregister_termination_cleanup(runtime)
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
        if interrupted_signal is not None:
            raise ScreenshotRuntimeError("screenshot coordinator interrupted")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--controller-repository", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-log", type=Path, required=True)
    parser.add_argument("--base-url", default=f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    try:
        result = run_capture(
            repository=args.repository,
            controller_repository=args.controller_repository,
            plan=args.plan,
            output=args.output,
            server_log=args.server_log,
            base_url=args.base_url,
            host=args.host,
            port=args.port,
        )
    except ScreenshotRuntimeError as error:
        print(f"screenshot runtime failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    raise SystemExit(result)


if __name__ == "__main__":
    main()
