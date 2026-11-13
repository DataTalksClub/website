from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from course_management.package_mail import PURPOSE_DEADLINE_REMINDER
from course_management.relay_campaigns import RelayCampaignError, submit_transient_list
from courses.deadline_reminder_events import (
    build_reminder_events,
    reminder_event_member_count,
)


def aware_now(value: str):
    if not value:
        return timezone.now()

    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError("--now must be an ISO-8601 datetime.")
    if timezone.is_naive(parsed):
        current_timezone = timezone.get_current_timezone()
        parsed = timezone.make_aware(parsed, current_timezone)
    return parsed


def member_user_ids(event):
    ids = []
    for member in event.members:
        user_id = (member.get("metadata") or {}).get("user_id")
        if user_id is not None:
            ids.append(user_id)
    return ids


def reminder_users(events):
    """Learners referenced by the reminder members, keyed by id.

    The package records the recipient user on the delivery, which the
    studio views and the preference resolver both read.
    """

    ids = {user_id for event in events for user_id in member_user_ids(event)}
    if not ids:
        return {}
    users = get_user_model().objects.filter(pk__in=ids)
    return {user.pk: user for user in users}


def send_reminder_event(event, users=None):
    """Submit one reminder event in ONE request to Relay."""
    del users
    if not event.members:
        return []

    context = event.send_payload["context"]
    members = []
    for member in event.members:
        metadata = member.get("metadata") or {}
        members.append(
            {
                "source_object_key": member.get("source_object_key", ""),
                "email": member["email"],
                "metadata": {
                    "course_title": context["course_title"],
                    "course_url": context["action_url"],
                    "deadline": metadata.get("deadline_at") or context["deadline_at"],
                },
            }
        )
    try:
        submit_transient_list(
            list_key=event.list_key,
            name=event.list_name,
            template_key=PURPOSE_DEADLINE_REMINDER,
            idempotency_key=event.key,
            context={
                "course_title": context["course_title"],
                "course_url": context["action_url"],
                "deadline": context["deadline_at"],
            },
            members=members,
            category="email_deadline_reminders",
        )
    except RelayCampaignError as error:
        return [(member, str(error)) for member in event.members]
    return []


class Command(BaseCommand):
    help = "Send deadline reminders through the package mail app."

    def add_arguments(self, parser):
        parser.add_argument(
            "--course-slug",
            default="",
            help="Limit reminders to one course cohort slug.",
        )
        parser.add_argument(
            "--now",
            default="",
            help="Override current time with an ISO-8601 datetime.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print planned reminder sends without sending mail.",
        )

    def handle(self, *args, **options):
        now = aware_now(options["now"])
        events = build_reminder_events(
            now,
            course_slug=options["course_slug"],
        )
        total_members = reminder_event_member_count(events)
        self.stdout.write(f"Prepared {len(events)} reminder event(s), {total_members} member(s).")

        if options["dry_run"]:
            self.write_dry_run_events(events)
            return

        self.send_events(events)

    def write_dry_run_events(self, events):
        for event in events:
            self.stdout.write(f"{event.list_key}: {len(event.members)} member(s)")

    def send_events(self, events):
        users = reminder_users(events)
        total_failures = 0
        for event in events:
            failures = send_reminder_event(event, users)
            total_failures += len(failures)
            if failures:
                for member, error in failures:
                    self.stderr.write(
                        f"Failed {event.list_key} {member.get('source_object_key')}: {error}"
                    )
                continue
            self.stdout.write(f"Sent {event.list_key}: {len(event.members)} member(s)")

        if not total_failures:
            return

        # Raise only after every event has been attempted, so the task
        # still exits non-zero and surfaces in CloudWatch.
        raise CommandError(f"{total_failures} reminder member(s) failed to record.")
