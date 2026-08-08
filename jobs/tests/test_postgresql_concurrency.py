from __future__ import annotations

import threading
import uuid
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature

from jobs.dispatch import dispatch_after_commit
from jobs.models import DurableJob
from jobs.registry import JobContext, JobPayload, register_handler
from jobs.scheduler import acquire_scheduler_lease


@register_handler("jobs.tests.concurrent")
def concurrent_handler(context: JobContext, payload: JobPayload) -> None:
    del context, payload


class PostgreSQLConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update_skip_locked")
    def test_identical_concurrent_dispatches_create_one_authoritative_intent(self) -> None:
        self.assertEqual(connection.vendor, "postgresql")
        barrier = threading.Barrier(2)
        results: list[uuid.UUID] = []
        failures: list[BaseException] = []

        def contender() -> None:
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                with transaction.atomic():
                    job, _ = dispatch_after_commit(
                        handler="jobs.tests.concurrent",
                        deduplication_key="same-concurrent-key",
                        payload={"record_id": "same-record"},
                    )
                results.append(job.id)
            except BaseException as exc:
                failures.append(exc)
            finally:
                connection.close()

        with patch("django_q.tasks.async_task"):
            threads = [threading.Thread(target=contender) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertFalse(failures)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(DurableJob.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update_skip_locked")
    def test_two_scheduler_contenders_elect_one_owner(self) -> None:
        self.assertEqual(connection.vendor, "postgresql")
        barrier = threading.Barrier(2)
        claims = []
        failures: list[BaseException] = []

        def contender(owner: str) -> None:
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                claims.append(acquire_scheduler_lease(owner, ttl_seconds=30))
            except BaseException as exc:
                failures.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=contender, args=("scheduler-one",)),
            threading.Thread(target=contender, args=("scheduler-two",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(failures)
        self.assertEqual(sum(claim is not None for claim in claims), 1)
