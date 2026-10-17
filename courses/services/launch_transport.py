"""Thin transport boundaries with simulation as the deployment default."""

import requests
from community_base.mail import send
from django.conf import settings
from django.db import transaction

from courses.models import Announcement, AnnouncementAssignment, AnnouncementDelivery, Enrollment
from courses.services.launch_context import preview
from courses.services.launch_validation import serialize_rows


def transport_mode():
    if not getattr(settings, "COURSE_ANNOUNCEMENTS_ENABLED", False):
        return "simulation"
    if settings.ENVIRONMENT != "production":
        return "simulation"
    return "live"


def submit_email(row):
    content = preview(row)
    mode = transport_mode()
    enrollments = Enrollment.objects.filter(course=row.cohort).select_related("student")
    for enrollment in enrollments.iterator(chunk_size=100):
        submit_recipient(row, enrollment.student, content, mode)


def submit_recipient(row, user, content, mode):
    with transaction.atomic():
        delivery, created = AnnouncementDelivery.objects.get_or_create(
            announcement=row, recipient_key=f"user:{user.pk}"
        )
        if not created:
            return
        delivery.state = "simulated"
        if user.email_course_updates is False:
            delivery.state = "suppressed"
        elif mode == "live":
            delivery.email_delivery = send(
                purpose="course-announcement",
                to=user.email,
                context={
                    "announcement_subject": content["subject"],
                    "announcement_body": content["body"],
                },
                idempotency_key=f"announcement:{row.pk}:user:{user.pk}",
                category="course-updates",
                sender="courses",
                user=user,
                related=row,
            )
            delivery.state = "submitted"
        delivery.save()


def submit_telegram(row):
    with transaction.atomic():
        serialize_rows(Announcement.objects.filter(pk=row.pk))
        delivery, created = AnnouncementDelivery.objects.get_or_create(
            announcement=row, recipient_key="telegram"
        )
        if not created and delivery.state != "pending":
            return delivery.state
        destination_key = getattr(row, "_telegram_destination_key", None)
        if destination_key is None:
            destination_key = telegram_destination_reference(row)
        if transport_mode() == "simulation":
            delivery.state = "simulated"
            delivery.save(update_fields=["state"])
            return delivery.state
        delivery.state = "ambiguous"
        delivery.save(update_fields=["state"])
    # The uncertainty marker commits before network IO. A worker crash cannot automatically resend.
    return telegram_request(row, delivery, destination_key)


def telegram_destination_reference(row):
    assignment = AnnouncementAssignment.objects.filter(course=row.cohort.course).first()
    return assignment.telegram_destination_key if assignment is not None else ""


def telegram_request(row, delivery, destination_key):
    destinations = getattr(settings, "COURSE_TELEGRAM_DESTINATIONS", {})
    token = getattr(settings, "COURSE_TELEGRAM_BOT_TOKEN", "")
    destination = destinations.get(destination_key)
    if not token or not destination:
        delivery.state = "blocked"
        delivery.save(update_fields=["state"])
        return "blocked"
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": destination, "text": preview(row)["body"]},
            timeout=15,
        )
        result = response.json()
    except (requests.RequestException, ValueError):
        return "ambiguous"
    return telegram_outcome(response.status_code, result, delivery)


def telegram_outcome(status_code, result, delivery):
    if not isinstance(result, dict):
        return "ambiguous"
    acknowledgement = result.get("result")
    if status_code == 200 and result.get("ok") is True:
        if not isinstance(acknowledgement, dict):
            return "ambiguous"
        message_id = acknowledgement.get("message_id")
        if type(message_id) is not int or message_id < 1:
            return "ambiguous"
        delivery.state = "submitted"
        delivery.external_id = str(message_id)
    elif 400 <= status_code < 500:
        delivery.state = "blocked"
    delivery.save(update_fields=["state", "external_id"])
    return delivery.state
