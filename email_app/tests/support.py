"""A stub for the pooled Relay session, so tests exercise the real bridge code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass(frozen=True, slots=True)
class RecordedCall:
    method: str
    url: str
    params: dict[str, str] | None
    data: dict[str, str] | None
    timeout: float
    allow_redirects: bool


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


@dataclass
class FakeRelay:
    """Answers exactly like Relay, or refuses to answer at all."""

    status_code: int | None = 200
    error: Exception | None = None
    calls: list[RecordedCall] = field(default_factory=list)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(
            RecordedCall(
                method=method,
                url=url,
                params=kwargs.get("params"),
                data=kwargs.get("data"),
                timeout=kwargs.get("timeout", 0.0),
                allow_redirects=bool(kwargs.get("allow_redirects", True)),
            )
        )
        if self.error is not None:
            raise self.error
        assert self.status_code is not None
        return FakeResponse(self.status_code)

    @property
    def called(self) -> bool:
        return bool(self.calls)


def unreachable_relay() -> FakeRelay:
    return FakeRelay(status_code=None, error=requests.ConnectionError("connection refused"))


def timing_out_relay() -> FakeRelay:
    return FakeRelay(status_code=None, error=requests.Timeout("read timed out"))
