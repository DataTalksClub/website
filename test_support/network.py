from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, cast
from unittest.mock import patch

NETWORK_GUARD_ENV = "DTC_TEST_NETWORK_DENY"
_NETWORK_EXECUTABLES = frozenset(
    {"aws", "curl", "gh", "http", "httpie", "nc", "netcat", "ssh", "wget"}
)
_SHELL_EXECUTABLES = frozenset({"bash", "dash", "ksh", "sh", "zsh"})
_GIT_NETWORK_COMMANDS = frozenset(
    {
        "fetch",
        "http-fetch",
        "http-push",
        "imap-send",
        "ls-remote",
        "pull",
        "push",
        "receive-pack",
        "send-pack",
        "upload-archive",
        "upload-pack",
    }
)
_PYTHON_GUARD_SUPPRESSING_FLAGS = frozenset({"-E", "-I", "-S"})
_SHELL_NETWORK_RE = re.compile(
    r"(?:^|[\s;&|()])(?:[^\s;&|()]*/)?"
    r"(?:aws|curl|gh|http|httpie|nc|netcat|ssh|wget)(?:$|[\s;&|()])"
)
_SHELL_GIT_NETWORK_RE = re.compile(
    r"(?:^|[\s;&|()])(?:[^\s;&|()]*/)?git(?:\s+-(?:C|c)\s+\S+)*\s+"
    r"(?:clone|fetch|http-fetch|http-push|imap-send|ls-remote|pull|push|receive-pack|"
    r"send-pack|upload-archive|upload-pack|remote\s+(?:prune|show|update)|"
    r"submodule\s+update)(?:$|[\s;&|()])"
)
_SHELL_PYTHON_RE = re.compile(r"(?:^|[\s;&|()])(?:[^\s;&|()]*/)?python(?:[0-9.]+)?(?:$|[\s;&|()])")
_SHELL_GUARD_ENV_RE = re.compile(
    rf"(?:^|[\s;&|()])(?:unset|export)\s+[^;&|()]*"
    rf"(?:{NETWORK_GUARD_ENV}|PYTHONHOME|PYTHONPATH)(?:$|[\s;&|()=])"
    rf"|(?:^|[\s;&|()])(?:{NETWORK_GUARD_ENV}|PYTHONHOME|PYTHONPATH)="
)


class ExternalNetworkDenied(RuntimeError):
    """Ordinary tests may not access an external network or provider."""


def _loopback_host(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if value.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


class NetworkGuard:
    def __init__(self, *, allow_loopback: bool = True) -> None:
        self.allow_loopback = allow_loopback
        self._patches: list[Any] = []
        self._old_environment: str | None = None

    def __enter__(self) -> NetworkGuard:
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_send = socket.socket.send
        original_sendall = socket.socket.sendall
        original_sendto = socket.socket.sendto
        original_sendmsg = getattr(socket.socket, "sendmsg", None)
        original_sendfile = getattr(socket.socket, "sendfile", None)
        original_getaddrinfo = socket.getaddrinfo
        original_gethostbyname = socket.gethostbyname
        original_gethostbyname_ex = socket.gethostbyname_ex
        original_gethostbyaddr = socket.gethostbyaddr
        original_getnameinfo = socket.getnameinfo
        original_popen = subprocess.Popen
        original_posix_spawn = getattr(os, "posix_spawn", None)
        original_posix_spawnp = getattr(os, "posix_spawnp", None)

        def guarded_connect(sock: socket.socket, address: object) -> Any:
            if sock.family == socket.AF_UNIX:
                return original_connect(sock, address)  # type: ignore[arg-type]
            host = address[0] if isinstance(address, tuple) and address else None
            if self.allow_loopback and _loopback_host(host):
                return original_connect(sock, address)  # type: ignore[arg-type]
            raise ExternalNetworkDenied("external socket connection denied by test runtime")

        def guarded_connect_ex(sock: socket.socket, address: object) -> int:
            if sock.family == socket.AF_UNIX:
                return original_connect_ex(sock, address)  # type: ignore[arg-type]
            host = address[0] if isinstance(address, tuple) and address else None
            if self.allow_loopback and _loopback_host(host):
                return original_connect_ex(sock, address)  # type: ignore[arg-type]
            raise ExternalNetworkDenied("external socket connection denied by test runtime")

        def guarded_send(sock: socket.socket, data: bytes, *args: Any) -> int:
            _assert_connected_socket_allowed(sock, allow_loopback=self.allow_loopback)
            return original_send(sock, data, *args)

        def guarded_sendall(sock: socket.socket, data: bytes, *args: Any) -> None:
            _assert_connected_socket_allowed(sock, allow_loopback=self.allow_loopback)
            return original_sendall(sock, data, *args)

        def guarded_sendto(sock: socket.socket, data: bytes, *args: Any) -> int:
            address = args[-1] if args else None
            _assert_socket_address_allowed(
                sock,
                address,
                allow_loopback=self.allow_loopback,
            )
            return original_sendto(sock, data, *args)

        def guarded_sendmsg(sock: socket.socket, buffers: Any, *args: Any) -> int:
            address = args[2] if len(args) >= 3 else None
            if address is None:
                _assert_connected_socket_allowed(sock, allow_loopback=self.allow_loopback)
            else:
                _assert_socket_address_allowed(
                    sock,
                    address,
                    allow_loopback=self.allow_loopback,
                )
            assert original_sendmsg is not None
            return original_sendmsg(sock, buffers, *args)

        def guarded_sendfile(sock: socket.socket, file: Any, *args: Any, **kwargs: Any) -> int:
            _assert_connected_socket_allowed(sock, allow_loopback=self.allow_loopback)
            assert original_sendfile is not None
            return original_sendfile(sock, file, *args, **kwargs)

        def guarded_getaddrinfo(host: object, *args: Any, **kwargs: Any) -> Any:
            if self.allow_loopback and isinstance(host, str) and _loopback_host(host):
                return original_getaddrinfo(host, *args, **kwargs)
            raise ExternalNetworkDenied("external DNS resolution denied by test runtime")

        def guarded_gethostbyname(host: str) -> str:
            if self.allow_loopback and _loopback_host(host):
                return original_gethostbyname(host)
            raise ExternalNetworkDenied("external DNS resolution denied by test runtime")

        def guarded_gethostbyname_ex(host: str) -> Any:
            if self.allow_loopback and _loopback_host(host):
                return original_gethostbyname_ex(host)
            raise ExternalNetworkDenied("external DNS resolution denied by test runtime")

        def guarded_gethostbyaddr(host: str) -> Any:
            if self.allow_loopback and _loopback_host(host):
                return original_gethostbyaddr(host)
            raise ExternalNetworkDenied("external reverse DNS resolution denied by test runtime")

        def guarded_getnameinfo(address: object, flags: int) -> Any:
            host = address[0] if isinstance(address, tuple) and address else None
            if self.allow_loopback and _loopback_host(host):
                return original_getnameinfo(address, flags)  # type: ignore[arg-type]
            raise ExternalNetworkDenied("external reverse DNS resolution denied by test runtime")

        def guarded_popen(args: Any, *popen_args: Any, **popen_kwargs: Any) -> Any:
            _assert_guarded_subprocess(
                args,
                executable=popen_kwargs.get("executable"),
                shell=bool(popen_kwargs.get("shell")),
            )
            popen_kwargs["env"] = _guarded_environment(popen_kwargs.get("env"))
            return original_popen(args, *popen_args, **popen_kwargs)

        def guarded_posix_spawn(
            path: str | bytes,
            argv: Sequence[str | bytes],
            env: dict[str | bytes, str | bytes],
            *args: Any,
            **kwargs: Any,
        ) -> int:
            _assert_guarded_subprocess(argv, executable=path)
            assert original_posix_spawn is not None
            return original_posix_spawn(
                path,
                argv,
                _guarded_environment(env),
                *args,
                **kwargs,
            )

        def guarded_posix_spawnp(
            path: str | bytes,
            argv: Sequence[str | bytes],
            env: dict[str | bytes, str | bytes],
            *args: Any,
            **kwargs: Any,
        ) -> int:
            _assert_guarded_subprocess(argv, executable=path)
            assert original_posix_spawnp is not None
            return original_posix_spawnp(
                path,
                argv,
                _guarded_environment(env),
                *args,
                **kwargs,
            )

        self._old_environment = os.environ.get(NETWORK_GUARD_ENV)
        os.environ[NETWORK_GUARD_ENV] = "1"
        self._patches = [
            patch.object(socket.socket, "connect", guarded_connect),
            patch.object(socket.socket, "connect_ex", guarded_connect_ex),
            patch.object(socket.socket, "send", guarded_send),
            patch.object(socket.socket, "sendall", guarded_sendall),
            patch.object(socket.socket, "sendto", guarded_sendto),
            patch.object(socket, "getaddrinfo", guarded_getaddrinfo),
            patch.object(socket, "gethostbyname", guarded_gethostbyname),
            patch.object(socket, "gethostbyname_ex", guarded_gethostbyname_ex),
            patch.object(socket, "gethostbyaddr", guarded_gethostbyaddr),
            patch.object(socket, "getnameinfo", guarded_getnameinfo),
            patch.object(subprocess, "Popen", guarded_popen),
        ]
        if original_sendmsg is not None:
            self._patches.append(patch.object(socket.socket, "sendmsg", guarded_sendmsg))
        if original_sendfile is not None:
            self._patches.append(patch.object(socket.socket, "sendfile", guarded_sendfile))
        if original_posix_spawn is not None:
            self._patches.append(patch.object(os, "posix_spawn", guarded_posix_spawn))
        if original_posix_spawnp is not None:
            self._patches.append(patch.object(os, "posix_spawnp", guarded_posix_spawnp))
        for active_patch in self._patches:
            active_patch.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        for active_patch in reversed(self._patches):
            active_patch.stop()
        self._patches.clear()
        if self._old_environment is None:
            os.environ.pop(NETWORK_GUARD_ENV, None)
        else:
            os.environ[NETWORK_GUARD_ENV] = self._old_environment


def _assert_socket_address_allowed(
    sock: socket.socket,
    address: object,
    *,
    allow_loopback: bool,
) -> None:
    if sock.family == socket.AF_UNIX:
        return
    host = address[0] if isinstance(address, tuple) and address else None
    if allow_loopback and _loopback_host(host):
        return
    raise ExternalNetworkDenied("external socket send denied by test runtime")


def _assert_connected_socket_allowed(sock: socket.socket, *, allow_loopback: bool) -> None:
    if sock.family == socket.AF_UNIX:
        return
    try:
        address = sock.getpeername()
    except OSError:
        return
    _assert_socket_address_allowed(sock, address, allow_loopback=allow_loopback)


def _guarded_environment(
    supplied: Mapping[str | bytes, str | bytes] | None,
) -> dict[str | bytes, str | bytes]:
    source = supplied if supplied is not None else os.environ
    environment = dict(source)
    uses_bytes = any(isinstance(key, bytes) for key in environment)
    if uses_bytes:
        byte_environment = cast(dict[bytes, bytes], environment)
        guard_value: str | bytes | None = byte_environment.get(os.fsencode(NETWORK_GUARD_ENV))
    else:
        text_environment = cast(dict[str, str], environment)
        guard_value = text_environment.get(NETWORK_GUARD_ENV)
    if guard_value not in {None, "1", b"1"}:
        raise ExternalNetworkDenied("subprocess cannot remove the test network guard")
    if uses_bytes:
        repository_bytes = os.fsencode(Path(__file__).resolve().parents[1])
        byte_environment[b"DTC_TEST_NETWORK_DENY"] = b"1"
        byte_entries = [
            entry
            for entry in byte_environment.get(b"PYTHONPATH", b"").split(os.fsencode(os.pathsep))
            if entry
        ]
        if repository_bytes not in byte_entries:
            byte_environment[b"PYTHONPATH"] = os.fsencode(os.pathsep).join(
                (repository_bytes, *byte_entries)
            )
    else:
        repository_text = os.fspath(Path(__file__).resolve().parents[1])
        text_environment[NETWORK_GUARD_ENV] = "1"
        text_entries = [
            entry for entry in text_environment.get("PYTHONPATH", "").split(os.pathsep) if entry
        ]
        if repository_text not in text_entries:
            text_environment["PYTHONPATH"] = os.pathsep.join((repository_text, *text_entries))
    return cast(dict[str | bytes, str | bytes], environment)


def _subprocess_executable(args: object) -> str:
    if isinstance(args, (str, bytes)):
        first = os.fsdecode(args).strip().split(maxsplit=1)[0]
    elif isinstance(args, Sequence) and args:
        first = os.fsdecode(args[0])
    else:
        return ""
    return Path(first).name.casefold()


def _subprocess_argv(args: object) -> tuple[str, ...]:
    if isinstance(args, (str, bytes)):
        return (os.fsdecode(args),)
    if isinstance(args, Sequence):
        return tuple(os.fsdecode(value) for value in args)
    return ()


def _assert_guarded_subprocess(
    args: object,
    *,
    executable: object = None,
    shell: bool = False,
) -> None:
    argv = _subprocess_argv(args)
    selected_executable = (
        Path(os.fsdecode(executable)).name.casefold()
        if isinstance(executable, (str, bytes))
        else _subprocess_executable(args)
    )
    if selected_executable in _NETWORK_EXECUTABLES:
        raise ExternalNetworkDenied("network-capable subprocess denied by test runtime")
    if shell:
        command = argv[0] if len(argv) == 1 else " ".join(argv)
        _assert_guarded_shell_command(command)
        return
    if not argv:
        return
    if selected_executable == "env":
        nested = _env_nested_argv(argv[1:])
        _assert_guarded_subprocess(nested)
        return
    if selected_executable in _SHELL_EXECUTABLES:
        shell_command = _shell_command(argv[1:])
        if shell_command is not None:
            _assert_guarded_shell_command(shell_command)
        return
    if selected_executable == "git":
        _assert_guarded_git(argv[1:])
        return
    if selected_executable.startswith("python"):
        _assert_guarded_python(argv[1:])


def _env_nested_argv(argv: Sequence[str]) -> tuple[str, ...]:
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--":
            return tuple(argv[index + 1 :])
        if value in {"-i", "--ignore-environment"}:
            raise ExternalNetworkDenied(
                "environment wrapper cannot suppress the test network guard"
            )
        if value in {"-u", "--unset"}:
            if index + 1 >= len(argv):
                return ()
            if argv[index + 1] in {NETWORK_GUARD_ENV, "PYTHONHOME", "PYTHONPATH"}:
                raise ExternalNetworkDenied(
                    "environment wrapper cannot suppress the test network guard"
                )
            index += 2
            continue
        if value.startswith("--unset="):
            if value.partition("=")[2] in {NETWORK_GUARD_ENV, "PYTHONHOME", "PYTHONPATH"}:
                raise ExternalNetworkDenied(
                    "environment wrapper cannot suppress the test network guard"
                )
            index += 1
            continue
        if value.startswith("-"):
            index += 1
            continue
        if "=" in value:
            name = value.partition("=")[0]
            if name in {NETWORK_GUARD_ENV, "PYTHONHOME", "PYTHONPATH"}:
                raise ExternalNetworkDenied(
                    "environment wrapper cannot suppress the test network guard"
                )
            index += 1
            continue
        return tuple(argv[index:])
    return ()


def _shell_command(argv: Sequence[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "-c" and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith("-") and "c" in value[1:] and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _assert_guarded_shell_command(command: str) -> None:
    normalized = command.casefold()
    if (
        _SHELL_NETWORK_RE.search(normalized)
        or _SHELL_GIT_NETWORK_RE.search(normalized)
        or _SHELL_PYTHON_RE.search(command)
        or _SHELL_GUARD_ENV_RE.search(command)
        or "/dev/tcp/" in normalized
        or "/dev/udp/" in normalized
        or re.search(
            rf"(?:^|[\s;&|()])env\s+(?:[^;&|()]*\s)?"
            rf"(?:-i|--ignore-environment|-u\s+(?:{NETWORK_GUARD_ENV}|PYTHONHOME|PYTHONPATH)|"
            rf"(?:{NETWORK_GUARD_ENV}|PYTHONHOME|PYTHONPATH)=)",
            command,
        )
    ):
        raise ExternalNetworkDenied("shell-wrapped network subprocess denied by test runtime")


def _assert_guarded_git(argv: Sequence[str]) -> None:
    command = _git_command(argv)
    if command is None:
        return
    name, remainder = command
    if name in _GIT_NETWORK_COMMANDS:
        raise ExternalNetworkDenied("Git network operation denied by test runtime")
    if name == "archive" and any(value.startswith("--remote") for value in remainder):
        raise ExternalNetworkDenied("Git network operation denied by test runtime")
    if name == "clone":
        source = _git_clone_source(remainder)
        if source is not None and _looks_like_network_remote(source):
            raise ExternalNetworkDenied("Git remote clone denied by test runtime")
    if name == "submodule" and "update" in remainder:
        raise ExternalNetworkDenied("Git submodule network operation denied by test runtime")
    if name == "remote" and _git_remote_requires_network(remainder):
        raise ExternalNetworkDenied("Git remote network operation denied by test runtime")


def _git_remote_requires_network(argv: Sequence[str]) -> bool:
    command = next((value.casefold() for value in argv if not value.startswith("-")), None)
    if command in {"prune", "update"}:
        return True
    if command == "show":
        return "-n" not in argv and "--no-query" not in argv
    if command == "set-head":
        return "-a" in argv or "--auto" in argv
    return False


def _git_command(argv: Sequence[str]) -> tuple[str, tuple[str, ...]] | None:
    options_with_value = {"-C", "-c", "--git-dir", "--namespace", "--work-tree"}
    index = 0
    while index < len(argv):
        value = argv[index]
        if value in options_with_value:
            index += 2
            continue
        if any(value.startswith(f"{option}=") for option in options_with_value if option != "-C"):
            index += 1
            continue
        if value.startswith("-"):
            index += 1
            continue
        return value.casefold(), tuple(argv[index + 1 :])
    return None


def _git_clone_source(argv: Sequence[str]) -> str | None:
    options_with_value = {"--branch", "--config", "--depth", "--filter", "--jobs", "--origin"}
    index = 0
    while index < len(argv):
        value = argv[index]
        if value in options_with_value or value in {"-b", "-c", "-j", "-o"}:
            index += 2
            continue
        if value.startswith("-"):
            index += 1
            continue
        return value
    return None


def _looks_like_network_remote(value: str) -> bool:
    lowered = value.casefold()
    if lowered.startswith(("git://", "http://", "https://", "ssh://")):
        return True
    return bool(re.fullmatch(r"(?:[^/@:]+@)?[^/:]+:.+", value))


def _assert_guarded_python(argv: Sequence[str]) -> None:
    if any(
        value in _PYTHON_GUARD_SUPPRESSING_FLAGS
        or (value.startswith("-") and any(flag in value[1:] for flag in "EIS"))
        for value in argv
    ):
        raise ExternalNetworkDenied("Python subprocess cannot suppress the test network guard")


_PROCESS_GUARD: NetworkGuard | None = None


def install_process_network_guard() -> None:
    global _PROCESS_GUARD
    if _PROCESS_GUARD is None:
        _PROCESS_GUARD = NetworkGuard()
        _PROCESS_GUARD.__enter__()
