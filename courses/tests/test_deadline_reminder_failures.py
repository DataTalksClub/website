from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from community_base.mail import MailError
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from courses.models import Homework
from courses.tests.deadline_reminder_base import (
    DATAMAILER_SETTINGS,
    NO_PREFERENCE_LOOKUP,
    DeadlineReminderTestBase,
)


SEND_TARGET = (
    "courses.management.commands.send_deadline_reminders."
    "send_deadline_reminder_mail"
)


class DeadlineReminderFailureIsolationTest(DeadlineReminderTestBase):
    """A failing reminder must not cancel the reminders queued behind it.

    Production showed one audit row per run: the first event failed, the
    command aborted, and every later event was silently dropped.
    """

    def create_two_homeworks(self, course, now):
        first = Homework.objects.create(
            course=course,
            slug="homework-1",
            title="Homework 1",
            due_date=now + timedelta(days=1, hours=10),
        )
        second = Homework.objects.create(
            course=course,
            slug="homework-2",
            title="Homework 2",
            due_date=now + timedelta(days=1, hours=14),
        )
        return first, second

    @override_settings(
        **DATAMAILER_SETTINGS,
        **NO_PREFERENCE_LOOKUP,
        PUBLIC_BASE_URL="https://courses.example.com",
    )
    @patch(SEND_TARGET)
    def test_second_reminder_is_sent_when_first_one_fails(self, send_mail):
        now = self.reminder_run_time()
        course = self.create_course()
        user = self.create_user("student", "student@example.com")
        self.create_enrollment(user, course)
        self.create_two_homeworks(course, now)

        send_mail.side_effect = [
            MailError("recipient rejected"),
            None,
        ]

        err = StringIO()
        with self.assertRaises(CommandError):
            self.run_deadline_reminders(now, stderr=err)

        self.assertEqual(send_mail.call_count, 2)
        self.assertIn("recipient rejected", err.getvalue())

    @override_settings(
        **DATAMAILER_SETTINGS,
        **NO_PREFERENCE_LOOKUP,
        PUBLIC_BASE_URL="https://courses.example.com",
    )
    @patch(SEND_TARGET)
    def test_failure_is_reported_on_stderr_with_reason(self, send_mail):
        now = self.reminder_run_time()
        course = self.create_course()
        user = self.create_user("student", "student@example.com")
        self.create_enrollment(user, course)
        Homework.objects.create(
            course=course,
            slug="homework-1",
            title="Homework 1",
            due_date=now + timedelta(days=1, hours=14),
        )

        send_mail.side_effect = MailError("idempotency conflict")

        err = StringIO()
        with self.assertRaises(CommandError):
            self.run_deadline_reminders(now, stderr=err)

        self.assertIn("idempotency conflict", err.getvalue())
