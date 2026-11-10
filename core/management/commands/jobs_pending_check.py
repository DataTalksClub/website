"""Fail when a due durable job remains pending beyond its age threshold."""

from __future__ import annotations

from datetime import timedelta

from community_base.jobs.models import JobIntent
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Check whether due durable jobs are stuck in pending state"
    requires_system_checks = []

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-age-minutes",
            type=int,
            default=30,
            help="Fail when a due pending intent is older than this many minutes (default: 30).",
        )

    def handle(self, *args, **options) -> None:
        del args
        max_age_minutes = options["max_age_minutes"]
        if max_age_minutes < 1:
            raise CommandError("max-age-minutes must be at least 1")

        now = timezone.now()
        cutoff = now - timedelta(minutes=max_age_minutes)
        overdue = JobIntent.objects.filter(
            status=JobIntent.Status.PENDING,
            created_at__lt=cutoff,
            available_at__lte=now,
        )
        count = overdue.count()
        if count == 0:
            self.stdout.write(
                f"jobs queue ok pending_overdue=0 threshold_minutes={max_age_minutes}"
            )
            return

        oldest_created_at = (
            overdue.order_by("created_at").values_list("created_at", flat=True).first()
        )
        oldest_age_minutes = int((now - oldest_created_at).total_seconds() // 60)
        self.stderr.write(
            "jobs queue overdue "
            f"pending_count={count} oldest_age_minutes={oldest_age_minutes} "
            f"threshold_minutes={max_age_minutes}"
        )
        raise CommandError("pending durable jobs exceed the age threshold")
