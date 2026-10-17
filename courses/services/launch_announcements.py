"""Announcement approval and scheduling state transitions."""

from community_base.jobs.dispatch import dispatch_after_commit
from django.utils import timezone

from courses.models import Announcement, AnnouncementTemplateVersion, Cohort
from courses.services.launch_context import announcement_context, preview, safe_preview
from courses.services.launch_generation import SETTLED, propose_schedule
from courses.services.launch_timing import owning_cohort, timing_owner
from courses.services.launch_validation import (
    LaunchError,
    aware_datetime,
    iso,
    live_cohort,
    load_row,
    require_revision,
    text,
)


def serialize_announcement(row):
    from courses.services.launch_status import delivery_summary, eligible_count, schedule_display
    from courses.services.launch_transport import telegram_destination_reference, transport_mode

    return {
        **schedule_display(row),
        "audience_count": eligible_count(row),
        "delivery_states": delivery_summary(row),
        "transport_mode": transport_mode(),
        "id": str(row.pk),
        "cohort_id": str(row.cohort_id),
        "channel": row.channel,
        "telegram_destination_key": (
            telegram_destination_reference(row) if row.channel == "telegram" else ""
        ),
        "target_kind": row.target_kind,
        "target_id": row.target_id,
        "subject": row.subject,
        "body": row.body,
        "context": row.context,
        "context_overrides": row.context_overrides,
        "status": row.status,
        "scheduled_at": iso(row.scheduled_at),
        "anchor_at": iso(row.anchor_at),
        "revision": row.revision,
        "reason_code": row.reason_code,
        "preview": safe_preview(row),
    }


def compose(payload):
    cohort = load_row(Cohort, payload["cohort_id"])
    live_cohort(cohort)
    version = load_row(AnnouncementTemplateVersion, payload["version_id"])
    kind = payload.get("target_kind", "cohort")
    expected_kind = kind
    if kind == "shared_module_placement":
        expected_kind = "module"
    if version.kind != expected_kind:
        raise LaunchError("Choose a template for this start owner kind.")
    identity = payload.get("target_id", str(cohort.pk))
    target = timing_owner(kind, identity)
    if owning_cohort(kind, target).pk != cohort.pk:
        raise LaunchError("Target does not belong to this cohort.")
    row = Announcement(
        cohort=cohort,
        version=version,
        channel=version.channel,
        target_kind=kind,
        target_id=str(identity),
        subject=version.subject,
        body=version.body,
        context=announcement_context(kind, identity, payload.get("context")),
        context_overrides=payload.get("context") or {},
    )
    preview(row)
    row.save()
    return serialize_announcement(row)


def editable(payload):
    row = load_row(Announcement, payload["id"], lock=True)
    live_cohort(row.cohort)
    require_revision(row, payload["expected_revision"])
    if row.status in SETTLED:
        raise LaunchError(
            "This announcement is already dispatching or settled. Inspect delivery status.",
            status=409,
        )
    return row


def edit_announcement(payload):
    row = editable(payload)
    row.subject = text(payload["subject"], "subject", limit=200, blank=row.channel == "telegram")
    row.body = text(payload["body"], "body")
    row.context_overrides = payload.get("context", row.context_overrides) or {}
    row.context = announcement_context(row.target_kind, row.target_id, row.context_overrides)
    if row.generated_key:
        propose_schedule(row, timing_owner(row.target_kind, row.target_id).starts_at)
    preview(row)
    row.status = "draft"
    row.approved_revision = None
    row.revision += 1
    row.save()
    return serialize_announcement(row)


def approve(payload):
    row = editable(payload)
    preview(row)
    row.status = "approved"
    row.revision += 1
    row.approved_revision = row.revision
    row.save(update_fields=["status", "revision", "approved_revision"])
    return serialize_announcement(row)


def schedule_announcement(payload):
    row = editable(payload)
    if row.status not in {"approved", "scheduled"} or row.approved_revision != row.revision:
        raise LaunchError("Approve the current copy before scheduling or sending.")
    due = timezone.now()
    if payload["action"] == "schedule":
        due = aware_datetime(payload.get("scheduled_at"))
        if due is None or due <= timezone.now():
            raise LaunchError(
                "Choose a future timestamp. Past schedules never catch up automatically."
            )
    validate_anchor(row)
    row.status = "scheduled"
    row.scheduled_at = due
    row.reason_code = ""
    row.revision += 1
    row.approved_revision = row.revision
    row.save(
        update_fields=["status", "scheduled_at", "revision", "approved_revision", "reason_code"]
    )
    row.deliveries.filter(state="blocked").update(state="pending")
    dispatch_after_commit(
        "courses.announcement",
        f"announcement:{row.pk}:{row.revision}",
        {"announcement_id": str(row.pk), "revision": row.revision},
        available_at=due,
    )
    return serialize_announcement(row)


def validate_anchor(row):
    if not row.generated_key:
        return
    target = timing_owner(row.target_kind, row.target_id)
    if target.starts_at is None or target.starts_at != row.anchor_at:
        raise LaunchError("Start is missing or changed. Edit and review the schedule.")


def cancel(payload):
    row = editable(payload)
    row.status = "cancelled"
    row.approved_revision = None
    row.revision += 1
    row.save(update_fields=["status", "approved_revision", "revision"])
    return serialize_announcement(row)
