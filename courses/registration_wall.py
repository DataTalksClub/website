"""Approval gates cohort participation; public course materials remain readable.

The flag belongs to the delivery, so repointing or closing a registration
campaign cannot unlock an earlier cohort. This mirrors CMP's registration wall.
"""

from django.core.exceptions import PermissionDenied

from courses.models.cohort import CourseRegistration


def registration_wall_status_for_user(cohort, user):
    if not user.is_authenticated:
        return None
    email = (user.email or "").strip().lower()
    if not email:
        return None
    return (
        CourseRegistration.objects.filter(course=cohort, email_normalized=email)
        .order_by("-updated_at", "-pk")
        .values_list("status", flat=True)
        .first()
    )


def registration_wall_blocks_user(cohort, user):
    if not cohort.requires_registration_approval or user.is_staff:
        return False
    if not user.is_authenticated:
        return True
    email = (user.email or "").strip().lower()
    return (
        not email
        or not CourseRegistration.objects.filter(
            course=cohort,
            email_normalized=email,
            status=CourseRegistration.Status.APPROVED,
        ).exists()
    )


def require_registration_approval(cohort, user):
    if registration_wall_blocks_user(cohort, user):
        raise PermissionDenied("Registration approval is required before submitting.")


def registration_wall_context(cohort, user):
    return {
        "registration_wall": bool(cohort and cohort.requires_registration_approval),
        "registration_wall_blocked": bool(cohort and registration_wall_blocks_user(cohort, user)),
        "registration_wall_status": registration_wall_status_for_user(cohort, user)
        if cohort
        else None,
    }
