"""Poll durable jobs without bootstrapping Django again on every empty tick."""

import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections


class Command(BaseCommand):
    help = "Run the dedicated domain worker with a ten-second polling pause"
    requires_system_checks = []

    def handle(self, *args, **options):
        while True:
            close_old_connections()
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
                    # Preserve the former shell loop's per-command retry. Do
                    # not include exception text, which may hold job data.
                    self.stderr.write(f"{name} failed ({type(error).__name__}); retry next poll")
            time.sleep(10)
