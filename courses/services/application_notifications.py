"""Operator and applicant notifications for application-style registration.

Mirror of CMP intake 4d0452a8 (``application-pending-operator``,
``application-approved``, ``application-rejected``): a new pending
registration on a campaign with ``requires_approval`` notifies the
operators once, and an operator approve/reject decision emails the
applicant once per decision. Transport is the durable package mail path
(``community_base.mail.send`` → Relay), so every send is replay-safe by
its idempotency key and Relay failures stay loud (``MailError`` /
``MailConflict`` propagate; the ``cb_mail.deliver`` worker raises on
transport failure). No ``fail_silently`` path exists here.

Triggers and recipients match CMP: operators are
``alexey@datatalks.club`` plus ``APPLICATION_NOTIFICATION_OPERATORS``
(normalized, deduplicated, default always retained); open campaigns
notify nobody. Repeated decisions do not enqueue again; a changed
decision is a different purpose/key and sends anew. Registration and
notification rows commit together inside one transaction.
"""

from __future__ import annotations

import logging

from community_base.mail.service import MailError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateformat import format as format_date

from course_management.mail_payloads import (
    registration_confirmation_course_context,
    registration_confirmation_urls,
    registration_email,
)
from course_management.package_mail import (
    PURPOSE_APPLICATION_APPROVED,
    PURPOSE_APPLICATION_PENDING_OPERATOR,
    PURPOSE_APPLICATION_REJECTED,
    mail_idempotency_key,
    send_package_mail,
)

logger = logging.getLogger(__name__)

#: The operator address that is always retained, even when the configured
#: operator list is empty or omits it.
DEFAULT_OPERATOR_EMAIL = "alexey@datatalks.club"

#: Django setting (also ``APPLICATION_NOTIFICATION_OPERATORS`` env var, see
#: ``website/settings/base.py``) holding the extra operator recipients.
OPERATOR_SETTING_NAME = "APPLICATION_NOTIFICATION_OPERATORS"

APPLICATION_CATEGORY = "email_course_updates"


def normalize_operator_email(value) -> str:
    return str(value or "").strip().lower()


def operator_recipients() -> list[str]:
    """Operator recipients: default plus configured, normalized and deduplicated."""

    configured = getattr(settings, OPERATOR_SETTING_NAME, None) or []
    if isinstance(configured, str):
        configured = configured.split(",")
    recipients: list[str] = []
    for candidate in [DEFAULT_OPERATOR_EMAIL, *configured]:
        normalized = normalize_operator_email(candidate)
        if normalized and normalized not in recipients:
            recipients.append(normalized)
    return recipients


def application_review_url(campaign) -> str:
    """Absolute operator link to the campaign's pending-review queue."""

    from course_management.public_urls import public_route_url

    base = public_route_url(
        "studio_courses_campaign_registrations",
        {"campaign_slug": campaign.slug},
    )
    return f"{base}?status=pending"


def _submitted_at(registration) -> str:
    created_at = registration.created_at or timezone.now()
    return format_date(created_at, "F j, Y")


def operator_notification_context(registration) -> dict:
    campaign = registration.campaign
    course = registration.course or campaign.current_course
    course_context = registration_confirmation_course_context(course)
    course_title = course_context["course_title"] or campaign.title
    email = registration_email(registration)
    name = (registration.name or "").strip() or email
    return {
        "applicant_name": name,
        "applicant_email": email,
        "campaign_title": campaign.title,
        "course_title": course_title,
        "review_url": application_review_url(campaign),
        "submitted_at": _submitted_at(registration),
    }


def queue_operator_notification(registration):
    """Notify operators about one pending application, once per recipient.

    Only fires for pending registrations on campaigns that require
    approval; every other registration returns ``[]`` without sending.
    One durable delivery per operator recipient makes the send idempotent
    per (registration, operator): replaying returns the original rows.
    """

    from courses.views.registration_form import campaign_requires_approval

    campaign = registration.campaign
    if not campaign_requires_approval(campaign):
        return []
    if registration.status != registration.Status.PENDING:
        return []
    context = operator_notification_context(registration)
    recipients = operator_recipients()
    # Fail the whole batch before sending anything: the same recipient
    # rule ``community_base.mail.send`` enforces, checked up front so one
    # bad address can neither half-notify nor fail silently.
    for recipient in recipients:
        if not recipient or len(recipient) > 254 or "@" not in recipient:
            raise MailError(f"invalid operator recipient for application notice: {recipient!r}")
    deliveries = []
    for recipient in operator_recipients():
        deliveries.append(
            send_package_mail(
                purpose=PURPOSE_APPLICATION_PENDING_OPERATOR,
                to=recipient,
                context=dict(context),
                idempotency_key=mail_idempotency_key(
                    "application-pending-operator",
                    str(registration.pk),
                    recipient,
                ),
                category=APPLICATION_CATEGORY,
            )
        )
    logger.info(
        "queued operator application notice for registration %s to %d recipient(s)",
        registration.pk,
        len(deliveries),
    )
    return deliveries


def notify_application_approved(registration):
    """Email the applicant about an approval decision, once per decision."""

    from course_management.public_urls import public_route_url

    campaign = registration.campaign
    course = registration.course or campaign.current_course
    urls = registration_confirmation_urls(campaign, course)
    course_context = registration_confirmation_course_context(course)
    course_title = course_context["course_title"] or campaign.title
    return send_package_mail(
        purpose=PURPOSE_APPLICATION_APPROVED,
        to=registration_email(registration),
        context={
            "applicant_email": registration_email(registration),
            "course_title": course_title,
            "course_url": urls["course_url"] or urls["registration_url"],
            "login_url": public_route_url("login"),
        },
        idempotency_key=mail_idempotency_key(
            "application-approved",
            str(registration.pk),
        ),
        category=APPLICATION_CATEGORY,
        user=registration.user,
    )


def notify_application_rejected(registration):
    """Email the applicant about a rejection decision, once per decision."""

    campaign = registration.campaign
    course = registration.course or campaign.current_course
    course_context = registration_confirmation_course_context(course)
    course_title = course_context["course_title"] or campaign.title
    return send_package_mail(
        purpose=PURPOSE_APPLICATION_REJECTED,
        to=registration_email(registration),
        context={
            "course_title": course_title,
        },
        idempotency_key=mail_idempotency_key(
            "application-rejected",
            str(registration.pk),
        ),
        category=APPLICATION_CATEGORY,
        user=registration.user,
    )


_DECISION_SENDERS = {
    "approved": notify_application_approved,
    "rejected": notify_application_rejected,
}


@transaction.atomic
def review_registration(registration, decision):
    """Apply an operator approve/reject decision and notify the applicant.

    The status change and the applicant notification commit together, so a
    rollback sends nothing. Repeating the same decision replays the
    original delivery instead of sending twice; changing the decision
    sends under the other purpose/key. Returns
    ``(registration, decision_changed, delivery)``.
    """

    from courses.models.cohort import CourseRegistration

    valid = {
        CourseRegistration.Status.APPROVED,
        CourseRegistration.Status.REJECTED,
    }
    if decision not in valid:
        raise ValueError(f"decision must be approved or rejected, got {decision!r}")
    # Same FOR UPDATE/outer-join constraint as the confirmation flow:
    # never ``select_related("course")`` on a locked query; ``campaign``
    # is non-nullable and stays prefetched.
    locked = (
        CourseRegistration.objects.select_for_update()
        .select_related("campaign")
        .get(pk=registration.pk)
    )
    changed = locked.status != decision
    if changed:
        locked.status = decision
        locked.save(update_fields=["status", "updated_at"])
    sender = _DECISION_SENDERS[decision]
    delivery = sender(locked)
    logger.info(
        "application %s reviewed as %s (changed=%s)",
        locked.pk,
        decision,
        changed,
    )
    return locked, changed, delivery
