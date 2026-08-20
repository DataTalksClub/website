from __future__ import annotations

import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from events.qna.services import retry_event_qna_provision


class Command(BaseCommand):
    help = "Retry one blocked Event Q&A provisioning intent without creating a duplicate."

    def add_arguments(self, parser) -> None:
        parser.add_argument("event_id", help="Canonical Event UUID to retry.")

    def handle(self, *args, **options):
        del args
        event_id = options["event_id"]
        try:
            parsed_event_id = uuid.UUID(event_id)
        except ValueError as exc:
            raise CommandError("event_id must be a canonical UUID") from exc
        if str(parsed_event_id) != event_id:
            raise CommandError("event_id must be a canonical UUID")
        try:
            result = retry_event_qna_provision(parsed_event_id)
        except LookupError as exc:
            raise CommandError("event was not found") from exc
        self.stdout.write(
            json.dumps(
                {
                    "event_id": event_id,
                    "job_id": str(result.job.id),
                    "job_status": result.job.status,
                    "session_id": str(result.session.id),
                },
                sort_keys=True,
            )
        )
