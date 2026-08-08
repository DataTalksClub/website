from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import transaction
from django.test import TransactionTestCase

from core.audit import record_audit_event
from core.context import AuditContext, context_scope, current_context
from core.models import AuditEvent
from jobs.clock import database_now
from jobs.dispatch import dispatch_after_commit
from jobs.execution import (
    PermanentJobError,
    RetryableJobError,
    claim_job,
    complete_job,
    execute_job,
    renew_job_lease,
    sweep_expired_jobs,
)
from jobs.models import DurableJob
from jobs.registry import JobContext, JobPayload, register_handler

COMPLETIONS: list[uuid.UUID] = []
OBSERVED_CONTEXTS: list[tuple[JobContext, AuditContext]] = []


@register_handler("jobs.tests.complete")
def complete_handler(context: JobContext, payload: JobPayload) -> None:
    del payload
    COMPLETIONS.append(context.job_id)


@register_handler("jobs.tests.retry")
def retry_handler(context: JobContext, payload: JobPayload) -> None:
    del context, payload
    raise RetryableJobError("temporary_failure")


@register_handler("jobs.tests.permanent")
def permanent_handler(context: JobContext, payload: JobPayload) -> None:
    del context, payload
    raise PermanentJobError("invalid_target")


@register_handler("jobs.tests.unexpected")
def unexpected_handler(context: JobContext, payload: JobPayload) -> None:
    del context, payload
    raise RuntimeError("credential-canary")


@register_handler("jobs.tests.audit-context")
def audit_context_handler(context: JobContext, payload: JobPayload) -> None:
    del payload
    OBSERVED_CONTEXTS.append((context, current_context()))
    record_audit_event(
        action="jobs.context_observed",
        target_type="durable_job",
        target_id=context.job_id,
        outcome=AuditEvent.Outcome.SUCCEEDED,
    )


class DurableExecutionTests(TransactionTestCase):
    def setUp(self) -> None:
        COMPLETIONS.clear()
        OBSERVED_CONTEXTS.clear()

    def create_job(self, handler: str, *, max_attempts: int = 3) -> DurableJob:
        with patch("django_q.tasks.async_task"), transaction.atomic():
            job, _ = dispatch_after_commit(
                handler=handler,
                deduplication_key=f"key-{uuid.uuid4()}",
                payload={"record_id": str(uuid.uuid4())},
                max_attempts=max_attempts,
            )
        return job

    def test_active_lease_is_exclusive_and_token_fences_completion(self) -> None:
        job = self.create_job("jobs.tests.complete")
        first = claim_job(job.id, worker_id="worker-one", lease_seconds=30)
        self.assertIsNotNone(first)
        self.assertIsNone(claim_job(job.id, worker_id="worker-two", lease_seconds=30))
        assert first is not None
        self.assertFalse(renew_job_lease(job.id, uuid.uuid4(), lease_seconds=30))
        self.assertFalse(complete_job(job.id, uuid.uuid4()))
        self.assertTrue(renew_job_lease(job.id, first.lease_token, lease_seconds=30))
        self.assertTrue(complete_job(job.id, first.lease_token))
        self.assertFalse(complete_job(job.id, first.lease_token))

    def test_successful_handler_completes_once(self) -> None:
        job = self.create_job("jobs.tests.complete")
        self.assertEqual(execute_job(job.id, worker_id="worker-one"), "succeeded")
        self.assertEqual(execute_job(job.id, worker_id="worker-two"), "not_claimed")
        self.assertEqual(COMPLETIONS, [job.id])
        job.refresh_from_db()
        self.assertEqual(job.status, DurableJob.Status.SUCCEEDED)
        self.assertEqual(job.attempt_count, 1)

    def test_retry_and_permanent_failures_use_safe_states(self) -> None:
        retry = self.create_job("jobs.tests.retry")
        permanent = self.create_job("jobs.tests.permanent")
        self.assertEqual(execute_job(retry.id, worker_id="worker-one"), "retry_wait")
        self.assertEqual(execute_job(permanent.id, worker_id="worker-two"), "failed")
        retry.refresh_from_db()
        permanent.refresh_from_db()
        self.assertEqual(retry.status, DurableJob.Status.RETRY_WAIT)
        self.assertEqual(retry.last_error_code, "temporary_failure")
        self.assertEqual(permanent.status, DurableJob.Status.FAILED)
        self.assertEqual(permanent.last_error_code, "invalid_target")

    def test_unexpected_exception_body_never_enters_state_or_log(self) -> None:
        job = self.create_job("jobs.tests.unexpected")
        with context_scope(
            request_id="worker-request",
            correlation_id="worker-correlation",
            job_id="worker-parent-job",
        ):
            with self.assertLogs("jobs.execution", level="WARNING") as logs:
                self.assertEqual(execute_job(job.id, worker_id="worker-one"), "retry_wait")
            self.assertEqual(current_context().request_id, "worker-request")
            self.assertEqual(current_context().correlation_id, "worker-correlation")
            self.assertEqual(current_context().job_id, "worker-parent-job")
        job.refresh_from_db()
        self.assertEqual(job.last_error_code, "handler_error")
        self.assertNotIn("credential-canary", repr(job.__dict__))
        self.assertNotIn("credential-canary", "\n".join(logs.output))

    def test_execution_propagates_context_to_handler_and_audit_then_resets(self) -> None:
        with context_scope(request_id="request-123", correlation_id="correlation-456"):
            job = self.create_job("jobs.tests.audit-context")
        self.assertIsNone(current_context().request_id)

        with context_scope(
            request_id="worker-request",
            correlation_id="worker-correlation",
            job_id="worker-parent-job",
        ):
            self.assertEqual(execute_job(job.id, worker_id="worker-one"), "succeeded")
            self.assertEqual(current_context().request_id, "worker-request")
            self.assertEqual(current_context().correlation_id, "worker-correlation")
            self.assertEqual(current_context().job_id, "worker-parent-job")

        handler_context, execution_context = OBSERVED_CONTEXTS.pop()
        self.assertEqual(handler_context.request_id, "request-123")
        self.assertEqual(handler_context.correlation_id, "correlation-456")
        self.assertEqual(execution_context.request_id, "request-123")
        self.assertEqual(execution_context.correlation_id, "correlation-456")
        self.assertEqual(execution_context.job_id, str(job.id))
        event = AuditEvent.objects.get(action="jobs.context_observed")
        self.assertEqual(event.request_id, "request-123")
        self.assertEqual(event.correlation_id, "correlation-456")
        self.assertEqual(event.job_id, str(job.id))
        self.assertIsNone(current_context().request_id)
        self.assertIsNone(current_context().correlation_id)
        self.assertIsNone(current_context().job_id)

    def test_expired_lease_is_recovered_and_old_worker_is_fenced(self) -> None:
        job = self.create_job("jobs.tests.complete")
        first = claim_job(job.id, worker_id="worker-one", lease_seconds=30)
        assert first is not None
        DurableJob.objects.filter(id=job.id).update(
            lease_expires_at=database_now() - timedelta(seconds=1)
        )
        self.assertEqual(sweep_expired_jobs(), (1, 0))
        job.refresh_from_db()
        DurableJob.objects.filter(id=job.id).update(
            available_at=database_now(), next_wakeup_at=database_now()
        )
        second = claim_job(job.id, worker_id="worker-two", lease_seconds=30)
        assert second is not None
        self.assertEqual(first.job_id, job.id)
        self.assertEqual(second.job_id, job.id)
        self.assertNotEqual(first.lease_token, second.lease_token)
        self.assertFalse(complete_job(job.id, first.lease_token))
        self.assertTrue(complete_job(job.id, second.lease_token))

    def test_expired_final_attempt_fails_instead_of_replaying_forever(self) -> None:
        job = self.create_job("jobs.tests.complete", max_attempts=1)
        claim = claim_job(job.id, worker_id="worker-one", lease_seconds=30)
        assert claim is not None
        DurableJob.objects.filter(id=job.id).update(
            lease_expires_at=database_now() - timedelta(seconds=1)
        )
        self.assertEqual(sweep_expired_jobs(), (0, 1))
        job.refresh_from_db()
        self.assertEqual(job.status, DurableJob.Status.FAILED)
        self.assertEqual(job.last_error_code, "lease_expired")
