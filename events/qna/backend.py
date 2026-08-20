"""Backend seam for event-linked Q&A provisioning.

DataQnA's proven room behavior is the compatibility reference, but the website
must own the Event identity and public host.  The protocol keeps future storage
or server-side provider adapters behind the same service boundary.  The native
adapter intentionally has no network side effect.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BackendSession:
    """Safe result of ensuring a backend session."""

    backend_reference: str = ""


class QnaBackend(Protocol):
    """Server-side adapter used by the Event-owned Q&A service."""

    key: str

    def ensure_session(
        self,
        *,
        event_id: uuid.UUID,
        session_id: uuid.UUID,
        idempotency_key: str,
    ) -> BackendSession:
        """Converge one backend session without exposing provider details."""


class NativeQnaBackend:
    """The initial Django-native backend.

    The relation is created in the Event transaction.  The worker calls this
    adapter after commit so a later provider adapter can be introduced without
    changing the Event service or its durable-job contract.
    """

    key = "native"

    def ensure_session(
        self,
        *,
        event_id: uuid.UUID,
        session_id: uuid.UUID,
        idempotency_key: str,
    ) -> BackendSession:
        del event_id, session_id, idempotency_key
        return BackendSession()


_BACKENDS: dict[str, QnaBackend] = {NativeQnaBackend.key: NativeQnaBackend()}


def get_qna_backend(key: str) -> QnaBackend:
    try:
        return _BACKENDS[key]
    except KeyError as exc:
        raise ValueError("qna_backend_unavailable") from exc
