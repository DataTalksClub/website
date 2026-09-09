from unittest.mock import patch

from django.test import override_settings

from courses.models import CourseRegistration
from courses.tests.registration_campaign_base import RegistrationCampaignBase


class RegistrationCampaignNotificationTests(RegistrationCampaignBase):
    @override_settings(
        DATAMAILER_URL="https://datamailer.example.com",
        DATAMAILER_API_KEY="secret-token",
        DATAMAILER_CLIENT="dtc-courses",
        DATAMAILER_AUDIENCE="dtc-courses",
    )
    @patch(
        "courses.views.registration.send_registration_confirmation_mail"
    )
    def test_registration_sends_confirmation(self, send_confirmation):
        url = self.campaign_url()
        payload = self.registration_payload()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                payload,
            )

        self.assertEqual(response.status_code, 200)
        registration = CourseRegistration.objects.get()
        send_confirmation.assert_called_once_with(registration)
