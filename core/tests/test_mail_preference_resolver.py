"""The D1.2b package-mail migration contracts.

The datamailer outbox enqueue path is gone: the grep gate keeps it gone,
the preference resolver keeps the datamailer store's opt-outs effective
while sends run through the package, and the read-only outbox storage
still reports its summary for rollback monitoring.
"""

from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.test import TestCase

from course_management.mail_preferences import resolve_mail_preference
from course_management.package_mail import send_package_mail


def repository_python_files():
    from pathlib import Path

    root = Path("course_management")
    for path in root.rglob("*.py"):
        if "migrations" in path.parts or "__pycache__" in path.parts:
            continue
        yield path


class OutboxEnqueueGateTest(TestCase):
    def test_no_enqueue_path_remains_outside_migrations(self):
        offenders = [
            str(path)
            for path in repository_python_files()
            if "enqueue_datamailer_outbox_event"
            in path.read_text(
                encoding="utf-8",
            )
        ]
        self.assertEqual(offenders, [])


class MailPreferenceResolverTest(TestCase):
    def _resolver(self, **kwargs):
        defaults = {
            "purpose": "deadline-reminder",
            "category": "email_deadline_reminders",
            "to": "student@example.com",
            "user": None,
        }
        defaults.update(kwargs)
        return resolve_mail_preference(**defaults)

    def test_anonymous_and_uncategorised_mail_is_allowed(self):
        self.assertTrue(self._resolver())
        user = _FakeUser()
        self.assertTrue(self._resolver(category="", user=user))

    @patch(
        "course_management.datamailer.preferences.get_email_preferences_for_user",
    )
    def test_a_datamailer_opt_out_suppresses_its_category(
        self,
        get_preferences,
    ):
        get_preferences.return_value = {"email_deadline_reminders": False}

        decision = self._resolver(user=_FakeUser())

        self.assertEqual(decision, "opted out of email_deadline_reminders")

    @patch(
        "course_management.datamailer.preferences.get_email_preferences_for_user",
    )
    def test_opted_in_and_unknown_categories_are_allowed(
        self,
        get_preferences,
    ):
        get_preferences.return_value = {"email_deadline_reminders": True}
        self.assertTrue(self._resolver(user=_FakeUser()))
        get_preferences.return_value = {"email_course_updates": False}
        self.assertTrue(self._resolver(user=_FakeUser()))

    @patch(
        "course_management.datamailer.preferences.get_email_preferences_for_user",
    )
    def test_an_unreachable_datamailer_fails_open(self, get_preferences):
        get_preferences.side_effect = OSError("external DNS resolution denied")

        self.assertTrue(self._resolver(user=_FakeUser()))


class _FakeUser:
    pk = 7


class OutboxStorageSummaryTest(TestCase):
    def test_the_read_only_storage_still_reports_a_summary(self):
        summary = _status_summary()
        self.assertIn("event_counts", summary)


def _status_summary():
    from course_management.datamailer_outbox_status import (
        datamailer_outbox_status_summary,
    )

    return datamailer_outbox_status_summary()


class PackageSendDurableRecordTest(TestCase):
    def test_a_send_records_one_durable_delivery(self):
        from accounts.models import CustomUser

        CustomUser.objects.create_user(
            username="worker",
            email="worker@example.com",
            password="password",
        )
        send_package_mail(
            purpose="slack-access",
            to="worker@example.com",
            context={"course_title": "ML", "slack_url": "https://s.example.com"},
            idempotency_key="slack-access:durable",
        )

        delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.state, EmailDelivery.State.PENDING)
        self.assertEqual(
            delivery.context_data["slack_url"],
            "https://s.example.com",
        )
