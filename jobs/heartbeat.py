from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import DEFAULT_DB_ALIAS, transaction

from jobs.clock import database_now
from jobs.execution import _validate_lease_seconds, _validate_worker_id
from jobs.models import WorkerHeartbeat
from jobs.registry import JobPayload, validate_payload


def start_worker_heartbeat(
    worker_id: str,
    *,
    ttl_seconds: int = 90,
    metadata: JobPayload | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> uuid.UUID:
    worker_id = _validate_worker_id(worker_id)
    ttl_seconds = _validate_lease_seconds(ttl_seconds)
    safe_metadata = validate_payload(metadata or {})
    now = database_now(using=using)
    token = uuid.uuid4()
    with transaction.atomic(using=using):
        WorkerHeartbeat.objects.using(using).update_or_create(
            worker_id=worker_id,
            defaults={
                "lease_token": token,
                "started_at": now,
                "heartbeat_at": now,
                "expires_at": now + timedelta(seconds=ttl_seconds),
                "metadata": safe_metadata,
            },
        )
    return token


def renew_worker_heartbeat(
    worker_id: str,
    lease_token: uuid.UUID,
    *,
    ttl_seconds: int = 90,
    using: str = DEFAULT_DB_ALIAS,
) -> bool:
    worker_id = _validate_worker_id(worker_id)
    ttl_seconds = _validate_lease_seconds(ttl_seconds)
    now = database_now(using=using)
    updated = (
        WorkerHeartbeat.objects.using(using)
        .filter(
            worker_id=worker_id,
            lease_token=lease_token,
            expires_at__gt=now,
        )
        .update(
            heartbeat_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
    )
    return updated == 1


def stop_worker_heartbeat(
    worker_id: str,
    lease_token: uuid.UUID,
    *,
    using: str = DEFAULT_DB_ALIAS,
) -> bool:
    worker_id = _validate_worker_id(worker_id)
    deleted, _ = (
        WorkerHeartbeat.objects.using(using)
        .filter(
            worker_id=worker_id,
            lease_token=lease_token,
        )
        .delete()
    )
    return deleted == 1


def prune_stale_heartbeats(*, limit: int = 1_000, using: str = DEFAULT_DB_ALIAS) -> int:
    if not 1 <= limit <= 10_000:
        raise ValueError("heartbeat prune limit must be between 1 and 10000")
    now = database_now(using=using)
    stale_ids = list(
        WorkerHeartbeat.objects.using(using)
        .filter(expires_at__lte=now)
        .order_by("expires_at", "worker_id")
        .values_list("worker_id", flat=True)[:limit]
    )
    deleted, _ = WorkerHeartbeat.objects.using(using).filter(worker_id__in=stale_ids).delete()
    return deleted
