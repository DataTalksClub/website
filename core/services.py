"""Shared application-service conventions for every presentation adapter.

Commands own business mutations and their database transaction. They persist durable work in the
same transaction and arrange dispatch with ``transaction.on_commit``; they never perform network
I/O inside that transaction. Queries are side-effect free. Studio, API, HTML views, jobs, and tests
call these protocols directly rather than duplicating business rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from core.context import (
    current_context,
    new_context_id,
    validate_context_id,
)
from core.redaction import is_sensitive_text

CommandT = TypeVar("CommandT", contravariant=True)
QueryT = TypeVar("QueryT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)
_ACTOR_REF_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._-]{0,94}")


def validate_actor_ref(value: object) -> str:
    """Validate a non-secret attribution reference; it never grants authorization."""

    if (
        not isinstance(value, str)
        or _ACTOR_REF_PATTERN.fullmatch(value) is None
        or is_sensitive_text(value)
    ):
        raise ValueError("Invalid actor_ref") from None
    return value


@dataclass(frozen=True, slots=True)
class ServiceContext:
    """Non-secret execution metadata propagated across adapters and durable jobs."""

    correlation_id: str
    request_id: str | None = None
    job_id: str | None = None
    actor_ref: str | None = None
    idempotency_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        validate_context_id("correlation_id", self.correlation_id)
        if self.request_id is not None:
            validate_context_id("request_id", self.request_id)
        if self.job_id is not None:
            validate_context_id("job_id", self.job_id)
        if self.actor_ref is not None:
            validate_actor_ref(self.actor_ref)
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str) or not 1 <= len(self.idempotency_key) <= 128
        ):
            raise ValueError("Invalid idempotency_key")

    @classmethod
    def from_current(
        cls,
        *,
        actor_ref: str | None = None,
        idempotency_key: str | None = None,
    ) -> ServiceContext:
        snapshot = current_context()
        return cls(
            request_id=snapshot.request_id,
            correlation_id=snapshot.correlation_id or snapshot.request_id or new_context_id(),
            job_id=snapshot.job_id,
            actor_ref=actor_ref,
            idempotency_key=idempotency_key,
        )


class CommandService(Protocol[CommandT, ResultT]):
    """A mutation boundary shared by Studio, API, HTML, jobs, and tests."""

    def __call__(self, command: CommandT, *, context: ServiceContext) -> ResultT: ...


class QueryService(Protocol[QueryT, ResultT]):
    """A side-effect-free read boundary shared by all presentation adapters."""

    def __call__(self, query: QueryT, *, context: ServiceContext) -> ResultT: ...
