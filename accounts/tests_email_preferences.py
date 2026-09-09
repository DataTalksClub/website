from django.test import override_settings
from django.urls import reverse

from accounts.models import CustomUser
from accounts.tests_base import AccountCourseTestCase


class AccountEmailPreferencesTestCase(AccountCourseTestCase):
    def test_update_writes_the_local_category_field(self):
        self.client.force_login(self.user)
        url = reverse("account_email_preferences")
        payload = {"field": "email_deadline_reminders", "value": "false"}

        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "field": "email_deadline_reminders",
                "value": False,
                "stored": True,
            },
        )
        self.user.refresh_from_db()
        self.assertIs(self.user.email_deadline_reminders, False)

    def test_get_reports_unset_fields_as_allowed(self):
        self.user.email_course_updates = False
        self.user.save(update_fields=["email_course_updates"])
        self.client.force_login(self.user)
        url = reverse("account_email_preferences")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["preferences"],
            {
                "email_course_updates": False,
                "email_deadline_reminders": True,
                "email_submission_confirmations": True,
            },
        )

    def test_unsupported_field_is_refused(self):
        self.client.force_login(self.user)
        url = reverse("account_email_preferences")

        response = self.client.post(
            url,
            {"field": "email_promotions", "value": "true"},
        )

        self.assertEqual(response.status_code, 400)
