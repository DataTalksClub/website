from django.core.management.base import BaseCommand, CommandParser

from jobs.dispatch import relay_due_jobs
from jobs.execution import sweep_expired_jobs
from jobs.heartbeat import prune_stale_heartbeats


class Command(BaseCommand):
    help = "Recover expired durable jobs and enqueue due disposable Q2 wakeups."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options) -> None:
        del args
        limit = options["limit"]
        recovered, exhausted = sweep_expired_jobs(limit=limit)
        relayed = relay_due_jobs(limit=limit)
        stale = prune_stale_heartbeats(limit=limit)
        self.stdout.write(
            f"recovered={recovered} exhausted={exhausted} relayed={relayed} stale={stale}"
        )
