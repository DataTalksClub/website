"""Mail to a person holding a confirmed seat: "you're registered" and the reminders.

Two purposes, both in ``email_templates/``:

``event-registration-confirmed``
    Sent once per seat (per registration version) at the moment the seat
    becomes confirmed through the site: a signed-in visitor's one-click
    registration, or the link in the "confirm your registration" email.  The
    registration views call :func:`send_registration_confirmed`; nothing else
    does, so a row written by an import script (Luma, Eventbrite, the Academy
    sessions move) never triggers it.

``event-reminder``
    Sent before the event at each of the event's reminder intervals
    (``EventOperatorConfig.reminder_intervals``, default ``24h,20m``, edited
    with ``dtc events reminders``).  :func:`plan_due_reminders` runs every five
    minutes as the durable job ``event_registrants.plan_reminders`` (a
    registered schedule, synced to Relay by ``manage.py sync_relay_schedules``
    and executed by the worker): it writes the package ``EventReminder`` row
    for each interval that has come due, claims the due rows and queues one
    ``event_registrants.send_reminder`` job per row.

Both carry the event's title, its date and time in the registrant's timezone
(the registration's, else the account's preferred one, else Berlin), the event
page and an "Add to calendar" link.  The Zoom join link -- private, the event's
``zoom_join_url`` -- is added only for a Zoom event and only to a confirmed
seat.

Imported seats are never mailed: a registration whose
``acquisition_metadata["source"]`` names an import (:data:`IMPORTED_SOURCES`)
gets no confirmation and no reminder.  The Academy session signups moved by
``scripts/prod/import_academy_sessions.py`` are such seats; the Academy signup
backend keeps reminding them itself.
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.utils import timezone

CONFIRMED_PURPOSE = "event-registration-confirmed"
REMINDER_PURPOSE = "event-reminder"
MAIL_CATEGORY = "events"
DEFAULT_TIMEZONE = "Europe/Berlin"
#: ``acquisition_metadata["source"]`` values written by import scripts.
IMPORTED_SOURCES = frozenset({"academy-sessions"})
#: The fallback when an event has no ``EventOperatorConfig`` row; the same
#: default ``events.admin_services`` reports.
DEFAULT_REMINDER_INTERVALS: tuple[str, ...] = ("24h", "20m")
PLAN_HANDLER = "event_registrants.plan_reminders"
SEND_HANDLER = "event_registrants.send_reminder"
#: The package's own reminder schedule.  It plans fixed 24h/20m reminders for
#: every event and sends a purpose this site has no template for; the site's
#: planner above replaces it, so the site drops it from the registry.
PACKAGE_REMINDER_SCHEDULE = "events.plan_reminders.every_15_minutes"

_INTERVAL = re.compile(r"^(\d{1,4})([dhm])$")
_UNITS = {"d": "days", "h": "hours", "m": "minutes"}


# --- who and what -----------------------------------------------------------------


def is_imported(registration: Any) -> bool:
    return (registration.acquisition_metadata or {}).get("source") in IMPORTED_SOURCES


def parse_interval(value: str) -> timedelta | None:
    match = _INTERVAL.fullmatch(str(value).strip().lower())
    if match is None or int(match.group(1)) == 0:
        return None
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


def reminder_intervals(event: Any) -> dict[str, timedelta]:
    """The event's reminder intervals, ``{}`` when its reminders are off."""

    from .models import EventOperatorConfig

    config = (
        EventOperatorConfig.objects.filter(event=event)
        .only("reminders_enabled", "reminder_intervals")
        .first()
    )
    if config is not None and not config.reminders_enabled:
        return {}
    labels = (config.reminder_intervals if config is not None else None) or list(
        DEFAULT_REMINDER_INTERVALS
    )
    parsed = {str(label): parse_interval(label) for label in labels}
    return {label: delta for label, delta in parsed.items() if delta is not None}


def registrant_timezone(registration: Any) -> ZoneInfo:
    user = registration.user
    for name in (registration.timezone, getattr(user, "preferred_timezone", "") if user else ""):
        if name:
            try:
                return ZoneInfo(str(name).strip())
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo(DEFAULT_TIMEZONE)


def event_when(event: Any, zone: ZoneInfo) -> str:
    """``Monday 30 November 2026, 17:00–18:00 (Europe/Berlin)``."""

    start = event.start_datetime.astimezone(zone)
    end = event.effective_end_datetime.astimezone(zone)
    day = f"{start:%A} {start.day} {start:%B %Y}"
    if end.date() == start.date():
        return f"{day}, {start:%H:%M}–{end:%H:%M} ({zone.key})"
    return f"{day}, {start:%H:%M} ({zone.key})"


def _site_url() -> str:
    return str(settings.COMMUNITY_BASE["SITE_URL"]).rstrip("/")


def event_page_url(event: Any) -> str:
    from events.identity import canonical_detail_path

    return f"{_site_url()}{canonical_detail_path(event.content_id)}"


def join_url_for(registration: Any) -> str:
    """The private join link, only for a confirmed seat at a Zoom event."""

    from community_base.events.models import EventRegistration

    event = registration.event
    if registration.status != EventRegistration.Status.CONFIRMED:
        return ""
    if event.platform != "zoom" or not event.zoom_join_url:
        return ""
    return str(event.zoom_join_url)


def google_calendar_url(event: Any, *, page_url: str, join_url: str) -> str:
    stamp = "%Y%m%dT%H%M%SZ"
    start = event.start_datetime.astimezone(ZoneInfo("UTC"))
    end = event.effective_end_datetime.astimezone(ZoneInfo("UTC"))
    details = f"Event page: {page_url}"
    if join_url:
        details = f"Join on Zoom: {join_url}\n{details}"
    query = urlencode(
        {
            "action": "TEMPLATE",
            "text": event.title,
            "dates": f"{start.strftime(stamp)}/{end.strftime(stamp)}",
            "details": details,
            "location": join_url or page_url,
        }
    )
    return f"https://calendar.google.com/calendar/render?{query}"


def seat_context(registration: Any) -> dict[str, str]:
    event = registration.event
    page_url = event_page_url(event)
    join_url = join_url_for(registration)
    context = {
        "event_title": event.title,
        "event_when": event_when(event, registrant_timezone(registration)),
        "event_url": page_url,
        "calendar_url": google_calendar_url(event, page_url=page_url, join_url=join_url),
    }
    if join_url:
        context["join_url"] = join_url
    return context


# --- the confirmation -------------------------------------------------------------


def send_registration_confirmed(registration: Any):
    """Queue "you're registered" for a seat that just became confirmed.

    Once per registration version: a replay returns the first delivery.  An
    imported seat, or one that is not confirmed, gets nothing.
    """

    from community_base.events.models import EventRegistration

    from course_management.package_mail import mail_idempotency_key, send_package_mail

    if registration.status != EventRegistration.Status.CONFIRMED or is_imported(registration):
        return None
    return send_package_mail(
        purpose=CONFIRMED_PURPOSE,
        to=registration.normalized_email,
        context=seat_context(registration),
        idempotency_key=mail_idempotency_key(
            CONFIRMED_PURPOSE, str(registration.pk), str(registration.version)
        ),
        category=MAIL_CATEGORY,
        user=registration.user,
    )


# --- the reminders ----------------------------------------------------------------


def _starts_in(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return "tomorrow" if days == 1 else f"in {days} days"
    if minutes % 60 == 0:
        hours = minutes // 60
        return "in 1 hour" if hours == 1 else f"in {hours} hours"
    return f"in {minutes} minutes"


def plan_due_reminders(*, now: datetime | None = None) -> int:
    """Write the ``EventReminder`` rows that have come due; return how many.

    For each confirmed, non-imported seat at a published event that has not
    started, the interval whose moment (start minus interval) has passed and
    is nearest the start gets a row -- one row per seat, version and interval,
    so a reminder is sent once.  An interval whose moment passed before the
    seat was confirmed is skipped: the confirmation email just said it all.
    """

    from community_base.events.models import Event, EventRegistration, EventReminder

    from events.lifecycle import PUBLISHED_STATUSES

    now = now or timezone.now()
    created = 0
    events = Event.objects.filter(status__in=PUBLISHED_STATUSES, start_datetime__gt=now)
    for event in events.filter(start_datetime__lte=now + timedelta(days=8)):
        intervals = reminder_intervals(event)
        due = [
            (label, delta)
            for label, delta in intervals.items()
            if event.start_datetime - delta <= now
        ]
        if not due:
            continue
        label, delta = min(due, key=lambda item: item[1])
        moment = event.start_datetime - delta
        seats = EventRegistration.objects.filter(
            event=event, status=EventRegistration.Status.CONFIRMED
        )
        for registration in seats:
            if is_imported(registration):
                continue
            if moment < (registration.verified_at or registration.created_at):
                continue
            _reminder, was_created = EventReminder.objects.get_or_create(
                registration=registration,
                registration_version=registration.version,
                interval=label[:32],
                defaults={"scheduled_for": moment},
            )
            created += int(was_created)
    return created


def plan_reminders_job(context: Any, payload: dict) -> None:
    from community_base.events.reminders import claim_due_reminders
    from community_base.jobs.dispatch import dispatch_after_commit
    from community_base.jobs.runner import PermanentJobError

    del context
    if payload:
        raise PermanentJobError("invalid_event_reminder_plan_payload")
    with transaction.atomic():
        plan_due_reminders()
        for claimed in claim_due_reminders(limit=500):
            dispatch_after_commit(
                SEND_HANDLER,
                f"{SEND_HANDLER}:{claimed.reminder_id}",
                {"reminder_id": claimed.reminder_id},
            )


def send_reminder(reminder_id: str) -> str:
    """Send one claimed reminder; return what happened as a reason code."""

    from community_base.events.models import EventRegistration, EventReminder
    from community_base.events.reminders import complete_reminder

    from course_management.package_mail import mail_idempotency_key, send_package_mail

    with transaction.atomic():
        reminder = (
            EventReminder.objects.select_for_update()
            .select_related("registration__event", "registration__user")
            .filter(pk=reminder_id)
            .first()
        )
        if reminder is None:
            return "not_found"
        if reminder.status != EventReminder.Status.CLAIMED:
            return "not_claimed"
        registration = reminder.registration
        event = registration.event
        stale = ""
        if (
            registration.version != reminder.registration_version
            or registration.status != EventRegistration.Status.CONFIRMED
        ):
            stale = "registration_inactive"
        elif is_imported(registration):
            stale = "imported_registration"
        elif event.start_datetime <= timezone.now():
            stale = "event_started"
        if stale:
            reminder.status = EventReminder.Status.SKIPPED
            reminder.reason = stale
            reminder.completed_at = timezone.now()
            reminder.save(update_fields=("status", "reason", "completed_at", "updated_at"))
            return stale
        delta = parse_interval(reminder.interval) or (event.start_datetime - reminder.scheduled_for)
        delivery = send_package_mail(
            purpose=REMINDER_PURPOSE,
            to=registration.normalized_email,
            context={**seat_context(registration), "starts_in": _starts_in(delta)},
            idempotency_key=mail_idempotency_key(REMINDER_PURPOSE, str(reminder.pk)),
            category=MAIL_CATEGORY,
            user=registration.user,
        )
        complete_reminder(reminder.pk, delivery)
    return "sent"


def send_reminder_job(context: Any, payload: dict) -> None:
    from community_base.events.reminders import fail_reminder
    from community_base.jobs.runner import PermanentJobError

    del context
    reminder_id = payload.get("reminder_id")
    if not isinstance(reminder_id, str) or not reminder_id:
        raise PermanentJobError("invalid_event_reminder_payload")
    try:
        send_reminder(reminder_id)
    except Exception as error:
        # The claim must not stay CLAIMED forever; record a safe code only.
        with contextlib.suppress(Exception):
            fail_reminder(reminder_id, reason="event_reminder_send_failed")
        raise PermanentJobError("event_reminder_send_failed") from error


def register_jobs() -> None:
    """Register the two handlers and the five-minute schedule (app ready)."""

    from community_base.jobs import registry

    registry.register_handler(PLAN_HANDLER)(plan_reminders_job)
    registry.register_handler(SEND_HANDLER)(send_reminder_job)
    registry.schedule(
        PLAN_HANDLER, "*/5 * * * *", {}, name="event_registrants.plan_reminders.every_5_minutes"
    )
    # The package schedule is replaced, not doubled: both would claim the
    # same EventReminder rows.  Its handler stays registered (harmless).
    registry._schedules.pop(PACKAGE_REMINDER_SCHEDULE, None)
