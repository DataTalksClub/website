from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from jobs.scheduler import SchedulerClaim


@override_settings(Q_CLUSTER={"orm": "default", "scheduler": False})
class RunJobWorkerCommandTests(SimpleTestCase):
    @patch("jobs.management.commands.run_job_worker.stop_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.start_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.renew_worker_heartbeat", return_value=True)
    @patch("jobs.management.commands.run_job_worker.release_scheduler_lease")
    @patch("jobs.management.commands.run_job_worker.register_code_schedules", return_value=1)
    @patch("jobs.management.commands.run_job_worker.acquire_scheduler_lease")
    @patch("jobs.management.commands.run_job_worker.q2_scheduler")
    @patch("jobs.management.commands.run_job_worker.Cluster")
    @patch("jobs.management.commands.run_job_worker.get_broker")
    def test_owner_starts_cluster_runs_scheduled_pass_and_cleans_up(
        self,
        get_broker,
        cluster_class,
        q2_scheduler,
        acquire_scheduler_lease,
        register_code_schedules,
        release_scheduler_lease,
        renew_worker_heartbeat,
        start_worker_heartbeat,
        stop_worker_heartbeat,
    ) -> None:
        broker = object()
        get_broker.return_value = broker
        cluster = MagicMock()
        cluster.is_running = True
        cluster_class.return_value = cluster
        heartbeat_token = uuid.uuid4()
        start_worker_heartbeat.return_value = heartbeat_token
        claim = SchedulerClaim(owner_id="owner", lease_token=uuid.uuid4())
        acquire_scheduler_lease.return_value = claim

        call_command("run_job_worker", "--once")

        cluster_class.assert_called_once_with()
        cluster.start.assert_called_once_with()
        start_worker_heartbeat.assert_called_once()
        renew_worker_heartbeat.assert_called_once()
        acquire_scheduler_lease.assert_called_once()
        register_code_schedules.assert_called_once_with(claim)
        q2_scheduler.assert_called_once_with(broker=broker)
        release_scheduler_lease.assert_called_once_with(claim)
        cluster.stop.assert_called_once_with()
        stop_worker_heartbeat.assert_called_once()

    @patch("jobs.management.commands.run_job_worker.stop_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.start_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.renew_worker_heartbeat", return_value=True)
    @patch("jobs.management.commands.run_job_worker.register_code_schedules")
    @patch("jobs.management.commands.run_job_worker.acquire_scheduler_lease", return_value=None)
    @patch("jobs.management.commands.run_job_worker.q2_scheduler")
    @patch("jobs.management.commands.run_job_worker.Cluster")
    @patch("jobs.management.commands.run_job_worker.get_broker")
    def test_non_owner_remains_an_ordinary_worker_without_running_scheduler(
        self,
        get_broker,
        cluster_class,
        q2_scheduler,
        acquire_scheduler_lease,
        register_code_schedules,
        renew_worker_heartbeat,
        start_worker_heartbeat,
        stop_worker_heartbeat,
    ) -> None:
        get_broker.return_value = object()
        cluster = MagicMock()
        cluster.is_running = True
        cluster_class.return_value = cluster
        start_worker_heartbeat.return_value = uuid.uuid4()

        call_command("run_job_worker", "--once")

        cluster.start.assert_called_once_with()
        acquire_scheduler_lease.assert_called_once()
        register_code_schedules.assert_not_called()
        q2_scheduler.assert_not_called()
        renew_worker_heartbeat.assert_called_once()
        cluster.stop.assert_called_once_with()
        stop_worker_heartbeat.assert_called_once()

    @patch("jobs.management.commands.run_job_worker.stop_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.start_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.renew_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.register_code_schedules")
    @patch("jobs.management.commands.run_job_worker.acquire_scheduler_lease")
    @patch("jobs.management.commands.run_job_worker.q2_scheduler")
    @patch("jobs.management.commands.run_job_worker.Cluster")
    @patch("jobs.management.commands.run_job_worker.get_broker")
    def test_dead_sentinel_never_publishes_heartbeat_or_runs_scheduler(
        self,
        get_broker,
        cluster_class,
        q2_scheduler,
        acquire_scheduler_lease,
        register_code_schedules,
        renew_worker_heartbeat,
        start_worker_heartbeat,
        stop_worker_heartbeat,
    ) -> None:
        cluster = MagicMock()
        cluster.is_running = True
        cluster.sentinel.is_alive.return_value = False
        cluster_class.return_value = cluster

        with self.assertRaisesMessage(CommandError, "sentinel did not start"):
            call_command("run_job_worker", "--once")

        get_broker.assert_not_called()
        start_worker_heartbeat.assert_not_called()
        renew_worker_heartbeat.assert_not_called()
        acquire_scheduler_lease.assert_not_called()
        register_code_schedules.assert_not_called()
        q2_scheduler.assert_not_called()
        stop_worker_heartbeat.assert_not_called()
        cluster.stop.assert_called_once_with()

    @patch("jobs.management.commands.run_job_worker.stop_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.start_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.renew_worker_heartbeat", return_value=True)
    @patch("jobs.management.commands.run_job_worker.register_code_schedules")
    @patch("jobs.management.commands.run_job_worker.acquire_scheduler_lease")
    @patch("jobs.management.commands.run_job_worker.q2_scheduler")
    @patch("jobs.management.commands.run_job_worker.Cluster")
    @patch("jobs.management.commands.run_job_worker.get_broker")
    def test_sentinel_death_after_heartbeat_stops_before_scheduler_pass(
        self,
        get_broker,
        cluster_class,
        q2_scheduler,
        acquire_scheduler_lease,
        register_code_schedules,
        renew_worker_heartbeat,
        start_worker_heartbeat,
        stop_worker_heartbeat,
    ) -> None:
        get_broker.return_value = object()
        cluster = MagicMock()
        cluster.is_running = True
        cluster.sentinel.is_alive.side_effect = (True, True, False)
        cluster_class.return_value = cluster
        start_worker_heartbeat.return_value = uuid.uuid4()

        with self.assertRaisesMessage(CommandError, "sentinel stopped unexpectedly"):
            call_command("run_job_worker", "--once")

        renew_worker_heartbeat.assert_called_once()
        acquire_scheduler_lease.assert_not_called()
        register_code_schedules.assert_not_called()
        q2_scheduler.assert_not_called()
        cluster.stop.assert_called_once_with()
        stop_worker_heartbeat.assert_called_once()

    @patch("jobs.management.commands.run_job_worker.stop_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.start_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.renew_worker_heartbeat", return_value=True)
    @patch("jobs.management.commands.run_job_worker.release_scheduler_lease")
    @patch("jobs.management.commands.run_job_worker.register_code_schedules", return_value=1)
    @patch("jobs.management.commands.run_job_worker.acquire_scheduler_lease")
    @patch(
        "jobs.management.commands.run_job_worker.q2_scheduler",
        side_effect=RuntimeError("scheduler failure"),
    )
    @patch("jobs.management.commands.run_job_worker.Cluster")
    @patch("jobs.management.commands.run_job_worker.get_broker")
    def test_failure_still_releases_all_owned_leases_and_stops_cluster(
        self,
        get_broker,
        cluster_class,
        q2_scheduler,
        acquire_scheduler_lease,
        register_code_schedules,
        release_scheduler_lease,
        renew_worker_heartbeat,
        start_worker_heartbeat,
        stop_worker_heartbeat,
    ) -> None:
        del q2_scheduler, register_code_schedules, renew_worker_heartbeat
        get_broker.return_value = object()
        cluster = MagicMock()
        cluster.is_running = True
        cluster_class.return_value = cluster
        heartbeat_token = uuid.uuid4()
        start_worker_heartbeat.return_value = heartbeat_token
        claim = SchedulerClaim(owner_id="owner", lease_token=uuid.uuid4())
        acquire_scheduler_lease.return_value = claim

        with self.assertRaisesMessage(RuntimeError, "scheduler failure"):
            call_command("run_job_worker", "--once")

        release_scheduler_lease.assert_called_once_with(claim)
        cluster.stop.assert_called_once_with()
        stop_worker_heartbeat.assert_called_once()

    @patch(
        "jobs.management.commands.run_job_worker.stop_worker_heartbeat",
        side_effect=RuntimeError("heartbeat cleanup failure"),
    )
    @patch("jobs.management.commands.run_job_worker.start_worker_heartbeat")
    @patch("jobs.management.commands.run_job_worker.renew_worker_heartbeat", return_value=True)
    @patch(
        "jobs.management.commands.run_job_worker.release_scheduler_lease",
        side_effect=RuntimeError("lease cleanup failure"),
    )
    @patch("jobs.management.commands.run_job_worker.register_code_schedules", return_value=1)
    @patch("jobs.management.commands.run_job_worker.acquire_scheduler_lease")
    @patch(
        "jobs.management.commands.run_job_worker.q2_scheduler",
        side_effect=RuntimeError("primary scheduler failure"),
    )
    @patch("jobs.management.commands.run_job_worker.Cluster")
    @patch("jobs.management.commands.run_job_worker.get_broker")
    def test_cleanup_errors_do_not_mask_primary_failure(
        self,
        get_broker,
        cluster_class,
        q2_scheduler,
        acquire_scheduler_lease,
        register_code_schedules,
        release_scheduler_lease,
        renew_worker_heartbeat,
        start_worker_heartbeat,
        stop_worker_heartbeat,
    ) -> None:
        del (
            q2_scheduler,
            register_code_schedules,
            release_scheduler_lease,
            renew_worker_heartbeat,
            stop_worker_heartbeat,
        )
        get_broker.return_value = object()
        cluster = MagicMock()
        cluster.is_running = True
        cluster.stop.side_effect = RuntimeError("cluster cleanup failure")
        cluster_class.return_value = cluster
        start_worker_heartbeat.return_value = uuid.uuid4()
        acquire_scheduler_lease.return_value = SchedulerClaim(
            owner_id="owner",
            lease_token=uuid.uuid4(),
        )

        with self.assertLogs("jobs.management.commands.run_job_worker", level="WARNING") as logs:
            with self.assertRaisesMessage(RuntimeError, "primary scheduler failure"):
                call_command("run_job_worker", "--once")

        rendered = "\n".join(logs.output)
        self.assertIn("scheduler_lease_release_failed", rendered)
        self.assertIn("job_cluster_stop_failed", rendered)
        self.assertIn("worker_heartbeat_stop_failed", rendered)
        self.assertNotIn("cleanup failure", rendered)

    def test_runtime_entrypoints_use_combined_worker_command(self) -> None:
        repository_root = Path(__file__).parents[2]
        entrypoint = (repository_root / "entrypoint.sh").read_text()
        makefile = (repository_root / "Makefile").read_text()

        self.assertIn("manage.py run_job_worker", entrypoint)
        self.assertIn("manage.py run_job_worker", makefile)
        self.assertNotIn("manage.py qcluster", entrypoint)
        self.assertNotIn("manage.py qcluster", makefile)
