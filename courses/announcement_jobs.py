"""Revision-fenced handlers, registered on the existing durable scheduler."""

from community_base.jobs.registry import register_handler
from django.db import transaction
from django.utils import timezone

from courses.models import Announcement
from courses.services.launch_announcements import validate_anchor
from courses.services.launch_transport import (
    submit_email,
    submit_telegram,
    telegram_destination_reference,
)
from courses.services.launch_validation import LaunchError, live_cohort, serialize_rows


@register_handler("courses.announcement")
def dispatch_announcement(context, payload):
    del context
    with transaction.atomic():
        rows = Announcement.objects.filter(pk=payload.get("announcement_id"))
        serialize_rows(rows)
        row = rows.first()
        if row is None or row.revision != payload.get("revision"):
            return
        if row.status == "dispatching":
            row.status = "ambiguous"
            row.reason_code = "interrupted_dispatch"
            row.save(update_fields=["status", "reason_code"])
            return
        if not is_due(row):
            return
        if not validate_current(row):
            return
        if row.channel == "telegram":
            row._telegram_destination_key = telegram_destination_reference(row)
        row.status = "dispatching"
        row.save(update_fields=["status"])
    status = "submitted"
    if row.channel == "email":
        submit_email(row)
    else:
        status = submit_telegram(row)
    Announcement.objects.filter(pk=row.pk, status="dispatching").update(status=status)


def validate_current(row):
    try:
        live_cohort(row.cohort)
        validate_anchor(row)
    except LaunchError:
        row.status = "draft"
        row.reason_code = "start_or_delivery_changed"
        row.approved_revision = None
        row.save(update_fields=["status", "reason_code", "approved_revision"])
        return False
    return True


def is_due(row):
    return (
        row.status == "scheduled"
        and row.scheduled_at is not None
        and row.scheduled_at <= timezone.now()
    )
