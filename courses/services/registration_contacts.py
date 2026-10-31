"""Synchronize saved registration consent through the website Relay client."""

import logging
from functools import partial
from uuid import UUID

from community_base.jobs import dispatch_after_commit
from community_base.jobs.models import JobIntent
from community_base.kernel.conf import get
from community_base.mail.relay_contacts import configured_contacts_client
from django.db import transaction

from core.operator_alerts import notify_operator_alert
from course_management.mail_payloads import registration_email
from courses.models.cohort import CourseRegistration

logger = logging.getLogger(__name__)
HANDLER = "courses.registration_contacts"
RELAY_CLIENT = "dtc-website"
RELAY_AUDIENCE = "dtc"


def report_contacts_failure(registration_id: int) -> None:
    logger.error("registration_contacts_failed", extra={"registration_id": registration_id})
    try:
        notify_operator_alert("registration_contacts_failed", {"registration_id": registration_id})
    except Exception:
        logger.error(
            "registration_contacts_alert_failed", extra={"registration_id": registration_id}
        )


def queue_registration_contacts(registration_id: int) -> None:
    """A failed enqueue cannot invalidate an already saved registration."""
    try:
        with transaction.atomic():
            intent, created = dispatch_after_commit(
                HANDLER,
                f"registration-contacts:{registration_id}",
                {"registration_id": registration_id},
            )
            if created and get("JOBS_BACKEND") == "relay":
                # The package deliberately swallows scheduler submission errors
                # and leaves a pending intent for its sweeper. Observe that
                # outcome after its callback so registration failures alert too.
                transaction.on_commit(partial(_check_submission, intent.pk, registration_id))
    except Exception:
        report_contacts_failure(registration_id)


def _check_submission(intent_id: UUID, registration_id: int) -> None:
    try:
        if not JobIntent.objects.filter(pk=intent_id, status=JobIntent.Status.PENDING).exists():
            return
    except Exception:
        pass
    report_contacts_failure(registration_id)


def sync_registration_to_relay(registration_id: int) -> None:
    registration = CourseRegistration.objects.select_related("course__course").get(
        pk=registration_id
    )
    # Imports never enqueue this job; replay must not invent newsletter consent.
    if not registration.accepted_newsletter:
        return
    tags = []
    if registration.course is not None:
        tags = [
            f"course-{registration.course.course.slug}",
            f"course-cohort-{registration.course.slug}",
        ]
    configured_contacts_client(RELAY_CLIENT).upsert_contact(
        registration_email(registration),
        RELAY_AUDIENCE,
        status="subscribed",
        verified=True,
        email_validation={"status": "externally_validated"},
        tags=tags,
    )
    logger.info("registration_contacts_synced", extra={"registration_id": registration_id})
