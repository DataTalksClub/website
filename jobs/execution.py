from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import DEFAULT_DB_ALIAS

from core.context import context_scope
from jobs.clock import database_now
from jobs.models import DurableJob
from jobs.registry import JobContext, JobPayload, RegistryError, get_handler

logger = logging.getLogger(__name__)

WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
DEFAULT_LEASE_SECONDS = 300
MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 3_600
MAX_BACKOFF_SECONDS = 3_600


class JobExecutionError(RuntimeError):
    """Safe durable job execution error."""


class RetryableJobError(JobExecutionError):
    def __init__(self, code: str = "retryable_error") -> None:
        self.code = _validate_error_code(code)
        super().__init__(self.code)


class PermanentJobError(JobExecutionError):
    def __init__(self, code: str = "permanent_error") -> None:
        self.code = _validate_error_code(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class JobClaim:
    job_id: uuid.UUID
    operation_id: uuid.UUID | None
    request_id: str | None
    correlation_id: str | None
    handler: str
    payload: JobPayload
    attempt_count: int
    worker_id: str
    lease_token: uuid.UUID
    lease_expires_at: datetime


def claim_job(
    job_id: uuid.UUID,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    using: str = DEFAULT_DB_ALIAS,
) -> JobClaim | None:
    worker_id = _validate_worker_id(worker_id)
    lease_seconds = _validate_lease_seconds(lease_seconds)
    now = database_now(using=using)
    try:
        job = DurableJob.objects.using(using).get(id=job_id)
    except DurableJob.DoesNotExist:
        return None

    if job.status in DurableJob.TERMINAL_STATUSES:
        return None
    if job.status == DurableJob.Status.RUNNING:
        if job.lease_expires_at is not None and job.lease_expires_at > now:
            return None
        observed_token = job.lease_token
        observed_expiry = job.lease_expires_at
        _recover_locked_job(job, now=now)
        DurableJob.objects.using(using).filter(
            id=job.id,
            status=DurableJob.Status.RUNNING,
            lease_token=observed_token,
            lease_expires_at=observed_expiry,
            attempt_count=job.attempt_count,
        ).update(
            status=job.status,
            available_at=job.available_at,
            next_wakeup_at=job.next_wakeup_at,
            lease_token=None,
            lease_expires_at=None,
            claimed_by="",
            last_error_code=job.last_error_code,
            completed_at=job.completed_at,
            updated_at=now,
        )
        return None
    if job.available_at > now:
        return None
    if job.attempt_count >= job.max_attempts:
        DurableJob.objects.using(using).filter(
            id=job.id,
            status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            lease_token__isnull=True,
        ).update(
            status=DurableJob.Status.FAILED,
            lease_token=None,
            lease_expires_at=None,
            claimed_by="",
            last_error_code="attempts_exhausted",
            completed_at=now,
            updated_at=now,
        )
        return None

    lease_token = uuid.uuid4()
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    next_attempt = job.attempt_count + 1
    updated = (
        DurableJob.objects.using(using)
        .filter(
            id=job.id,
            status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            available_at=job.available_at,
            lease_token__isnull=True,
        )
        .update(
            status=DurableJob.Status.RUNNING,
            attempt_count=next_attempt,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            claimed_by=worker_id,
            last_error_code="",
            updated_at=now,
        )
    )
    if updated != 1:
        return None
    return JobClaim(
        job_id=job.id,
        operation_id=job.operation_id,
        request_id=job.request_id or None,
        correlation_id=job.correlation_id or None,
        handler=job.handler,
        payload=job.payload,
        attempt_count=next_attempt,
        worker_id=worker_id,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
    )


def renew_job_lease(
    job_id: uuid.UUID,
    lease_token: uuid.UUID,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    using: str = DEFAULT_DB_ALIAS,
) -> bool:
    lease_seconds = _validate_lease_seconds(lease_seconds)
    now = database_now(using=using)
    updated = (
        DurableJob.objects.using(using)
        .filter(
            id=job_id,
            status=DurableJob.Status.RUNNING,
            lease_token=lease_token,
            lease_expires_at__gt=now,
        )
        .update(lease_expires_at=now + timedelta(seconds=lease_seconds))
    )
    return updated == 1


def complete_job(
    job_id: uuid.UUID,
    lease_token: uuid.UUID,
    *,
    using: str = DEFAULT_DB_ALIAS,
) -> bool:
    now = database_now(using=using)
    updated = (
        DurableJob.objects.using(using)
        .filter(
            id=job_id,
            status=DurableJob.Status.RUNNING,
            lease_token=lease_token,
            lease_expires_at__gt=now,
        )
        .update(
            status=DurableJob.Status.SUCCEEDED,
            lease_token=None,
            lease_expires_at=None,
            claimed_by="",
            last_error_code="",
            completed_at=now,
        )
    )
    return updated == 1


def fail_job(
    job_id: uuid.UUID,
    lease_token: uuid.UUID,
    *,
    error_code: str,
    retryable: bool,
    using: str = DEFAULT_DB_ALIAS,
) -> bool:
    error_code = _validate_error_code(error_code)
    now = database_now(using=using)
    job = (
        DurableJob.objects.using(using)
        .filter(
            id=job_id,
            status=DurableJob.Status.RUNNING,
            lease_token=lease_token,
            lease_expires_at__gt=now,
        )
        .first()
    )
    if job is None:
        return False

    if retryable and job.attempt_count < job.max_attempts:
        status = DurableJob.Status.RETRY_WAIT
        available_at = now + retry_backoff(job.attempt_count)
        next_wakeup_at = available_at
        completed_at = None
    else:
        status = DurableJob.Status.FAILED
        available_at = job.available_at
        next_wakeup_at = job.next_wakeup_at
        completed_at = now
    updated = (
        DurableJob.objects.using(using)
        .filter(
            id=job_id,
            status=DurableJob.Status.RUNNING,
            lease_token=lease_token,
            lease_expires_at__gt=now,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
        )
        .update(
            status=status,
            available_at=available_at,
            next_wakeup_at=next_wakeup_at,
            lease_token=None,
            lease_expires_at=None,
            claimed_by="",
            last_error_code=error_code,
            completed_at=completed_at,
            updated_at=now,
        )
    )
    return updated == 1


def sweep_expired_jobs(*, limit: int = 100, using: str = DEFAULT_DB_ALIAS) -> tuple[int, int]:
    if not 1 <= limit <= 1_000:
        raise JobExecutionError("sweep limit must be between 1 and 1000")
    now = database_now(using=using)
    recovered = 0
    exhausted = 0
    jobs = list(
        DurableJob.objects.using(using)
        .filter(
            status=DurableJob.Status.RUNNING,
            lease_expires_at__lte=now,
        )
        .order_by("lease_expires_at", "id")[:limit]
    )
    for job in jobs:
        observed_token = job.lease_token
        observed_expiry = job.lease_expires_at
        _recover_locked_job(job, now=now)
        updated = (
            DurableJob.objects.using(using)
            .filter(
                id=job.id,
                status=DurableJob.Status.RUNNING,
                lease_token=observed_token,
                lease_expires_at=observed_expiry,
                attempt_count=job.attempt_count,
            )
            .update(
                status=job.status,
                available_at=job.available_at,
                next_wakeup_at=job.next_wakeup_at,
                lease_token=None,
                lease_expires_at=None,
                claimed_by="",
                last_error_code=job.last_error_code,
                completed_at=job.completed_at,
                updated_at=now,
            )
        )
        if updated != 1:
            continue
        if job.status == DurableJob.Status.FAILED:
            exhausted += 1
        else:
            recovered += 1
    return recovered, exhausted


def execute_job(
    job_id: uuid.UUID,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    using: str = DEFAULT_DB_ALIAS,
) -> str:
    claim = claim_job(
        job_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        using=using,
    )
    if claim is None:
        return "not_claimed"
    try:
        handler = get_handler(claim.handler)
    except RegistryError:
        fail_job(
            claim.job_id,
            claim.lease_token,
            error_code="unknown_handler",
            retryable=False,
            using=using,
        )
        return "failed"

    context = JobContext(
        job_id=claim.job_id,
        operation_id=claim.operation_id,
        request_id=claim.request_id,
        correlation_id=claim.correlation_id,
        attempt_count=claim.attempt_count,
        worker_id=claim.worker_id,
        lease_token=claim.lease_token,
    )
    try:
        with context_scope(
            request_id=claim.request_id,
            correlation_id=claim.correlation_id,
            job_id=str(claim.job_id),
        ):
            handler(context, claim.payload)
    except PermanentJobError as exc:
        fail_job(
            claim.job_id,
            claim.lease_token,
            error_code=exc.code,
            retryable=False,
            using=using,
        )
        return "failed"
    except RetryableJobError as exc:
        fail_job(
            claim.job_id,
            claim.lease_token,
            error_code=exc.code,
            retryable=True,
            using=using,
        )
        return "retry_wait"
    except Exception:
        fail_job(
            claim.job_id,
            claim.lease_token,
            error_code="handler_error",
            retryable=True,
            using=using,
        )
        logger.warning(
            "durable_job_handler_failed",
            extra={"durable_job_id": str(claim.job_id), "handler": claim.handler},
        )
        return "retry_wait"

    if complete_job(claim.job_id, claim.lease_token, using=using):
        return "succeeded"
    logger.warning("durable_job_lease_lost", extra={"durable_job_id": str(claim.job_id)})
    return "lease_lost"


def retry_backoff(attempt_count: int) -> timedelta:
    seconds = min(MAX_BACKOFF_SECONDS, 5 * (2 ** max(0, attempt_count - 1)))
    return timedelta(seconds=seconds)


def _recover_locked_job(job: DurableJob, *, now: datetime) -> None:
    _clear_lease(job)
    job.last_error_code = "lease_expired"
    if job.attempt_count >= job.max_attempts:
        job.status = DurableJob.Status.FAILED
        job.completed_at = now
    else:
        job.status = DurableJob.Status.RETRY_WAIT
        job.available_at = now + retry_backoff(job.attempt_count)
        job.next_wakeup_at = job.available_at
        job.completed_at = None


def _clear_lease(job: DurableJob) -> None:
    job.lease_token = None
    job.lease_expires_at = None
    job.claimed_by = ""


def _validate_worker_id(worker_id: str) -> str:
    if not isinstance(worker_id, str) or not WORKER_ID_PATTERN.fullmatch(worker_id):
        raise JobExecutionError("invalid durable worker id")
    return worker_id


def _validate_lease_seconds(lease_seconds: int) -> int:
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool):
        raise JobExecutionError("lease duration must be an integer")
    if not MIN_LEASE_SECONDS <= lease_seconds <= MAX_LEASE_SECONDS:
        raise JobExecutionError("lease duration is outside the safe range")
    return lease_seconds


def _validate_error_code(code: str) -> str:
    if not isinstance(code, str) or not ERROR_CODE_PATTERN.fullmatch(code):
        raise JobExecutionError("invalid safe job error code")
    return code
