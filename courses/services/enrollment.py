"""The live learner enrollment action and its confirmation mail (#460).

The enrollment confirmation is an explicit effect of a learner enrolling,
not of an ``Enrollment`` row being saved. Every live learner path that can
create an enrollment goes through this module. Importers and seeds create
rows with the plain ORM, so they never send mail.
"""

from __future__ import annotations

from functools import partial

from django.db import transaction

from course_management.package_mail import send_enrollment_confirmation_mail
from courses.models.cohort import Enrollment


def enroll_learner(student, cohort) -> tuple[Enrollment, bool]:
    """Return the learner's enrollment in ``cohort``, creating it if absent.

    Only a newly created enrollment schedules the confirmation mail, and
    the mail is sent after the surrounding transaction commits.
    """

    enrollment, created = Enrollment.objects.get_or_create(
        student=student,
        course=cohort,
    )
    if created:
        confirm_new_enrollment(enrollment)
    return enrollment, created


def confirm_new_enrollment(enrollment: Enrollment) -> None:
    """Schedule the confirmation for an enrollment a learner just created.

    Used directly by a live path that persists the enrollment itself (the
    enrollment form saves its own instance). The send runs on commit, so a
    rolled-back enrollment sends nothing.
    """

    callback = partial(send_enrollment_confirmation_mail, enrollment)
    transaction.on_commit(callback)
