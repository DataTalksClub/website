from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from community_base.kernel.idempotency import (
    _MAX_IDEMPOTENCY_KEY_BYTES,
    JsonObject,
    JsonPrimitive,
    JsonValue,
    UnsafeJsonValue,
    _fenced_hash,
    _validate_identifier,
    canonical_json,
    canonical_json_bytes,
    canonical_json_object,
)
from django.db import transaction
from django.utils import timezone

from core.models import IdempotencyRecord

__all__ = [
    "IdempotencyConflict",
    "IdempotencyFenceLost",
    "IdempotencyInProgress",
    "IdempotencyResult",
    "JsonObject",
    "JsonPrimitive",
    "JsonValue",
    "UnsafeJsonValue",
    "_fenced_hash",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_json_object",
    "execute_idempotent",
    "hash_idempotency_key",
    "hash_idempotency_request",
]


class IdempotencyConflict(RuntimeError):
    """The same scoped key was already used for a different request."""


class IdempotencyInProgress(RuntimeError):
    """An invalid committed in-progress record needs operator reconciliation."""


class IdempotencyFenceLost(RuntimeError):
    """The command no longer owns its completion record."""


@dataclass(frozen=True, slots=True)
class IdempotencyResult:
    value: JsonObject
    replayed: bool
    record_id: uuid.UUID


def hash_idempotency_key(scope: str, raw_key: str) -> str:
    scope = _validate_identifier(scope, name="scope")
    key_bytes = raw_key.encode()
    if not key_bytes or len(key_bytes) > _MAX_IDEMPOTENCY_KEY_BYTES:
        raise ValueError("idempotency key must contain between 1 and 512 UTF-8 bytes")
    return _fenced_hash(b"dtc:idempotency-key:v1\0", scope.encode(), key_bytes)


def hash_idempotency_request(scope: str, payload: Any) -> str:
    scope = _validate_identifier(scope, name="scope")
    return _fenced_hash(
        b"dtc:idempotency-request:v1\0",
        scope.encode(),
        canonical_json_bytes(payload),
    )


def execute_idempotent(
    *,
    scope: str,
    key: str,
    request: JsonObject,
    command: Callable[[], JsonObject],
    using: str = "default",
) -> IdempotencyResult:
    """Execute a command and persist its replay result in one database transaction.

    The unique scope/key constraint arbitrates absent-row races on every supported
    database. The in-progress row, domain writes, and fenced completion commit
    together, so an owner crash or an enclosing rollback cannot strand a
    half-completed command.
    """

    scope = _validate_identifier(scope, name="scope")
    key_hash = hash_idempotency_key(scope, key)
    request_hash = hash_idempotency_request(scope, request)
    owner_token = uuid.uuid4()

    with transaction.atomic(using=using):
        record, created = IdempotencyRecord.objects.using(using).get_or_create(
            scope=scope,
            key_hash=key_hash,
            defaults={
                "request_hash": request_hash,
                "owner_token": owner_token,
            },
        )
        if not created:
            if record.request_hash != request_hash:
                raise IdempotencyConflict(
                    f"idempotency key conflicts with an earlier request in scope {scope}"
                )
            if record.status == IdempotencyRecord.Status.COMPLETED:
                return IdempotencyResult(
                    value=canonical_json_object(record.result),
                    replayed=True,
                    record_id=record.id,
                )
            raise IdempotencyInProgress(
                f"scope {scope} contains an invalid committed in-progress record"
            )
        result = canonical_json_object(command())
        completed_at = timezone.now()
        updated = (
            IdempotencyRecord.objects.using(using)
            .filter(
                pk=record.pk,
                owner_token=owner_token,
                status=IdempotencyRecord.Status.IN_PROGRESS,
            )
            .update(
                status=IdempotencyRecord.Status.COMPLETED,
                result=result,
                completed_at=completed_at,
            )
        )
        if updated != 1:
            raise IdempotencyFenceLost("idempotency completion ownership was lost")
        return IdempotencyResult(
            value=cast(JsonObject, canonical_json(result)),
            replayed=False,
            record_id=record.id,
        )
