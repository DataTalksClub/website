from unittest.mock import patch

from django.test import TestCase, override_settings

from accounts.models import CustomUser
from courses.models import Cohort, Enrollment

DATAMAILER_SETTINGS = {
    "DATAMAILER_URL": "https://datamailer.example.com",
    "DATAMAILER_API_KEY": "secret-token",
    "DATAMAILER_CLIENT": "dtc-courses",
    "DATAMAILER_AUDIENCE": "dtc-courses",
    "DATAMAILER_SYNC_ON_USER_CREATE": True,
}


class DatamailerSignalTest(TestCase):
    """The contact lifecycle stays on the datamailer until D1.2c; the
    enrollment trigger became the package enrollment confirmation."""

    @override_settings(**DATAMAILER_SETTINGS)
    @patch("courses.signals.sync_contact")
    def test_new_user_syncs_after_commit(self, sync):
        with self.captureOnCommitCallbacks(execute=True):
            user = CustomUser.objects.create(email="student@example.com")

        sync.assert_called_once_with(user)

    @override_settings(**DATAMAILER_SETTINGS)
    @patch("courses.signals.send_enrollment_confirmation_mail")
    def test_new_enrollment_sends_confirmation_after_commit(self, send):
        user = CustomUser.objects.create(email="student@example.com")
        course = Cohort.objects.create(
            slug="ml-zoomcamp",
            title="ML Zoomcamp",
            description="Machine learning",
        )

        with self.captureOnCommitCallbacks(execute=True):
            enrollment = Enrollment.objects.create(
                student=user,
                course=course,
            )

        send.assert_called_once_with(enrollment)

    @override_settings(**DATAMAILER_SETTINGS)
    @patch("courses.signals.erase_contact_from_datamailer")
    def test_deleted_user_erases_contact_after_commit(self, erase_contact):
        user = CustomUser.objects.create_user(
            username="student",
            email="student@example.com",
        )
        user_id = user.pk

        with self.captureOnCommitCallbacks(execute=True):
            user.delete()

        erase_contact.assert_called_once_with(
            user_id=user_id,
            email="student@example.com",
        )
