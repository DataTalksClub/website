from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from time import sleep

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django_q.brokers import get_broker  # type: ignore[import-untyped]
from django_q.cluster import Cluster  # type: ignore[import-untyped]
from django_q.scheduler import scheduler as q2_scheduler  # type: ignore[import-untyped]

from jobs.heartbeat import (
    renew_worker_heartbeat,
    start_worker_heartbeat,
    stop_worker_heartbeat,
)
from jobs.scheduler import (
    SchedulerClaim,
    acquire_scheduler_lease,
    register_code_schedules,
    release_scheduler_lease,
    renew_scheduler_lease,
    require_non_owner_scheduler_disabled,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run a Django-Q worker cluster with shared heartbeat and leased scheduling."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=30)
        parser.add_argument("--lease-seconds", type=int, default=90)
        parser.add_argument("--heartbeat-seconds", type=int, default=90)

    def handle(self, *args, **options) -> None:
        del args
        require_non_owner_scheduler_disabled()
        interval = options["interval"]
        lease_seconds = options["lease_seconds"]
        heartbeat_seconds = options["heartbeat_seconds"]
        if not 1 <= interval < lease_seconds:
            raise CommandError("worker interval must be positive and shorter than scheduler lease")
        if not 1 <= interval < heartbeat_seconds:
            raise CommandError("worker interval must be positive and shorter than heartbeat lease")

        worker_id = f"job-worker-{uuid.uuid4().hex}"
        # The cluster's sentinel creates its own broker after process startup. Passing an
        # already-connected ORM broker here would risk carrying a live DB connection across fork.
        cluster = Cluster()
        cluster_started = False
        heartbeat_token: uuid.UUID | None = None
        scheduler_claim: SchedulerClaim | None = None

        try:
            cluster.start()
            cluster_started = True
            if not self._cluster_is_healthy(cluster):
                raise CommandError("Django-Q worker sentinel did not start")
            scheduler_broker = get_broker()
            heartbeat_token = start_worker_heartbeat(
                worker_id,
                ttl_seconds=heartbeat_seconds,
                metadata={"role": "durable-jobs"},
            )

            while self._cluster_is_healthy(cluster):
                if not renew_worker_heartbeat(
                    worker_id,
                    heartbeat_token,
                    ttl_seconds=heartbeat_seconds,
                ):
                    raise CommandError("worker heartbeat lease was lost")
                if not self._cluster_is_healthy(cluster):
                    raise CommandError("Django-Q worker sentinel stopped unexpectedly")

                scheduler_claim = self._run_scheduler_pass(
                    worker_id=worker_id,
                    current_claim=scheduler_claim,
                    lease_seconds=lease_seconds,
                    broker=scheduler_broker,
                )
                if options["once"]:
                    self.stdout.write(
                        f"scheduler_owner={'yes' if scheduler_claim is not None else 'no'}"
                    )
                    return
                sleep(interval)
            raise CommandError("Django-Q worker sentinel stopped unexpectedly")
        except KeyboardInterrupt:
            self.stdout.write("job worker interrupted")
        finally:
            if scheduler_claim is not None:
                self._cleanup(
                    "scheduler_lease_release_failed",
                    lambda: release_scheduler_lease(scheduler_claim),
                )
            if cluster_started:
                self._cleanup("job_cluster_stop_failed", cluster.stop)
            if heartbeat_token is not None:
                self._cleanup(
                    "worker_heartbeat_stop_failed",
                    lambda: stop_worker_heartbeat(worker_id, heartbeat_token),
                )

    @staticmethod
    def _cleanup(event: str, cleanup: Callable[[], object]) -> None:
        """Best-effort cleanup; leases expire and cleanup errors never hide the primary failure."""

        try:
            cleanup()
        except Exception:
            logger.warning(event)

    @staticmethod
    def _cluster_is_healthy(cluster: Cluster) -> bool:
        """Require both Q2's Event state and the actual sentinel process to be alive."""

        sentinel = cluster.sentinel
        return bool(cluster.is_running and sentinel is not None and sentinel.is_alive())

    @staticmethod
    def _run_scheduler_pass(
        *,
        worker_id: str,
        current_claim: SchedulerClaim | None,
        lease_seconds: int,
        broker: object,
    ) -> SchedulerClaim | None:
        claim = current_claim
        if claim is not None and not renew_scheduler_lease(
            claim,
            ttl_seconds=lease_seconds,
        ):
            release_scheduler_lease(claim)
            claim = None

        if claim is None:
            claim = acquire_scheduler_lease(worker_id, ttl_seconds=lease_seconds)
        if claim is None:
            return None

        try:
            registered = register_code_schedules(claim)
            if registered == 0:
                release_scheduler_lease(claim)
                return None
            q2_scheduler(broker=broker)
        except Exception:
            Command._cleanup(
                "scheduler_lease_release_failed",
                lambda: release_scheduler_lease(claim),
            )
            raise
        return claim
