from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.idempotency import (
    JsonObject,
    canonical_json_bytes,
    canonical_json_object,
)

from .constants import IDEMPOTENCY_RETENTION, MAX_IDEMPOTENCY_KEY_BYTES
from .models import APIPrincipal, ManagementIdempotencyRecord


class ManagementIdempotencyConflict(RuntimeError):
    pass


class SecretUnavailableOnReplay(RuntimeError):
    def __init__(self, safe_result: JsonObject) -> None:
        self.safe_result = safe_result
        super().__init__("one-time secret is unavailable on replay")


@dataclass(frozen=True, slots=True)
class OneTimeCommandResult:
    response: JsonObject
    safe_result: JsonObject


def _fenced_hash(fence: bytes, *parts: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(fence)
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _hash_key(principal_id: uuid.UUID, operation: str, key: str) -> str:
    key_bytes = key.encode("utf-8")
    if not key_bytes or len(key_bytes) > MAX_IDEMPOTENCY_KEY_BYTES:
        raise ValueError("idempotency key must contain between 1 and 512 UTF-8 bytes")
    return _fenced_hash(
        b"dtc:management-idempotency-key:v1\0",
        principal_id.bytes,
        operation.encode("ascii"),
        key_bytes,
    )


def _hash_request(principal_id: uuid.UUID, operation: str, request: Any) -> str:
    return _fenced_hash(
        b"dtc:management-idempotency-request:v1\0",
        principal_id.bytes,
        operation.encode("ascii"),
        canonical_json_bytes(request),
    )


def execute_one_time_idempotent(
    *,
    principal: APIPrincipal,
    operation: str,
    key: str,
    request: JsonObject,
    command: Callable[[], OneTimeCommandResult],
    replay_safe: bool = False,
    using: str = "default",
) -> OneTimeCommandResult:
    if not operation or len(operation) > 128 or not operation.isascii():
        raise ValueError("operation must be a bounded ASCII identifier")
    key_hash = _hash_key(principal.id, operation, key)
    request_hash = _hash_request(principal.id, operation, request)
    now = timezone.now()
    owner_token = uuid.uuid4()
    defaults = {
        "request_hash": request_hash,
        "status": ManagementIdempotencyRecord.Status.IN_PROGRESS,
        "owner_token": owner_token,
        "expires_at": now + IDEMPOTENCY_RETENTION,
    }

    with transaction.atomic(using=using):
        record, created = ManagementIdempotencyRecord.objects.using(using).get_or_create(
            principal=principal,
            operation=operation,
            key_hash=key_hash,
            defaults=defaults,
        )
        if not created and record.expires_at <= now:
            deleted, _ = (
                ManagementIdempotencyRecord.objects.using(using)
                .filter(pk=record.pk, expires_at__lte=now)
                .delete()
            )
            if deleted:
                record, created = ManagementIdempotencyRecord.objects.using(using).get_or_create(
                    principal=principal,
                    operation=operation,
                    key_hash=key_hash,
                    defaults=defaults,
                )
        if not created:
            if record.request_hash != request_hash:
                raise ManagementIdempotencyConflict("idempotency request conflicts")
            if record.status == ManagementIdempotencyRecord.Status.COMPLETED:
                safe_result = canonical_json_object(record.safe_result)
                if replay_safe:
                    return OneTimeCommandResult(response=safe_result, safe_result=safe_result)
                raise SecretUnavailableOnReplay(safe_result)
            raise ManagementIdempotencyConflict("idempotency request is in progress")

        result = command()
        safe_result = canonical_json_object(result.safe_result)
        response = canonical_json_object(result.response)
        updated = (
            ManagementIdempotencyRecord.objects.using(using)
            .filter(
                pk=record.pk,
                owner_token=owner_token,
                status=ManagementIdempotencyRecord.Status.IN_PROGRESS,
            )
            .update(
                status=ManagementIdempotencyRecord.Status.COMPLETED,
                safe_result=safe_result,
                completed_at=now,
            )
        )
        if updated != 1:
            raise RuntimeError("management idempotency fence was lost")
        return OneTimeCommandResult(response=response, safe_result=safe_result)
