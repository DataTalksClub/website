from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django_q.models import Schedule  # type: ignore[import-untyped]

from jobs.clock import database_now
from jobs.heartbeat import (
    prune_stale_heartbeats,
    renew_worker_heartbeat,
    start_worker_heartbeat,
    stop_worker_heartbeat,
)
from jobs.models import WorkerHeartbeat
from jobs.scheduler import (
    acquire_scheduler_lease,
    register_code_schedules,
    release_scheduler_lease,
    renew_scheduler_lease,
    require_non_owner_scheduler_disabled,
)


class SchedulerAndHeartbeatTests(TestCase):
    @override_settings(Q_CLUSTER={"orm": "default", "scheduler": False})
    def test_non_owner_scheduler_contract_requires_exact_false(self) -> None:
        require_non_owner_scheduler_disabled()

    @override_settings(Q_CLUSTER={"orm": "default"})
    def test_non_owner_scheduler_contract_fails_closed_when_omitted(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            require_non_owner_scheduler_disabled()

    def test_scheduler_lease_has_one_fenced_owner_and_registers_idempotently(self) -> None:
        first = acquire_scheduler_lease("scheduler-one", ttl_seconds=30)
        self.assertIsNotNone(first)
        self.assertIsNone(acquire_scheduler_lease("scheduler-two", ttl_seconds=30))
        assert first is not None
        self.assertEqual(register_code_schedules(first), 1)
        self.assertEqual(register_code_schedules(first), 1)
        schedule = Schedule.objects.get(name="dtc:durable-job-relay")
        self.assertEqual(schedule.func, "jobs.tasks.sweep_and_relay")
        self.assertEqual(schedule.schedule_type, Schedule.MINUTES)
        self.assertEqual(schedule.minutes, 1)
        self.assertTrue(renew_scheduler_lease(first, ttl_seconds=30))
        self.assertTrue(release_scheduler_lease(first))
        second = acquire_scheduler_lease("scheduler-two", ttl_seconds=30)
        self.assertIsNotNone(second)
        self.assertFalse(release_scheduler_lease(first))

    def test_registration_collapses_only_code_owned_schedule_duplicates(self) -> None:
        Schedule.objects.create(name="third-party", func="outside.one")
        Schedule.objects.create(name="third-party", func="outside.two")
        Schedule.objects.create(name="dtc:durable-job-relay", func="outside.one")
        Schedule.objects.create(name="dtc:durable-job-relay", func="outside.two")

        claim = acquire_scheduler_lease("scheduler-owner", ttl_seconds=30)
        assert claim is not None
        self.assertEqual(register_code_schedules(claim), 1)

        self.assertEqual(Schedule.objects.filter(name="third-party").count(), 2)
        self.assertEqual(Schedule.objects.filter(name="dtc:durable-job-relay").count(), 1)

    def test_worker_heartbeat_is_shared_fenced_and_prunable(self) -> None:
        first = start_worker_heartbeat(
            "worker-one", ttl_seconds=30, metadata={"queue_id": "default"}
        )
        self.assertTrue(renew_worker_heartbeat("worker-one", first, ttl_seconds=30))
        second = start_worker_heartbeat("worker-one", ttl_seconds=30)
        self.assertNotEqual(first, second)
        self.assertFalse(renew_worker_heartbeat("worker-one", first, ttl_seconds=30))
        self.assertTrue(stop_worker_heartbeat("worker-one", second))
        self.assertFalse(WorkerHeartbeat.objects.exists())

        stale = start_worker_heartbeat("worker-stale", ttl_seconds=30)
        del stale
        now = database_now()
        WorkerHeartbeat.objects.filter(worker_id="worker-stale").update(
            started_at=now - timedelta(seconds=90),
            heartbeat_at=now - timedelta(seconds=60),
            expires_at=now - timedelta(seconds=30),
        )
        self.assertEqual(prune_stale_heartbeats(), 1)
