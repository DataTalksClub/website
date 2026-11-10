"""Poll durable jobs without bootstrapping Django again on every empty tick."""

import logging
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from core.safe_logging import safe_traceback
from core.worker_healthcheck import touch_worker_heartbeat

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the dedicated domain worker with a ten-second polling pause"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit 0 when the job-intent table is reachable, else non-zero.",
        )

    def handle(self, *args, **options):
        if options.get("check"):
            self._check()
            return
        while True:
            close_old_connections()
            touch_worker_heartbeat()
            for name in ("jobs_run_due", "jobs_sweep"):
                try:
                    call_command(
                        name,
                        limit=100,
                        skip_checks=True,
                        stdout=self.stdout,
                        stderr=self.stderr,
                    )
                except Exception as error:
                    # Preserve the former shell loop's per-command retry.
                    # Safe frame locations help diagnose it without logging
                    # exception text, which may hold job data or credentials.
                    logger.error(
                        "%s failed (%s); retry next poll",
                        name,
                        type(error).__name__,
                        extra={
                            "exception_type": type(error).__name__,
                            "traceback": safe_traceback(
                                (type(error), error, error.__traceback__)
                            ),
                        },
                    )
            time.sleep(10)

    def _check(self):
        # Compose/worker healthcheck: prove the worker can reach the durable
        # job table. No job data is printed; any failure exits non-zero.
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM cb_jobs_jobintent LIMIT 1")
                cursor.fetchone()
        except Exception as error:
            raise SystemExit(f"jobs_worker unhealthy ({type(error).__name__})") from error
        self.stdout.write("jobs_worker ok")
