"""Request and job correlation context that is safe across concurrent tasks."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import NoReturn, TypeGuard

from core.redaction import is_sensitive_text

CONTEXT_ID_MAX_LENGTH = 128
CONTEXT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)


class ContextIdError(ValueError):
    """A value-free error for invalid context identifiers."""


def _fail(field: str, reason: str = "invalid") -> NoReturn:
    if field not in {"request_id", "correlation_id", "job_id"}:
        field = "context_id"
    raise ContextIdError(f"Invalid {field}: {reason}")


def is_valid_context_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and CONTEXT_ID_PATTERN.fullmatch(value) is not None


def is_safe_external_context_id(value: object) -> TypeGuard[str]:
    """Accept only bounded identifiers that do not resemble protected values."""

    return is_valid_context_id(value) and not is_sensitive_text(value)


def validate_context_id(field: str, value: object) -> str:
    if not is_valid_context_id(value):
        _fail(field)
    return value


def new_context_id() -> str:
    return uuid.uuid4().hex


def external_context_id_or_new(value: object) -> str:
    """Accept a bounded untrusted header value or replace it with an opaque ID."""

    return value if is_safe_external_context_id(value) else new_context_id()


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Immutable execution identifiers safe to attach to an audit record."""

    request_id: str | None
    correlation_id: str | None
    job_id: str | None


# Backward-neutral descriptive alias for callers that treat this as a context snapshot.
ContextSnapshot = AuditContext


@dataclass(frozen=True, slots=True)
class ContextTokens:
    request_id: Token[str | None]
    correlation_id: Token[str | None]
    job_id: Token[str | None]


def bind_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    job_id: str | None = None,
) -> ContextTokens:
    """Bind validated IDs and return tokens that must be reset in ``finally``."""

    if request_id is not None:
        request_id = validate_context_id("request_id", request_id)
    if correlation_id is not None:
        correlation_id = validate_context_id("correlation_id", correlation_id)
    if job_id is not None:
        job_id = validate_context_id("job_id", job_id)
    return ContextTokens(
        request_id=_request_id.set(request_id),
        correlation_id=_correlation_id.set(correlation_id),
        job_id=_job_id.set(job_id),
    )


def reset_context(tokens: ContextTokens) -> None:
    """Restore the exact enclosing context in reverse binding order."""

    _job_id.reset(tokens.job_id)
    _correlation_id.reset(tokens.correlation_id)
    _request_id.reset(tokens.request_id)


@contextmanager
def context_scope(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    job_id: str | None = None,
) -> Iterator[ContextSnapshot]:
    tokens = bind_context(
        request_id=request_id,
        correlation_id=correlation_id,
        job_id=job_id,
    )
    try:
        yield current_context()
    finally:
        reset_context(tokens)


def current_context() -> AuditContext:
    return AuditContext(
        request_id=_request_id.get(),
        correlation_id=_correlation_id.get(),
        job_id=_job_id.get(),
    )


def current_request_id() -> str | None:
    return _request_id.get()


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def current_job_id() -> str | None:
    return _job_id.get()
