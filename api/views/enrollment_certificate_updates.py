from dataclasses import dataclass

from accounts.identity_resolution import (
    AccountEmailResolution,
    AccountEmailResolutionStatus,
    resolve_accounts_by_email,
)
from accounts.identity_values import normalize_account_email
from api.views.enrollment_certificate_delivery import (
    persist_certificate_updates,
    queue_certificate_notifications,
)
from api.views.enrollment_certificate_validation import (
    validate_certificate_update_items,
)
from courses.models.cohort import Enrollment


@dataclass
class CertificateApplyResult:
    enrollment: Enrollment | None = None
    notify: bool = False
    updated: dict | None = None
    error: dict | None = None


@dataclass
class CertificateApplyBatch:
    enrollments_to_update: dict
    enrollments_to_notify: dict
    updated: list
    errors: list

    def record(self, result):
        if result.error:
            self.errors.append(result.error)
            return

        enrollment = result.enrollment
        self.enrollments_to_update[enrollment.id] = enrollment
        if result.notify:
            self.enrollments_to_notify[enrollment.id] = enrollment
        self.updated.append(result.updated)


@dataclass(frozen=True)
class CertificateUpdateLookups:
    course_slug: str
    identities_by_email: dict[str, AccountEmailResolution]
    enrollments_by_user_id: dict[int, Enrollment]


def process_certificate_updates(
    course,
    course_slug,
    certificate_updates,
    notification_sender,
):
    valid_updates, errors = validate_certificate_update_items(certificate_updates)

    lookups = certificate_update_lookups(
        course,
        course_slug,
        valid_updates,
    )
    apply_batch = apply_certificate_updates(
        valid_updates,
        lookups,
    )
    errors.extend(apply_batch.errors)

    deliver_certificate_update_batch(apply_batch, notification_sender)

    return apply_batch.updated, errors


def deliver_certificate_update_batch(apply_batch, notification_sender):
    persist_certificate_updates(apply_batch.enrollments_to_update)
    queue_certificate_notifications(
        apply_batch.enrollments_to_notify,
        notification_sender,
    )


def certificate_update_lookups(course, course_slug, valid_updates):
    identities_by_email = resolve_accounts_by_email(update["email"] for update in valid_updates)
    related_user_ids = {
        user_id
        for identity in identities_by_email.values()
        for user_id in identity.related_user_ids
    }
    enrollments_by_user_id = {}
    enrollments = Enrollment.objects.filter(
        course=course,
        student_id__in=related_user_ids,
    ).select_related("student")
    for enrollment in enrollments:
        enrollments_by_user_id[enrollment.student_id] = enrollment

    lookups = CertificateUpdateLookups(
        course_slug=course_slug,
        identities_by_email=identities_by_email,
        enrollments_by_user_id=enrollments_by_user_id,
    )
    return lookups


def apply_certificate_updates(valid_updates, lookups):
    batch = CertificateApplyBatch(
        enrollments_to_update={},
        enrollments_to_notify={},
        updated=[],
        errors=[],
    )

    for update in valid_updates:
        result = apply_certificate_update(
            update,
            lookups,
        )
        batch.record(result)

    return batch


def apply_certificate_update(update, lookups):
    email = update["email"]
    certificate_path = update["certificate_path"]
    normalized_email = normalize_account_email(email)
    identity = lookups.identities_by_email.get(normalized_email)

    if identity is None or identity.status == AccountEmailResolutionStatus.NOT_FOUND:
        error = user_not_found_error(update)
        return CertificateApplyResult(error=error)

    if identity.status == AccountEmailResolutionStatus.AMBIGUOUS:
        return CertificateApplyResult(error=identity_ambiguous_error(update))

    if identity.status != AccountEmailResolutionStatus.AVAILABLE:
        return CertificateApplyResult(error=identity_unavailable_error(update))

    related_enrollment_user_ids = {
        user_id
        for user_id in identity.related_user_ids
        if user_id in lookups.enrollments_by_user_id
    }
    durable_user = identity.user
    if durable_user is None:
        return CertificateApplyResult(error=identity_unavailable_error(update))
    enrollment = lookups.enrollments_by_user_id.get(durable_user.pk)
    if related_enrollment_user_ids - {durable_user.pk}:
        return CertificateApplyResult(error=identity_unavailable_error(update))

    if enrollment is None:
        error = not_enrolled_error(update, lookups.course_slug)
        return CertificateApplyResult(error=error)

    notify = should_notify_certificate_available(
        enrollment,
        certificate_path,
    )
    enrollment.certificate_url = certificate_path
    updated = {
        "index": update["index"],
        "email": update["email"],
        "enrollment_id": enrollment.id,
        "certificate_url": update["certificate_path"],
    }
    return CertificateApplyResult(
        enrollment=enrollment,
        notify=notify,
        updated=updated,
    )


def user_not_found_error(update):
    email = update["email"]
    return certificate_update_error(
        update,
        "user_not_found",
        f"User with email {email} not found",
    )


def not_enrolled_error(update, course_slug):
    email = update["email"]
    return certificate_update_error(
        update,
        "not_enrolled",
        f"User {email} is not enrolled in course {course_slug}",
    )


def identity_unavailable_error(update):
    return certificate_update_error(
        update,
        "identity_unavailable",
        "Account identity is unavailable",
    )


def identity_ambiguous_error(update):
    return certificate_update_error(
        update,
        "identity_ambiguous",
        "Account identity is ambiguous",
    )


def certificate_update_error(update, code, error):
    return {
        "index": update["index"],
        "email": update["email"],
        "code": code,
        "error": error,
    }


def should_notify_certificate_available(enrollment, certificate_path):
    existing_certificate_url = enrollment.certificate_url or ""
    stripped_existing_certificate_url = existing_certificate_url.strip()
    stripped_certificate_path = certificate_path.strip()
    had_certificate = bool(stripped_existing_certificate_url)
    has_new_certificate = bool(stripped_certificate_path)
    return not had_certificate and has_new_certificate
