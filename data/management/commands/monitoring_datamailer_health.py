import json

from django.core.management.base import BaseCommand

from django.db import models
from django.utils import timezone

from data.models import DatamailerOutboxEvent
from course_management.observability import record_event
from data.management.commands.datamailer_callback_status import (
    callback_event_aggregate,
    callback_event_counts,
)
from data.management.commands.datamailer_send_status import (
    datamailer_send_audit_summary,
)


class Command(BaseCommand):
    help = "Emit compact Datamailer health observability events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print the emitted health payload as JSON.",
        )

    def handle(self, *args, **options):
        payload = datamailer_health_payload()
        record_event("datamailer.health_checked", properties=payload)
        if payload["status"] != "ok":
            record_event("datamailer.health_warning", properties=payload)

        if options["json"]:
            self.stdout.write(json.dumps(payload, default=str, indent=2))
            return

        self.stdout.write("Datamailer monitoring health emitted")


def datamailer_health_payload():
    outbox_counts = dict(
        DatamailerOutboxEvent.objects.values_list("status")
        .annotate(count=models.Count("id"))
        .values_list("status", "count"),
    )
    due_count = DatamailerOutboxEvent.objects.filter(
        status__in=("pending", "retrying"),
        next_attempt_at__lte=timezone.now(),
    ).count()
    send_summary = datamailer_send_audit_summary(limit=5)
    callback_aggregate = callback_event_aggregate()
    callback_counts = callback_event_counts()
    failed_sends = send_summary["totals"]["failed"]
    failed_outbox = outbox_counts.get("failed", 0)
    retrying_outbox = outbox_counts.get("retrying", 0)
    status = "ok"
    if failed_sends or failed_outbox or retrying_outbox:
        status = "warning"

    return {
        "status": status,
        "outbox_due_count": due_count,
        "outbox_pending_count": outbox_counts.get("pending", 0),
        "outbox_retrying_count": retrying_outbox,
        "outbox_failed_count": failed_outbox,
        "outbox_acked_count": outbox_counts.get("acked", 0),
        "send_total_count": send_summary["totals"]["total"],
        "send_failed_count": failed_sends,
        "send_succeeded_count": send_summary["totals"]["succeeded"],
        "callback_total_count": callback_aggregate["total"],
        "callback_duplicate_count": callback_aggregate["duplicates"] or 0,
        "callback_event_type_count": len(callback_counts),
    }
