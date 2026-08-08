from __future__ import annotations

import uuid
from unittest.mock import patch

from django.db import transaction
from django.test import TransactionTestCase

from core.context import context_scope
from jobs.clock import database_now
from jobs.dispatch import DispatchConflict, DispatchError, dispatch_after_commit
from jobs.models import DurableJob
from jobs.registry import JobContext, JobPayload, register_handler


@register_handler("jobs.tests.noop")
def noop_handler(context: JobContext, payload: JobPayload) -> None:
    del context, payload


class DurableDispatchTests(TransactionTestCase):
    reset_sequences = True

    def test_dispatch_requires_caller_transaction(self) -> None:
        with self.assertRaisesMessage(DispatchError, "requires an active transaction"):
            dispatch_after_commit(
                handler="jobs.tests.noop",
                deduplication_key="outside-transaction",
                payload={"record_id": str(uuid.uuid4())},
            )

    @patch("django_q.tasks.async_task")
    def test_rollback_persists_no_intent_and_dispatches_no_wakeup(self, async_task) -> None:
        class Rollback(Exception):
            pass

        with self.assertRaises(Rollback):
            with transaction.atomic():
                dispatch_after_commit(
                    handler="jobs.tests.noop",
                    deduplication_key="rolled-back",
                    payload={"record_id": str(uuid.uuid4())},
                )
                raise Rollback
        self.assertFalse(DurableJob.objects.exists())
        async_task.assert_not_called()

    @patch("django_q.tasks.async_task")
    def test_commit_persists_once_and_wakeup_contains_only_job_uuid(self, async_task) -> None:
        raw_key = "retry-key-123"
        payload = {"record_id": str(uuid.uuid4())}
        with transaction.atomic():
            first, first_created = dispatch_after_commit(
                handler="jobs.tests.noop",
                deduplication_key=raw_key,
                payload=payload,
            )
            second, second_created = dispatch_after_commit(
                handler="jobs.tests.noop",
                deduplication_key=raw_key,
                payload=payload,
            )
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(DurableJob.objects.count(), 1)
        self.assertNotEqual(first.deduplication_key_hash, raw_key)
        self.assertNotIn(raw_key, repr(first.__dict__))
        async_task.assert_called_once_with(
            "jobs.tasks.execute_durable_job",
            str(first.id),
            q_options={
                "task_name": f"durable-job-{first.id}",
                "ack_failure": True,
            },
        )

    @patch("django_q.tasks.async_task")
    def test_dispatch_captures_first_safe_request_and_correlation_context(self, async_task) -> None:
        with context_scope(request_id="request-first", correlation_id="correlation-first"):
            with transaction.atomic():
                first, first_created = dispatch_after_commit(
                    handler="jobs.tests.noop",
                    deduplication_key="context-key",
                    payload={"record_id": "one"},
                )
        with context_scope(request_id="request-retry", correlation_id="correlation-retry"):
            with transaction.atomic():
                second, second_created = dispatch_after_commit(
                    handler="jobs.tests.noop",
                    deduplication_key="context-key",
                    payload={"record_id": "one"},
                )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.id, second.id)
        first.refresh_from_db()
        self.assertEqual(first.request_id, "request-first")
        self.assertEqual(first.correlation_id, "correlation-first")
        self.assertEqual(async_task.call_count, 1)

    @patch("django_q.tasks.async_task")
    def test_same_key_with_different_payload_fails_deterministically(self, async_task) -> None:
        with transaction.atomic():
            dispatch_after_commit(
                handler="jobs.tests.noop",
                deduplication_key="conflict-key",
                payload={"record_id": "first"},
            )
        with self.assertRaises(DispatchConflict), transaction.atomic():
            dispatch_after_commit(
                handler="jobs.tests.noop",
                deduplication_key="conflict-key",
                payload={"record_id": "second"},
            )
        self.assertEqual(DurableJob.objects.count(), 1)
        self.assertEqual(async_task.call_count, 1)

    @patch("django_q.tasks.async_task", side_effect=RuntimeError("credential-canary"))
    def test_wakeup_failure_leaves_committed_intent_immediately_sweepable(self, async_task) -> None:
        with self.assertLogs("jobs.dispatch", level="WARNING") as logs:
            with transaction.atomic():
                job, _ = dispatch_after_commit(
                    handler="jobs.tests.noop",
                    deduplication_key="broker-down",
                    payload={"record_id": "safe-id"},
                )
        job.refresh_from_db()
        self.assertEqual(job.status, DurableJob.Status.PENDING)
        self.assertLessEqual(job.next_wakeup_at, database_now())
        self.assertNotIn("credential-canary", "\n".join(logs.output))
        async_task.assert_called_once()

    @patch("django_q.tasks.async_task")
    def test_pre_reservation_failure_cannot_escape_after_successful_commit(
        self, async_task
    ) -> None:
        initial_now = database_now()
        with (
            patch(
                "jobs.dispatch.database_now",
                side_effect=(initial_now, RuntimeError("database-credential-canary")),
            ),
            self.assertLogs("jobs.dispatch", level="WARNING") as logs,
        ):
            with transaction.atomic():
                job, created = dispatch_after_commit(
                    handler="jobs.tests.noop",
                    deduplication_key="database-down-after-commit",
                    payload={"record_id": "safe-id"},
                )

        self.assertTrue(created)
        job.refresh_from_db()
        self.assertEqual(job.status, DurableJob.Status.PENDING)
        self.assertLessEqual(job.next_wakeup_at, database_now())
        self.assertNotIn("database-credential-canary", "\n".join(logs.output))
        async_task.assert_not_called()
