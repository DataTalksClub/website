"""Sending the DTC mail purposes through the package mail app (D1.2b).

The five purposes D1.2a committed as ``email_templates/`` are sent through
``community_base.mail.send`` with the idempotency keys the datamailer
outbox used, so a replay of the same business event returns the original
``EmailDelivery`` instead of sending twice. The datamailer keeps serving
the legacy non-purpose flows (submission confirmations, score and
peer-review notifications) until D1.2c retires it; only the five purposes
moved here.
"""

from __future__ import annotations

import re

from community_base.mail import send as package_send
from django.db import transaction

PURPOSE_REGISTRATION_CONFIRMATION = "course-registration-confirmation"
PURPOSE_DEADLINE_REMINDER = "deadline-reminder"
PURPOSE_ENROLLMENT_CONFIRMATION = "enrollment-confirmation"
PURPOSE_CERTIFICATE_READY = "certificate-ready"
PURPOSE_SLACK_ACCESS = "slack-access"

#: The package validates keys with ``^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$``.
#: The outbox keys already fit that shape; the builder exists so composed
#: keys (event key + member source key) stay inside the 200-character cap
#: no matter how long the business identifiers grow.
_IDEMPOTENCY_KEY_FORBIDDEN = re.compile(r"[^A-Za-z0-9._:-]+")
MAX_IDEMPOTENCY_KEY_LENGTH = 200


def mail_idempotency_key(*parts: str) -> str:
    key = ":".join(part for part in parts if part)
    key = _IDEMPOTENCY_KEY_FORBIDDEN.sub("-", key)
    return key[:MAX_IDEMPOTENCY_KEY_LENGTH]


def send_package_mail(
    *,
    purpose: str,
    to: str,
    context: dict,
    idempotency_key: str,
    category: str = "",
    user=None,
):
    """Record one durable package delivery, replay-safe by key.

    The package requires an active transaction; flows that call this from
    an ``on_commit`` callback run outside one, so the adapter opens its
    own. Transport failures are not possible here - the delivery is
    durable and the ``cb_mail.deliver`` job carries the Relay contact -
    so a raised error means a programming error or a key conflict, and
    the studio delivery views are the audit surface.
    """

    with transaction.atomic():
        return package_send(
            purpose=purpose,
            to=to,
            context=context,
            idempotency_key=idempotency_key,
            category=category,
            user=user,
        )


def send_registration_confirmation_mail(registration):
    """Confirm a course registration through the package (D1.2b).

    Replaces the datamailer transactional send and keeps its idempotency
    key ``registration-confirmation:<registration pk>`` and its
    ``email_course_updates`` preference category.
    """

    from course_management.datamailer.payloads.registration_confirmations import (
        registration_confirmation_course_context,
        registration_confirmation_urls,
    )
    from course_management.datamailer.payloads.registration_common import (
        registration_email,
    )

    email = registration_email(registration)
    if not email:
        return None

    course = registration.course
    urls = registration_confirmation_urls(registration.campaign, course)
    course_context = registration_confirmation_course_context(course)
    return send_package_mail(
        purpose=PURPOSE_REGISTRATION_CONFIRMATION,
        to=email,
        context={
            "course_title": course_context["course_title"],
            "course_url": urls["course_url"],
            "start_date": course_context["course_start_date"],
            "campaign_title": registration.campaign.title,
            "profile_url": urls["profile_url"],
        },
        idempotency_key=mail_idempotency_key(
            "registration-confirmation",
            str(registration.pk),
        ),
        category="email_course_updates",
        user=registration.user,
    )


def send_enrollment_confirmation_mail(enrollment):
    """Confirm an enrollment through the package (D1.2b).

    Replaces the enrollment member-upsert outbox event: the enrollment
    itself is the replay boundary, so the key is derived from it. The
    datamailer never sent an enrollment confirmation; this purpose is new
    and fires on the same trigger the outbox event had.
    """

    course = enrollment.course
    student = enrollment.student
    raw_email = student.email or ""
    email = raw_email.strip().lower()
    if not email:
        return None
    return send_package_mail(
        purpose=PURPOSE_ENROLLMENT_CONFIRMATION,
        to=email,
        context={
            "course_title": course.title,
            "cohort_name": cohort_display_name(course),
            "course_url": cohort_public_url(course),
        },
        idempotency_key=mail_idempotency_key(
            "enrollment-confirmation",
            str(enrollment.pk),
        ),
        category="email_course_updates",
        user=student,
    )


def send_deadline_reminder_mail(event, member, user=None):
    """Remind one learner about one deadline through the package (D1.2b).

    Replaces the transient recipient-list campaign: the reminder event
    identifies the deadline window and each member one learner, so the
    replay key is the event key plus the member source key. The member
    metadata carries the deadline formatted in the learner's own
    timezone, which the event-level context cannot know.
    """

    context = event.send_payload["context"]
    metadata = member.get("metadata") or {}
    return send_package_mail(
        purpose=PURPOSE_DEADLINE_REMINDER,
        to=member["email"],
        context={
            "course_title": context["course_title"],
            "course_url": context["action_url"],
            "deadline": metadata.get("deadline_at") or context["deadline_at"],
        },
        idempotency_key=mail_idempotency_key(
            event.key,
            member.get("source_object_key", ""),
        ),
        category="email_deadline_reminders",
        user=user,
    )


def send_certificate_ready_mail(enrollment):
    """Tell a graduate their certificate is ready (D1.2b).

    Replaces the datamailer transactional availability notification and
    keeps its idempotency key ``certificate-available:<enrollment pk>``.
    """

    from course_management.datamailer.payloads.certificate_availability import (
        certificate_availability_urls,
    )

    raw_email = enrollment.student.email or ""
    email = raw_email.strip().lower()
    certificate_url = (enrollment.certificate_url or "").strip()
    if not email or not certificate_url:
        return None
    urls = certificate_availability_urls(enrollment)
    return send_package_mail(
        purpose=PURPOSE_CERTIFICATE_READY,
        to=email,
        context={
            "course_title": enrollment.course.title,
            "certificate_url": urls["certificate_url"],
        },
        idempotency_key=mail_idempotency_key(
            "certificate-available",
            str(enrollment.pk),
        ),
        category="email_course_updates",
        user=enrollment.student,
    )


def cohort_display_name(cohort) -> str:
    identifier = getattr(cohort, "identifier", "") or ""
    if identifier:
        return f"{cohort.title} ({identifier})"
    return cohort.title


def cohort_public_url(cohort) -> str:
    from course_management.datamailer.payloads.urls import (
        cohort_route_kwargs,
        public_route_url,
    )

    return public_route_url("cohort", cohort_route_kwargs(cohort))
