from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from django.db import transaction
from django.utils import timezone

from core.models import IdempotencyRecord

type JsonPrimitive = None | bool | int | float | str
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_MAX_JSON_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 12
_MAX_JSON_NODES = 2_000
_MAX_IDEMPOTENCY_KEY_BYTES = 512


class UnsafeJsonValue(ValueError):
    """Raised when persisted command data is not bounded canonical JSON."""


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


def _normalize_json(value: Any, *, depth: int, budget: list[int]) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise UnsafeJsonValue("JSON exceeds the maximum nesting depth")
    budget[0] -= 1
    if budget[0] < 0:
        raise UnsafeJsonValue("JSON exceeds the maximum item count")

    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsafeJsonValue("JSON numbers must be finite")
        return value
    if isinstance(value, list):
        return [_normalize_json(item, depth=depth + 1, budget=budget) for item in value]
    if isinstance(value, dict):
        normalized: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise UnsafeJsonValue("JSON object keys must be strings")
            if len(key) > 128:
                raise UnsafeJsonValue("JSON object keys must not exceed 128 characters")
            normalized[key] = _normalize_json(item, depth=depth + 1, budget=budget)
        return normalized
    raise UnsafeJsonValue(f"unsupported JSON value type: {type(value).__name__}")


def canonical_json(value: Any) -> JsonValue:
    """Return a bounded deep copy containing only canonical JSON value types."""

    normalized = _normalize_json(value, depth=0, budget=[_MAX_JSON_NODES])
    encoded = json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if len(encoded) > _MAX_JSON_BYTES:
        raise UnsafeJsonValue("JSON exceeds the 64 KiB persistence limit")
    return normalized


def canonical_json_object(value: Any) -> JsonObject:
    normalized = canonical_json(value)
    if not isinstance(normalized, dict):
        raise UnsafeJsonValue("command payloads and results must be JSON objects")
    return normalized


def canonical_json_bytes(value: Any) -> bytes:
    normalized = canonical_json(value)
    return json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _validate_identifier(value: str, *, name: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a stable lowercase identifier")
    return value


def _fenced_hash(fence: bytes, *parts: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(fence)
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


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
