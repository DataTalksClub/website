from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta

from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.models import F

from core.context import current_context
from jobs.clock import database_now
from jobs.models import DurableJob
from jobs.registry import JobPayload, get_handler, validate_payload

logger = logging.getLogger(__name__)

DEDUPLICATION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
WAKEUP_COOLDOWN = timedelta(seconds=30)


class DispatchError(RuntimeError):
    """Safe base error for durable dispatch contract violations."""


class DispatchConflict(DispatchError):
    """A deduplication key was reused for a different immutable intent."""


def dispatch_after_commit(
    *,
    handler: str,
    deduplication_key: str,
    payload: Mapping[str, object],
    operation_id: uuid.UUID | None = None,
    max_attempts: int = 3,
    available_at: datetime | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> tuple[DurableJob, bool]:
    """Persist one authoritative intent in the caller transaction and wake after commit."""

    connection = connections[using]
    if not connection.in_atomic_block:
        raise DispatchError("durable dispatch requires an active transaction")
    get_handler(handler)
    normalized_payload = validate_payload(payload)
    deduplication_key_hash = _hash_deduplication_key(deduplication_key)
    payload_hash = _hash_payload(normalized_payload)
    if not 1 <= max_attempts <= 100:
        raise DispatchError("max attempts must be between 1 and 100")
    now = database_now(using=using)
    due_at = available_at or now
    if not isinstance(due_at, datetime) or due_at.tzinfo is None:
        raise DispatchError("available_at must be timezone-aware")

    execution_context = current_context()
    defaults = {
        "operation_id": operation_id,
        "payload_hash": payload_hash,
        "payload": normalized_payload,
        "request_id": execution_context.request_id or "",
        "correlation_id": execution_context.correlation_id or "",
        "max_attempts": max_attempts,
        "available_at": due_at,
        "next_wakeup_at": due_at,
    }
    job, created = DurableJob.objects.using(using).get_or_create(
        handler=handler,
        deduplication_key_hash=deduplication_key_hash,
        defaults=defaults,
    )

    if not created:
        immutable_intent = (
            job.payload_hash,
            job.operation_id,
            job.max_attempts,
        )
        supplied_intent = (payload_hash, operation_id, max_attempts)
        if immutable_intent != supplied_intent:
            raise DispatchConflict("deduplication key conflicts with an existing durable job")
        return job, False

    if due_at <= now:
        transaction.on_commit(
            lambda: best_effort_wake(job.id, using=using),
            using=using,
            robust=True,
        )
    return job, True


def best_effort_wake(job_id: uuid.UUID, *, using: str = DEFAULT_DB_ALIAS) -> bool:
    """Reserve and enqueue a disposable Q2 wakeup; failure leaves the intent sweepable."""

    try:
        now = database_now(using=using)
        reserved = (
            DurableJob.objects.using(using)
            .filter(
                id=job_id,
                status__in=(DurableJob.Status.PENDING, DurableJob.Status.RETRY_WAIT),
                available_at__lte=now,
                next_wakeup_at__lte=now,
            )
            .update(
                next_wakeup_at=now + WAKEUP_COOLDOWN,
                wakeup_count=F("wakeup_count") + 1,
            )
        )
    except Exception:
        logger.warning(
            "durable_job_wakeup_reservation_failed",
            extra={"durable_job_id": str(job_id)},
        )
        return False
    if reserved != 1:
        return False
    try:
        from django_q.tasks import async_task  # type: ignore[import-untyped]

        async_task(
            "jobs.tasks.execute_durable_job",
            str(job_id),
            q_options={
                "task_name": f"durable-job-{job_id}",
                "ack_failure": True,
            },
        )
    except Exception:
        try:
            DurableJob.objects.using(using).filter(
                id=job_id,
                status__in=(DurableJob.Status.PENDING, DurableJob.Status.RETRY_WAIT),
            ).update(next_wakeup_at=now)
        except Exception:
            # The reservation cooldown is bounded, so a failed reset cannot lose durable work.
            logger.warning(
                "durable_job_wakeup_reset_failed",
                extra={"durable_job_id": str(job_id)},
            )
        logger.warning("durable_job_wakeup_failed", extra={"durable_job_id": str(job_id)})
        return False
    return True


def relay_due_jobs(*, limit: int = 100, using: str = DEFAULT_DB_ALIAS) -> int:
    if not 1 <= limit <= 1_000:
        raise DispatchError("relay limit must be between 1 and 1000")
    now = database_now(using=using)
    connection = connections[using]
    with transaction.atomic(using=using):
        queryset = (
            DurableJob.objects.using(using)
            .filter(
                status__in=(DurableJob.Status.PENDING, DurableJob.Status.RETRY_WAIT),
                available_at__lte=now,
                next_wakeup_at__lte=now,
            )
            .order_by("available_at", "created_at", "id")
        )
        if connection.features.has_select_for_update_skip_locked:
            queryset = queryset.select_for_update(skip_locked=True)
        elif connection.features.has_select_for_update:
            queryset = queryset.select_for_update()
        job_ids = list(queryset.values_list("id", flat=True)[:limit])

    return sum(best_effort_wake(job_id, using=using) for job_id in job_ids)


def _hash_deduplication_key(raw_key: str) -> str:
    if not isinstance(raw_key, str) or not DEDUPLICATION_KEY_PATTERN.fullmatch(raw_key):
        raise DispatchError("invalid durable job deduplication key")
    return hashlib.sha256(f"durable-job-dedupe-v1\0{raw_key}".encode()).hexdigest()


def _hash_payload(payload: JobPayload) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()
