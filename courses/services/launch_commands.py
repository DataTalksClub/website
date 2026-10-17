"""One transactional command boundary for Studio, API and CLI."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError

from core.audit import AuditWriteContext, record_audit_event
from core.idempotency import execute_idempotent
from courses.models import Cohort
from courses.services import launch_announcements as announcements
from courses.services import launch_assignments as assignments
from courses.services import launch_batch as batch
from courses.services import launch_templates as templates
from courses.services.launch_generation import generate
from courses.services.launch_test_send import test_send
from courses.services.launch_timing import update_timing
from courses.services.launch_validation import LaunchError, load_row

WRITE_ACTIONS = {
    "template_save": (
        templates.save_template,
        {
            "id",
            "expected_revision",
            "name",
            "channel",
            "kind",
            "subject",
            "body",
            "offset_minutes",
            "seed_key",
        },
    ),
    "template_publish": (templates.publish_template, {"id", "expected_revision"}),
    "template_archive": (templates.archive_template, {"id", "expected_revision", "archived"}),
    "course_create": (assignments.create_course, {"slug", "title", "version_ids"}),
    "cohort_create": (
        assignments.create_cohort,
        {
            "course_id",
            "slug",
            "identifier",
            "title",
            "description",
            "year",
            "delivery_mode",
            "curriculum_format",
        },
    ),
    "assignment_save": (
        assignments.save_assignment,
        {
            "course_id",
            "cohort_id",
            "version_ids",
            "expected_revision",
            "override_defaults",
            "telegram_destination_key",
        },
    ),
    "timing_update": (
        update_timing,
        {"target_kind", "id", "starts_at", "provenance", "expected_revision"},
    ),
    "launch": (
        lambda data: generate(load_row(Cohort, data["cohort_id"], lock=True)),
        {"cohort_id"},
    ),
    "compose": (
        announcements.compose,
        {"cohort_id", "version_id", "target_kind", "target_id", "context"},
    ),
    "announcement_edit": (
        announcements.edit_announcement,
        {"id", "subject", "body", "context", "expected_revision"},
    ),
    "approve": (announcements.approve, {"id", "expected_revision"}),
    "cancel": (announcements.cancel, {"id", "expected_revision"}),
}
SEND_ACTIONS = {
    "test_send": (test_send, {"id", "destination", "confirmed"}),
    "schedule": (
        announcements.schedule_announcement,
        {"id", "expected_revision", "scheduled_at", "confirmed"},
    ),
    "send": (announcements.schedule_announcement, {"id", "expected_revision", "confirmed"}),
    "launch_and_schedule": (
        batch.execute_batch,
        {"cohort_id", "confirmed", "preview_token"},
    ),
}


def audited_command(action, callback, payload, context):
    result = callback(payload)
    record_audit_event(
        action=f"courses.launch.{action}",
        target_type="courses.launch",
        target_label="course-launch",
        outcome="succeeded",
        context=AuditWriteContext.from_service_context(context),
        changes={},
        metadata={"action": action},
    )
    return result


def execute_launch(payload, context, actions):
    action = payload.get("action")
    if not isinstance(action, str) or action not in actions:
        raise LaunchError("Choose a supported launch action.")
    callback, allowed = actions[action]
    if set(payload) - allowed - {"action"}:
        raise LaunchError("The command contains unsupported fields.")
    if action in SEND_ACTIONS and payload.get("confirmed") is not True:
        raise LaunchError("Review the preview, audience and destination, then explicitly confirm.")

    try:
        result = execute_idempotent(
            scope=f"course-launch:{context.actor_ref}",
            key=context.idempotency_key or "",
            request=payload,
            command=lambda: audited_command(action, callback, payload, context),
        )
    except (KeyError, TypeError, ValidationError, IntegrityError) as error:
        raise LaunchError(
            "Check the command fields and identifiers; no changes were saved."
        ) from error
    return {**result.value, "replayed": result.replayed}


def write_launch(payload, *, context):
    return execute_launch(payload, context, WRITE_ACTIONS)


def send_launch(payload, *, context):
    return execute_launch(payload, context, SEND_ACTIONS)
