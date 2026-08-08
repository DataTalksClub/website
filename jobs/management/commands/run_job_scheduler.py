from __future__ import annotations

import uuid
from time import sleep

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django_q.scheduler import scheduler as q2_scheduler  # type: ignore[import-untyped]

from jobs.scheduler import (
    acquire_scheduler_lease,
    register_code_schedules,
    release_scheduler_lease,
    renew_scheduler_lease,
    require_non_owner_scheduler_disabled,
)


class Command(BaseCommand):
    help = "Run the one database-leased owner for code schedules."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=30)
        parser.add_argument("--lease-seconds", type=int, default=90)

    def handle(self, *args, **options) -> None:
        del args
        require_non_owner_scheduler_disabled()
        interval = options["interval"]
        lease_seconds = options["lease_seconds"]
        if not 1 <= interval < lease_seconds:
            raise CommandError("scheduler interval must be positive and shorter than its lease")
        owner_id = f"scheduler-{uuid.uuid4().hex}"
        claim = acquire_scheduler_lease(owner_id, ttl_seconds=lease_seconds)
        if claim is None:
            raise CommandError("another scheduler owner holds the lease")
        try:
            while True:
                registered = register_code_schedules(claim)
                if registered == 0:
                    raise CommandError("scheduler lease was lost before registration")
                q2_scheduler()
                if options["once"]:
                    self.stdout.write(f"registered={registered}")
                    return
                sleep(interval)
                if not renew_scheduler_lease(claim, ttl_seconds=lease_seconds):
                    raise CommandError("scheduler lease was lost")
        except KeyboardInterrupt:
            self.stdout.write("scheduler interrupted")
        finally:
            release_scheduler_lease(claim)
