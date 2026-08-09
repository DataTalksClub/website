from __future__ import annotations

import argparse
import base64
import configparser
import hashlib
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, Protocol

from deploy import gate_b_assembler as assembler
from deploy import gate_b_evidence as evidence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = PROJECT_ROOT / ".tmp"
MAX_CREDENTIAL_BYTES = 16 * 1024
AWS_ACCESS_KEY_PATTERN = re.compile(r"ASIA[A-Z0-9]{16}")
CAPTURE_ID_CLOCK_SKEW_SECONDS = 5
OPERATOR_STOP_PHASE_CODES = assembler.OPERATOR_STOP_PHASE_CODES
OPERATOR_STOP_CODE_TO_PHASE = assembler.OPERATOR_STOP_CODE_TO_PHASE


class OperatorError(RuntimeError):
    """A fail-closed operator error exposing only a stable safe code."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not re.fullmatch(r"[a-z0-9-]+", code):
            code = "invalid-gate-b-operation"
        self._operator_stop_original_code = code
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise OperatorError(code)


def _operator_stop_line(error: OperatorError | None = None) -> str:
    if type(error) is not OperatorError:
        return assembler.OPERATOR_STOP_GENERIC_LINE
    state = object.__getattribute__(error, "__dict__")
    code = dict.get(state, "code")
    original_code = dict.get(state, "_operator_stop_original_code")
    if type(code) is not str or type(original_code) is not str or code != original_code:
        return assembler.OPERATOR_STOP_GENERIC_LINE
    phase = OPERATOR_STOP_CODE_TO_PHASE.get(code)
    if phase is None:
        return assembler.OPERATOR_STOP_GENERIC_LINE
    return f"gate-b-operator-stop phase={phase} code={code}\n"


class Runner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]: ...


@dataclass(frozen=True)
class FrozenCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: datetime


@dataclass(frozen=True)
class CapturedCommand:
    response: dict[str, Any]
    error: dict[str, Any]
    status: dict[str, Any]
    stdout: bytes
    stderr: bytes


def _credential_child_env() -> dict[str, str]:
    return {
        "HOME": "/home/alexey",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "NO_COLOR": "1",
        "PATH": "/usr/bin:/bin",
    }


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            process.kill()
    process.wait()


class _ProcessRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen[bytes]] = set()
        self._cancelled = False

    def add(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            cancelled = self._cancelled
            if not cancelled:
                self._processes.add(process)
        if cancelled:
            _kill_and_reap(process)

    def discard(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._processes.discard(process)

    def kill_all(self) -> None:
        with self._lock:
            self._cancelled = True
            processes = tuple(self._processes)
        for process in processes:
            _kill_and_reap(process)


def _run_bounded_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    pass_fds: Sequence[int] = (),
    executable: str | None = None,
    process_registry: _ProcessRegistry | None = None,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
        pass_fds=tuple(pass_fds),
        start_new_session=True,
        executable=executable,
    )
    if process_registry is not None:
        process_registry.add(process)
    try:
        if process.stdout is None or process.stderr is None:
            _kill_and_reap(process)
            _fail("provider-command-failed")
        selector: selectors.BaseSelector | None = None
        chunks: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        limits = {"stdout": 2 * 1024 * 1024, "stderr": 16 * 1024}
        deadline = time.monotonic() + timeout
        try:
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _kill_and_reap(process)
                    raise subprocess.TimeoutExpired(list(argv), timeout)
                events = selector.select(remaining)
                if not events:
                    continue
                for key, _mask in events:
                    stream = key.fileobj
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(stream)
                        continue
                    target = chunks[key.data]
                    target.extend(data)
                    if len(target) > limits[key.data]:
                        _kill_and_reap(process)
                        return subprocess.CompletedProcess(
                            list(argv),
                            125,
                            bytes(chunks["stdout"]),
                            bytes(chunks["stderr"]),
                        )
            return_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except BaseException:
            _kill_and_reap(process)
            raise
        finally:
            if selector is not None:
                selector.close()
        return subprocess.CompletedProcess(
            list(argv), return_code, bytes(chunks["stdout"]), bytes(chunks["stderr"])
        )
    finally:
        if process_registry is not None:
            process_registry.discard(process)


def _default_runner(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    return _run_bounded_process(argv, cwd=cwd, env=env, timeout=timeout)


def _safe_regular_file(
    path: Path,
    expected_mode: int,
    expected_hash: str | None = None,
    *,
    expected_uid: int | None = None,
    read_content: bool = False,
) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
    except OSError as exc:
        raise OperatorError("unsafe-credential-file") from exc
    try:
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != expected_mode
            or info.st_uid != (os.geteuid() if expected_uid is None else expected_uid)
            or info.st_nlink != 1
        ):
            _fail("unsafe-credential-file")
        digest = hashlib.sha256()
        content = bytearray()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            if expected_hash is not None:
                digest.update(chunk)
            if read_content:
                content.extend(chunk)
                if len(content) > 64 * 1024:
                    _fail("unsafe-credential-file")
        if expected_hash is not None and digest.hexdigest() != expected_hash:
            _fail("credential-source-mismatch")
        return bytes(content)
    except OSError as exc:
        raise OperatorError("unsafe-credential-file") from exc
    finally:
        os.close(descriptor)


def _open_bound_regular_file(
    path: Path,
    expected_mode: int,
    expected_hash: str | None,
    expected_uid: int,
) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != expected_mode
            or info.st_uid != expected_uid
            or info.st_nlink != 1
        ):
            _fail("unsafe-bound-executable")
        if expected_hash is not None:
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            if digest.hexdigest() != expected_hash:
                _fail("bound-executable-mismatch")
            os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


class _BoundRunner:
    """Execute only reviewed inodes held open for the complete capture."""

    def __init__(self, contract: dict[str, Any]) -> None:
        self._contract = contract
        self._descriptors: list[int] = []
        self._identity_argv = tuple(contract["graph"]["operations"][0]["argv"])
        self._allowed_provider_argv = {self._identity_argv}
        self._executed_provider_argv: set[tuple[str, ...]] = set()
        self._credential_used = False
        self._identity_sealed = False
        self._state_lock = threading.Lock()
        self._process_registry = _ProcessRegistry()
        self._aws_environment: dict[str, str] | None = None
        credential = contract["credential_process"]
        aws_tool = contract["tools"]["aws"]
        github_tool = contract["tools"]["github"]
        try:
            self._credential_python = self._open(
                credential["interpreter_resolved_path"],
                credential["interpreter_mode"],
                credential["interpreter_file_sha256"],
                credential["interpreter_owner_uid"],
            )
            self._credential_script = self._open(
                credential["script_path"],
                credential["script_mode"],
                credential["script_file_sha256"],
                os.geteuid(),
            )
            self._credential_env = self._open(
                credential["env_path"],
                credential["env_mode"],
                None,
                os.geteuid(),
            )
            self._aws_python = self._open(
                aws_tool["interpreter_resolved_path"],
                aws_tool["interpreter_mode"],
                aws_tool["interpreter_file_sha256"],
                aws_tool["interpreter_owner_uid"],
            )
            self._aws_cli = self._open(
                aws_tool["resolved_path"],
                aws_tool["mode"],
                aws_tool["file_sha256"],
                aws_tool["owner_uid"],
            )
            self._github_cli = self._open(
                github_tool["resolved_path"],
                github_tool["mode"],
                github_tool["file_sha256"],
                github_tool["owner_uid"],
            )
        except BaseException:
            self.close()
            raise

    def _open(self, path: str, mode: str, digest: str | None, uid: int) -> int:
        descriptor = _open_bound_regular_file(Path(path), int(mode, 8), digest, uid)
        self._descriptors.append(descriptor)
        return descriptor

    @staticmethod
    def _path(descriptor: int) -> str:
        return f"/proc/self/fd/{descriptor}"

    def close(self) -> None:
        while self._descriptors:
            os.close(self._descriptors.pop())

    def __enter__(self) -> _BoundRunner:
        return self

    def __exit__(self, *args: Any) -> None:
        del args
        self.close()

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        logical = list(argv)
        credential = self._contract["credential_process"]
        aws_tool = self._contract["tools"]["aws"]
        github_tool = self._contract["tools"]["github"]
        descriptors: Sequence[int]
        if cwd != PROJECT_ROOT:
            _fail("unsafe-bound-execution-context")
        if logical == credential["execution_argv"]:
            if (
                timeout != self._contract["limits"]["credential_timeout_seconds"]
                or env != _credential_child_env()
            ):
                _fail("unsafe-bound-execution-context")
            with self._state_lock:
                if self._credential_used:
                    _fail("credential-resolution-repeated")
                self._credential_used = True
            actual = [
                logical[0],
                self._path(self._credential_script),
                *logical[2:-1],
                self._path(self._credential_env),
            ]
            executable = self._path(self._credential_python)
            descriptors = (
                self._credential_python,
                self._credential_script,
                self._credential_env,
            )
        else:
            logical_tuple = tuple(logical)
            if timeout != self._contract["limits"]["command_timeout_seconds"]:
                _fail("unsafe-bound-execution-context")
            if logical and logical[0] == aws_tool["resolved_path"]:
                fixed = {
                    **self._contract["child_environments"]["base_values"],
                    **self._contract["child_environments"]["aws_fixed_values"],
                }
                added = set(self._contract["child_environments"]["aws_added_names"])
                if (
                    set(env) != set(fixed) | added
                    or any(env.get(name) != value for name, value in fixed.items())
                    or not AWS_ACCESS_KEY_PATTERN.fullmatch(env.get("AWS_ACCESS_KEY_ID", ""))
                    or not env.get("AWS_SECRET_ACCESS_KEY")
                    or not env.get("AWS_SESSION_TOKEN")
                ):
                    _fail("unsafe-bound-execution-context")
                with self._state_lock:
                    if self._aws_environment is None:
                        self._aws_environment = dict(env)
                    elif env != self._aws_environment:
                        _fail("unsafe-bound-execution-context")
            elif logical and logical[0] == github_tool["resolved_path"]:
                if env != build_github_child_env(self._contract):
                    _fail("unsafe-bound-execution-context")
            else:
                _fail("unbound-executable")
            with self._state_lock:
                if (
                    not self._credential_used
                    or logical_tuple not in self._allowed_provider_argv
                    or (logical_tuple != self._identity_argv and not self._identity_sealed)
                    or logical_tuple in self._executed_provider_argv
                ):
                    _fail("unbound-provider-operation")
                self._executed_provider_argv.add(logical_tuple)
            if logical[0] == aws_tool["resolved_path"]:
                actual = [
                    aws_tool["interpreter_invocation_path"],
                    self._path(self._aws_cli),
                    *logical[1:],
                ]
                descriptors = (self._aws_python, self._aws_cli)
                executable = self._path(self._aws_python)
            elif logical[0] == github_tool["resolved_path"]:
                actual = logical
                descriptors = (self._github_cli,)
                executable = self._path(self._github_cli)
            else:
                _fail("unbound-executable")
        return _run_bounded_process(
            actual,
            cwd=cwd,
            env=env,
            timeout=timeout,
            pass_fds=descriptors,
            executable=executable,
            process_registry=self._process_registry,
        )

    def authorize(self, specs: Sequence[dict[str, Any]]) -> None:
        if (
            len(specs) != 174
            or assembler.execution_graph_sha256(specs) != assembler.EXPECTED_RESOLVED_GRAPH_SHA256
        ):
            _fail("provider-operation-count")
        self._allowed_provider_argv = {tuple(spec["argv"]) for spec in specs}

    def seal_identity(self) -> None:
        with self._state_lock:
            if self._identity_argv not in self._executed_provider_argv:
                _fail("identity-not-first")
            self._identity_sealed = True

    def cancel_all(self) -> None:
        self._process_registry.kill_all()


def _validate_configured_credential_process(contract: dict[str, Any]) -> None:
    credential = contract["credential_process"]
    config_path = Path(credential["config_path"])
    config_bytes = _safe_regular_file(
        config_path,
        int(credential["config_mode"], 8),
        credential["config_file_sha256"],
        read_content=True,
    )
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        parser.read_string(config_bytes.decode("utf-8"))
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise OperatorError("invalid-aws-config") from exc
    expected = " ".join(credential["configured_argv"])
    section = credential["config_section"]
    sections_with_process = [
        item
        for item in parser.sections() + [parser.default_section]
        if parser.has_option(item, "credential_process")
    ]
    if (
        sections_with_process != [section]
        or parser.get(section, "credential_process").strip() != expected
    ):
        _fail("credential-process-config-mismatch")


def _parse_expiration(value: Any) -> datetime:
    if not isinstance(value, str) or not assembler.CREDENTIAL_EXPIRATION_PATTERN.fullmatch(value):
        _fail("invalid-credential-response")
    timestamp = value[:-1] if value.endswith("Z") else value[:-6]
    try:
        result = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError as exc:
        raise OperatorError("invalid-credential-response") from exc
    return result


def load_frozen_credentials(
    contract: dict[str, Any],
    *,
    runner: Runner = _default_runner,
    now: datetime | None = None,
) -> FrozenCredentials:
    credential = contract["credential_process"]
    for tool in contract["tools"].values():
        _safe_regular_file(
            Path(tool["resolved_path"]),
            int(tool["mode"], 8),
            tool["file_sha256"],
            expected_uid=tool["owner_uid"],
        )
    _validate_configured_credential_process(contract)
    _safe_regular_file(
        Path(credential["env_path"]),
        int(credential["env_mode"], 8),
    )
    _safe_regular_file(
        Path(credential["script_path"]),
        int(credential["script_mode"], 8),
        credential["script_file_sha256"],
    )
    safe_env = _credential_child_env()
    try:
        completed = runner(
            credential["execution_argv"],
            cwd=PROJECT_ROOT,
            env=safe_env,
            timeout=contract["limits"]["credential_timeout_seconds"],
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OperatorError("credential-process-failed") from exc
    if (
        completed.returncode != 0
        or completed.stderr != b""
        or len(completed.stdout) > MAX_CREDENTIAL_BYTES
    ):
        _fail("credential-process-failed")
    try:
        response = evidence.parse_json(completed.stdout.decode("utf-8"))
    except (UnicodeError, evidence.EvidenceError) as exc:
        raise OperatorError("invalid-credential-response") from exc
    if not isinstance(response, dict) or set(response) != {
        "Version",
        "AccessKeyId",
        "SecretAccessKey",
        "SessionToken",
        "Expiration",
    }:
        _fail("invalid-credential-response")
    if (
        isinstance(response["Version"], bool)
        or not isinstance(response["Version"], int)
        or response["Version"] != 1
        or not AWS_ACCESS_KEY_PATTERN.fullmatch(str(response["AccessKeyId"]))
    ):
        _fail("invalid-credential-response")
    if not all(
        isinstance(response[key], str) and response[key]
        for key in ("SecretAccessKey", "SessionToken")
    ):
        _fail("invalid-credential-response")
    expiration = _parse_expiration(response["Expiration"])
    current = (now or datetime.now(UTC)).astimezone(UTC)
    remaining = (expiration - current).total_seconds()
    if (
        remaining < credential["minimum_ttl_seconds_at_start"]
        or remaining > credential["accepted_duration_seconds"]
    ):
        _fail("credential-lifetime-out-of-contract")
    return FrozenCredentials(
        access_key_id=response["AccessKeyId"],
        secret_access_key=response["SecretAccessKey"],
        session_token=response["SessionToken"],
        expiration=expiration,
    )


def _base_child_env(contract: dict[str, Any]) -> dict[str, str]:
    return dict(contract["child_environments"]["base_values"])


def build_aws_child_env(contract: dict[str, Any], credentials: FrozenCredentials) -> dict[str, str]:
    result = _base_child_env(contract)
    result.update(contract["child_environments"]["aws_fixed_values"])
    result.update(
        {
            "AWS_ACCESS_KEY_ID": credentials.access_key_id,
            "AWS_SECRET_ACCESS_KEY": credentials.secret_access_key,
            "AWS_SESSION_TOKEN": credentials.session_token,
        }
    )
    expected_names = (
        set(contract["child_environments"]["base_values"])
        | set(contract["child_environments"]["aws_fixed_values"])
        | set(contract["child_environments"]["aws_added_names"])
    )
    if set(result) != expected_names:
        _fail("unsafe-aws-child-environment")
    return result


def build_github_child_env(contract: dict[str, Any]) -> dict[str, str]:
    result = _base_child_env(contract)
    result.update(contract["child_environments"]["github_fixed_values"])
    if any(name.startswith("AWS_") or name in {"GH_TOKEN", "GITHUB_TOKEN"} for name in result):
        _fail("unsafe-github-child-environment")
    return result


def _parse_success_stdout(value: bytes, max_bytes: int) -> dict[str, Any]:
    if len(value) > max_bytes:
        _fail("provider-output-too-large")
    if value.strip() == b"":
        return {}
    try:
        result = evidence.parse_json(value.decode("utf-8"))
    except (UnicodeError, evidence.EvidenceError) as exc:
        raise OperatorError("invalid-provider-json") from exc
    if not isinstance(result, dict):
        _fail("invalid-provider-json")
    return result


def _parse_expected_aws_error(stderr: bytes, spec: dict[str, Any], max_bytes: int) -> str:
    if len(stderr) > max_bytes:
        _fail("provider-error-too-large")
    try:
        message = stderr.decode("utf-8")
    except UnicodeError as exc:
        raise OperatorError("invalid-provider-error") from exc
    code = spec["expected"]["error_code"]
    pattern = re.compile(
        rf"^An error occurred \({re.escape(code)}\) when calling the "
        rf"{re.escape(spec['operation'])} operation: [^\r\n]+\n?$"
    )
    if not pattern.fullmatch(message):
        _fail("unexpected-provider-error")
    return code


def run_exact_argv(
    spec: dict[str, Any],
    contract: dict[str, Any],
    credentials: FrozenCredentials,
    *,
    runner: Runner = _default_runner,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], int, str, str, bytes, bytes]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if (credentials.expiration - current).total_seconds() < contract["credential_process"][
        "hard_reserve_seconds"
    ]:
        _fail("credential-reserve-crossed")
    provider = spec["provider"]
    env = (
        build_aws_child_env(contract, credentials)
        if provider == "aws"
        else build_github_child_env(contract)
    )
    started = current.isoformat().replace("+00:00", "Z")
    try:
        completed = runner(
            spec["argv"],
            cwd=PROJECT_ROOT,
            env=env,
            timeout=contract["limits"]["command_timeout_seconds"],
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OperatorError("provider-command-failed") from exc
    finished = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    expected = spec["expected"]
    if expected["exit_code"] == 0:
        if completed.returncode != 0 or completed.stderr != b"":
            _fail("unexpected-provider-result")
        response = _parse_success_stdout(completed.stdout, contract["limits"]["max_stdout_bytes"])
        return response, {}, 0, started, finished, completed.stdout, completed.stderr
    if completed.returncode == 0 or completed.stdout != b"":
        _fail("unexpected-provider-result")
    code = _parse_expected_aws_error(completed.stderr, spec, contract["limits"]["max_stderr_bytes"])
    error = {
        "provider": provider,
        "service": spec["service"],
        "operation": spec["operation"],
        "target": spec["target"],
        "code": code,
    }
    return (
        {},
        error,
        completed.returncode,
        started,
        finished,
        completed.stdout,
        completed.stderr,
    )


def _open_private_directory(path: Path) -> int:
    if ".." in path.parts:
        _fail("unsafe-private-directory")
    absolute = path if path.is_absolute() else PROJECT_ROOT / path
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(absolute.anchor, flags)
        for part in absolute.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise OperatorError("unsafe-private-directory") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
        or info.st_nlink < 2
    ):
        os.close(descriptor)
        _fail("unsafe-private-directory")
    return descriptor


def _validate_private_directory(path: Path, *, root: bool = False) -> None:
    descriptor = _open_private_directory(path)
    os.close(descriptor)
    if root and path != TMP_ROOT:
        _fail("unsafe-private-directory")


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    data = evidence.canonical_json_bytes(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    parent_descriptor = -1
    try:
        parent_descriptor = _open_private_directory(path.parent)
        descriptor = os.open(path.name, flags, 0o600, dir_fd=parent_descriptor)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        info = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise OperatorError("unsafe-private-write") from exc
    finally:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
    ):
        _fail("unsafe-private-write")


def _prepare_capture(capture_id: str) -> Path:
    if not assembler.CAPTURE_ID_PATTERN.fullmatch(capture_id):
        _fail("invalid-capture-id")
    try:
        TMP_ROOT.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise OperatorError("unsafe-tmp-root") from exc
    capture_dir = TMP_ROOT / f"gate-b-{capture_id}"
    raw_dir = capture_dir / "raw"
    root_descriptor = -1
    capture_descriptor = -1
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        root_descriptor = _open_private_directory(TMP_ROOT)
        os.mkdir(capture_dir.name, mode=0o700, dir_fd=root_descriptor)
        capture_descriptor = os.open(capture_dir.name, directory_flags, dir_fd=root_descriptor)
        capture_info = os.fstat(capture_descriptor)
        if (
            not stat.S_ISDIR(capture_info.st_mode)
            or stat.S_IMODE(capture_info.st_mode) != 0o700
            or capture_info.st_uid != os.geteuid()
            or capture_info.st_nlink < 2
        ):
            _fail("unsafe-private-directory")
        os.mkdir(raw_dir.name, mode=0o700, dir_fd=capture_descriptor)
    except OSError as exc:
        raise OperatorError("capture-already-exists") from exc
    finally:
        if capture_descriptor >= 0:
            os.close(capture_descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)
    _validate_private_directory(raw_dir)
    return capture_dir


def _validate_capture_start(capture_id: str, now: datetime | None = None) -> None:
    if not assembler.CAPTURE_ID_PATTERN.fullmatch(capture_id):
        _fail("invalid-capture-id")
    try:
        capture_time = datetime.strptime(capture_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise OperatorError("invalid-capture-id") from exc
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if abs((current - capture_time).total_seconds()) > CAPTURE_ID_CLOCK_SKEW_SECONDS:
        _fail("stale-capture-id")


def capture_one(
    spec: dict[str, Any],
    contract: dict[str, Any],
    credentials: FrozenCredentials,
    capture_id: str,
    raw_dir: Path,
    graph_sha256: str,
    *,
    runner: Runner = _default_runner,
    now: datetime | None = None,
) -> CapturedCommand:
    response, error, exit_code, started, finished, stdout, stderr = run_exact_argv(
        spec, contract, credentials, runner=runner, now=now
    )
    status_doc = {
        "schema_version": 1,
        "capture_id": capture_id,
        "command_id": spec["id"],
        "sequence": spec["sequence"],
        "phase": spec["phase"],
        "provider": spec["provider"],
        "argv_sha256": assembler._canonical_sha256(spec["argv"]),
        "graph_sha256": graph_sha256,
        "started_at": started,
        "finished_at": finished,
        "exit_code": exit_code,
        "response_sha256": hashlib.sha256(stdout).hexdigest(),
        "error_sha256": hashlib.sha256(stderr).hexdigest(),
    }
    command_id = spec["id"]
    _write_private_json(
        raw_dir / f"{command_id}.response.json",
        {"stdout_base64": base64.b64encode(stdout).decode("ascii")},
    )
    _write_private_json(
        raw_dir / f"{command_id}.error.json",
        {"stderr_base64": base64.b64encode(stderr).decode("ascii")},
    )
    _write_private_json(raw_dir / f"{command_id}.status.json", status_doc)
    return CapturedCommand(
        response=response,
        error=error,
        status=status_doc,
        stdout=stdout,
        stderr=stderr,
    )


def _capture_bounded_phase(
    specs: Sequence[dict[str, Any]],
    contract: dict[str, Any],
    credentials: FrozenCredentials,
    capture_id: str,
    raw_dir: Path,
    graph_sha256: str,
    *,
    runner: Runner,
) -> None:
    maximum = contract["limits"]["max_concurrency"]
    pending_specs = iter(specs)
    active: dict[Future[CapturedCommand], dict[str, Any]] = {}
    failure: BaseException | None = None
    executor = ThreadPoolExecutor(max_workers=maximum, thread_name_prefix="gate-b")
    try:
        for _ in range(maximum):
            try:
                spec = next(pending_specs)
            except StopIteration:
                break
            future = executor.submit(
                capture_one,
                spec,
                contract,
                credentials,
                capture_id,
                raw_dir,
                graph_sha256,
                runner=runner,
            )
            active[future] = spec
        while active and failure is None:
            done, _not_done = wait(active, return_when=FIRST_COMPLETED)
            completed = sorted(done, key=lambda future: active[future]["sequence"])
            for future in completed:
                active.pop(future)
                try:
                    future.result()
                except BaseException as exc:
                    if failure is None:
                        failure = exc
            if failure is not None:
                break
            for _future in completed:
                try:
                    spec = next(pending_specs)
                except StopIteration:
                    continue
                next_future = executor.submit(
                    capture_one,
                    spec,
                    contract,
                    credentials,
                    capture_id,
                    raw_dir,
                    graph_sha256,
                    runner=runner,
                )
                active[next_future] = spec
        if failure is not None:
            cancel_all = getattr(runner, "cancel_all", None)
            if callable(cancel_all):
                cancel_all()
            for future in active:
                future.cancel()
    except BaseException:
        cancel_all = getattr(runner, "cancel_all", None)
        if callable(cancel_all):
            cancel_all()
        for future in active:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    if failure is not None:
        if isinstance(failure, OperatorError):
            raise failure
        raise OperatorError("provider-phase-failed") from failure


def _provisional_bindings(
    seed: dict[str, Any], manifest: dict[str, Any], capture_id: str
) -> dict[str, Any]:
    return assembler.build_bindings_envelope(
        seed,
        manifest,
        capture_id,
        {
            "Account": "817685572750",
            "Arn": (
                "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/"
                "phone-sandbox-00000000"
            ),
            "UserId": "AROA34YO3VSHI2OCVBKTW:phone-sandbox-00000000",
        },
    )


def run_identity_phase(
    seed: dict[str, Any],
    contract: dict[str, Any],
    manifest: dict[str, Any],
    credentials: FrozenCredentials,
    capture_id: str,
    capture_dir: Path,
    specs: Sequence[dict[str, Any]],
    *,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    if specs[0]["id"] != "sts-caller":
        _fail("identity-not-first")
    graph_hash = assembler.execution_graph_sha256(specs)
    captured = capture_one(
        specs[0],
        contract,
        credentials,
        capture_id,
        capture_dir / "raw",
        graph_hash,
        runner=runner,
    )
    assembler._validated_status_times(
        capture_id,
        captured.status["started_at"],
        captured.status["finished_at"],
        identity=True,
    )
    bindings = assembler.build_bindings_envelope(seed, manifest, capture_id, captured.response)
    _write_private_json(capture_dir / "bindings.json", bindings)
    try:
        result = evidence.validate_bindings(bindings, manifest)
    except evidence.EvidenceError as exc:
        raise OperatorError("binding-validation-stop") from exc
    _write_private_json(capture_dir / "bindings.result.json", result)
    return bindings


def run_readback_phase(
    seed: dict[str, Any],
    contract: dict[str, Any],
    manifest: dict[str, Any],
    credentials: FrozenCredentials,
    capture_id: str,
    capture_dir: Path,
    bindings: dict[str, Any],
    specs: Sequence[dict[str, Any]],
    *,
    runner: Runner = _default_runner,
) -> None:
    readbacks = list(specs[:84])
    _capture_bounded_phase(
        readbacks[1:],
        contract,
        credentials,
        capture_id,
        capture_dir / "raw",
        assembler.execution_graph_sha256(specs),
        runner=runner,
    )
    raw = assembler.load_raw_capture_set(
        capture_dir,
        readbacks,
        expected_graph_sha256=assembler.execution_graph_sha256(specs),
    )
    try:
        binding_result = evidence.validate_bindings(bindings, manifest)
        policies = assembler.build_policy_envelope(raw, manifest, bindings)
        evidence.validate_policy_bundle(policies, manifest, binding_result)
        resources = assembler.build_resource_envelope(raw, seed, manifest, bindings)
        evidence.validate_resource_bundle(resources, manifest, bindings, binding_result)
    except (assembler.AssemblyError, evidence.EvidenceError) as exc:
        raise OperatorError("readback-validation-stop") from exc


def run_simulator_phase(
    contract: dict[str, Any],
    credentials: FrozenCredentials,
    capture_id: str,
    capture_dir: Path,
    specs: Sequence[dict[str, Any]],
    *,
    runner: Runner = _default_runner,
) -> None:
    _capture_bounded_phase(
        list(specs[84:]),
        contract,
        credentials,
        capture_id,
        capture_dir / "raw",
        assembler.execution_graph_sha256(specs),
        runner=runner,
    )


def run_gate_b(
    capture_id: str,
    seed: dict[str, Any],
    contract: dict[str, Any],
    manifest: dict[str, Any],
    *,
    runner: Runner = _default_runner,
    now: datetime | None = None,
) -> dict[str, Any]:
    assembler.validate_execution_contract(contract, seed, manifest)
    _validate_capture_start(capture_id, now)
    if runner is _default_runner:
        with _BoundRunner(contract) as bound_runner:
            return _run_gate_b_validated(
                capture_id,
                seed,
                contract,
                manifest,
                runner=bound_runner,
                now=now,
            )
    return _run_gate_b_validated(
        capture_id,
        seed,
        contract,
        manifest,
        runner=runner,
        now=now,
    )


def _run_gate_b_validated(
    capture_id: str,
    seed: dict[str, Any],
    contract: dict[str, Any],
    manifest: dict[str, Any],
    *,
    runner: Runner,
    now: datetime | None,
) -> dict[str, Any]:
    capture_dir = _prepare_capture(capture_id)
    credentials = load_frozen_credentials(contract, runner=runner, now=now)
    _validate_capture_start(capture_id)
    provisional = _provisional_bindings(seed, manifest, capture_id)
    specs = assembler.complete_operation_specs(contract, manifest, provisional)
    if len(specs) != 174:
        _fail("provider-operation-count")
    if isinstance(runner, _BoundRunner):
        runner.authorize(specs)
    bindings = run_identity_phase(
        seed,
        contract,
        manifest,
        credentials,
        capture_id,
        capture_dir,
        specs,
        runner=runner,
    )
    if isinstance(runner, _BoundRunner):
        runner.seal_identity()
    actual_specs = assembler.complete_operation_specs(contract, manifest, bindings)
    if actual_specs != specs:
        _fail("post-identity-graph-mismatch")
    run_readback_phase(
        seed,
        contract,
        manifest,
        credentials,
        capture_id,
        capture_dir,
        bindings,
        specs,
        runner=runner,
    )
    run_simulator_phase(
        contract,
        credentials,
        capture_id,
        capture_dir,
        specs,
        runner=runner,
    )
    raw = assembler.load_raw_capture_set(capture_dir, specs)
    documents = assembler.validate_complete_chain(seed, contract, manifest, raw, bindings)
    assembler.validate_sealed_binding_outputs(capture_dir, documents)
    assembler._write_outputs(
        capture_dir,
        {
            key: value
            for key, value in documents.items()
            if key not in {"bindings", "bindings.result"}
        },
    )
    return {
        "capture_id": capture_id,
        "status": "PASS",
        "summary_sha256": assembler._canonical_sha256(documents["summary"]),
        "attestation_sha256": assembler._canonical_sha256(documents["execution-attestation"]),
    }


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _fail("invalid-cli-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description="Run one frozen Gate B acquisition")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in ("plan", "capture"):
        command = subparsers.add_parser(mode)
        command.add_argument("--seed", default=str(assembler.SEED_PATH))
        command.add_argument("--contract", default=str(assembler.CONTRACT_PATH))
        if mode == "capture":
            command.add_argument("--capture-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        seed_path = assembler._require_tracked_path(
            args.seed, assembler.SEED_PATH, "untracked-seed-path"
        )
        contract_path = assembler._require_tracked_path(
            args.contract, assembler.CONTRACT_PATH, "untracked-contract-path"
        )
        seed = assembler.load_seed(seed_path)
        contract = assembler.load_execution_contract(contract_path)
        manifest = assembler.load_manifest()
        assembler.validate_execution_contract(contract, seed, manifest)
        if args.mode == "plan":
            bindings = _provisional_bindings(seed, manifest, "20260808T000000Z-000000000000")
            specs = assembler.complete_operation_specs(contract, manifest, bindings)
            result = {
                "contract_id": contract["contract_id"],
                "accepted_execution_binding": contract["accepted_execution_binding"],
                "seed_file_sha256": assembler.EXPECTED_SEED_FILE_SHA256,
                "seed_canonical_sha256": assembler.EXPECTED_SEED_CANONICAL_SHA256,
                "execution_contract_file_sha256": (assembler.EXPECTED_CONTRACT_FILE_SHA256),
                "execution_contract_canonical_sha256": (
                    assembler.EXPECTED_CONTRACT_CANONICAL_SHA256
                ),
                "manifest_sha256": contract["evidence"]["manifest_canonical_sha256"],
                "validator_file_sha256": contract["evidence"]["validator_file_sha256"],
                "tool_contract_sha256": assembler._canonical_sha256(contract["tools"]),
                "provider_operation_count": len(specs),
                "aws_operation_count": sum(item["provider"] == "aws" for item in specs),
                "github_operation_count": sum(item["provider"] == "github" for item in specs),
                "execution_graph_sha256": assembler.execution_graph_sha256(specs),
                "status": "PASS",
            }
        else:
            result = run_gate_b(args.capture_id, seed, contract, manifest)
        sys.stdout.buffer.write(evidence.canonical_json_bytes(result) + b"\n")
        return 0
    except OperatorError as error:
        sys.stderr.write(_operator_stop_line(error))
        return 1
    except (assembler.AssemblyError, evidence.EvidenceError, KeyboardInterrupt):
        sys.stderr.write(assembler.OPERATOR_STOP_GENERIC_LINE)
        return 1
    except Exception:
        sys.stderr.write(assembler.OPERATOR_STOP_GENERIC_LINE)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
