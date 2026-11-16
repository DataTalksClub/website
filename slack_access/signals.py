"""Current enrollment changes reconcile Slack eligibility without mutating a GET."""

from community_base.curriculum.models import Enrollment
from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Application
from .services import refresh_course_approval


@receiver(post_save, sender=Enrollment, dispatch_uid="slack_access.course_eligibility")
def enrollment_saved(sender, instance, **kwargs):
    changed_fields = kwargs.get("update_fields")
    if changed_fields and not {"user", "user_id", "cohort", "cohort_id", "unenrolled_at"} & set(
        changed_fields
    ):
        return
    if (
        instance.unenrolled_at is None
        and Application.objects.filter(
            user_id=instance.user_id,
            state=Application.State.PENDING,
        ).exists()
    ):
        try:
            refresh_course_approval(instance.user)
        except PermissionDenied:
            # Unverified/banned Slack eligibility must not prevent course enrollment.
            return
