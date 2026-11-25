"""Deny ordinary tests access to machine-local production data paths (e.g. ~/prod)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import Any

LOCAL_PATH_GUARD_ENV = "DTC_TEST_LOCAL_PATH_DENY"
_AUDITED_PATH_EVENTS = frozenset({"open", "os.listdir", "os.scandir", "glob.glob"})
_ACTIVE_GUARDS = 0
_HOOK_INSTALLED = False
_CAPTURED_FORBIDDEN_ROOTS: tuple[Path, ...] = ()


class MachineLocalPathDenied(RuntimeError):
    """Ordinary tests may not read ~/prod or other machine-local data directories."""


def _prod_roots_for(home: Path) -> tuple[Path, ...]:
    prod_root = home / "prod"
    try:
        resolved = prod_root.resolve()
    except OSError:
        resolved = prod_root
    if resolved != prod_root:
        return (prod_root, resolved)
    return (prod_root,)


def _forbidden_roots() -> tuple[Path, ...]:
    roots = list(_CAPTURED_FORBIDDEN_ROOTS)
    for root in _prod_roots_for(Path.home()):
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _path_is_forbidden(raw_path: object) -> Path | None:
    if not isinstance(raw_path, (str, bytes, os.PathLike)):
        return None
    try:
        text = os.fsdecode(raw_path)
    except (TypeError, ValueError):
        return None
    if not text or "prod" not in text:
        return None
    candidate = Path(text)
    resolved: Path | None = None
    for root in _forbidden_roots():
        if candidate.is_absolute() and candidate.is_relative_to(root):
            return candidate
        if resolved is None:
            try:
                resolved = candidate.resolve()
            except OSError:
                return None
        if resolved.is_relative_to(root):
            return resolved
    return None


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    if _ACTIVE_GUARDS <= 0 or event not in _AUDITED_PATH_EVENTS or not args:
        return
    denied = _path_is_forbidden(args[0])
    if denied is not None:
        raise MachineLocalPathDenied(
            f"test attempted to access machine-local path under ~/prod: {denied}"
        )


def _ensure_audit_hook_installed() -> None:
    global _HOOK_INSTALLED
    if not _HOOK_INSTALLED:
        sys.addaudithook(_audit_hook)
        _HOOK_INSTALLED = True


class LocalPathGuard:
    """Context manager that blocks filesystem access under ~/prod during test runs."""

    def __init__(self) -> None:
        self._entered = False
        self._old_environment: str | None = None

    def __enter__(self) -> LocalPathGuard:
        global _ACTIVE_GUARDS, _CAPTURED_FORBIDDEN_ROOTS
        if not _CAPTURED_FORBIDDEN_ROOTS:
            _CAPTURED_FORBIDDEN_ROOTS = _prod_roots_for(Path.home())
        _ensure_audit_hook_installed()
        self._old_environment = os.environ.get(LOCAL_PATH_GUARD_ENV)
        os.environ[LOCAL_PATH_GUARD_ENV] = "1"
        _ACTIVE_GUARDS += 1
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        global _ACTIVE_GUARDS
        if not self._entered:
            return
        self._entered = False
        _ACTIVE_GUARDS = max(0, _ACTIVE_GUARDS - 1)
        if self._old_environment is None:
            os.environ.pop(LOCAL_PATH_GUARD_ENV, None)
        else:
            os.environ[LOCAL_PATH_GUARD_ENV] = self._old_environment


_PROCESS_GUARD: LocalPathGuard | None = None


def install_process_local_path_guard() -> None:
    global _PROCESS_GUARD
    if _PROCESS_GUARD is None:
        _PROCESS_GUARD = LocalPathGuard()
        _PROCESS_GUARD.__enter__()
