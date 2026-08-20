from __future__ import annotations

import json
import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from events.models import Event, EventQnaSession
from events.qna.services import ensure_event_qna


class Command(BaseCommand):
    help = "Create missing draft Event Q&A sessions and durable provisioning intents."

    def add_arguments(self, parser) -> None:
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Report missing rows without changing the database (the default).",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Apply the idempotent relation/job ensure operation.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Maximum number of Events to inspect (1-1000; default 100).",
        )
        parser.add_argument(
            "--after-event-id",
            default=None,
            help="Resume after this canonical Event UUID.",
        )

    def handle(self, *args, **options):
        del args
        limit = options["limit"]
        if not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise CommandError("--limit must be between 1 and 1000")
        after_event_id = options["after_event_id"]
        if after_event_id is not None:
            try:
                after_event_id = uuid.UUID(after_event_id)
            except ValueError as exc:
                raise CommandError("--after-event-id must be a canonical UUID") from exc
            if str(after_event_id) != options["after_event_id"]:
                raise CommandError("--after-event-id must be a canonical UUID")

        event_query = Event.objects.order_by("id")
        if after_event_id is not None:
            event_query = event_query.filter(id__gt=after_event_id)
        events = tuple(event_query[:limit])
        dry_run = not options["apply"]
        report: dict[str, Any] = {
            "dry_run": dry_run,
            "events_seen": len(events),
            "sessions_created": 0,
            "jobs_created": 0,
            "already_provisioned": 0,
            "failures": 0,
            "next_after_event_id": str(events[-1].id) if events else None,
        }

        for event in events:
            if dry_run:
                report["sessions_created"] += int(
                    not EventQnaSession.objects.filter(event_id=event.id).exists()
                )
                continue
            try:
                result = ensure_event_qna(event.id)
            except Exception:
                # The durable row is the retry boundary.  Keep command output
                # aggregate-only and let the next bounded invocation revisit a
                # failed Event without exposing implementation details.
                report["failures"] += 1
                continue
            report["sessions_created"] += int(result.session_created)
            report["jobs_created"] += int(result.job_created)
            report["already_provisioned"] += int(
                not result.session_created and not result.job_created
            )

        self.stdout.write(json.dumps(report, sort_keys=True))
