from functools import partial

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from accounts.models import CustomUser
from core.runtime_config import get_bool_setting
from course_management.datamailer.sync.contacts import (
    erase_contact_from_datamailer,
    sync_contact,
)
from course_management.package_mail import send_enrollment_confirmation_mail
from courses.models.cohort import Enrollment


@receiver(post_save, sender=CustomUser)
def sync_user_to_datamailer(sender, instance, created, **kwargs):
    if not created:
        return

    if not get_bool_setting("datamailer.sync_on_user_create"):
        return

    callback = partial(sync_contact, instance)
    transaction.on_commit(callback)


@receiver(post_delete, sender=CustomUser)
def erase_user_from_datamailer(sender, instance, **kwargs):
    user_id = instance.pk
    email = instance.email
    callback = partial(
        erase_contact_from_datamailer,
        user_id=user_id,
        email=email,
    )
    transaction.on_commit(callback)


@receiver(post_save, sender=Enrollment)
def send_enrollment_confirmation(sender, instance, created, **kwargs):
    if not created:
        return

    callback = partial(send_enrollment_confirmation_mail, instance)
    transaction.on_commit(callback)
