from functools import partial

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from course_management.package_mail import send_enrollment_confirmation_mail
from courses.models.cohort import Enrollment


@receiver(post_save, sender=Enrollment)
def send_enrollment_confirmation(sender, instance, created, **kwargs):
    if not created:
        return

    callback = partial(send_enrollment_confirmation_mail, instance)
    transaction.on_commit(callback)
