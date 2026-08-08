from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_MODE: ContextVar[str] = ContextVar("management_fixture_policy_mode", default="unresolved")


def policy_mode() -> str:
    return _MODE.get()


@contextmanager
def fixture_policy(mode: str) -> Iterator[None]:
    if mode not in {"allowed", "absent", "unresolved", "error", "stale", "mismatch", "cancelled"}:
        raise ValueError("unknown fixture policy mode")
    token = _MODE.set(mode)
    try:
        yield
    finally:
        _MODE.reset(token)
