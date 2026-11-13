from unittest.mock import patch

from django.test import override_settings

from courses.tests.deadline_reminder_base import (
    DATAMAILER_SETTINGS,
    NO_PREFERENCE_LOOKUP,
    DeadlineReminderTestBase,
)
from courses.tests.deadline_reminder_project import (
    assert_project_reminder_deliveries,
    create_project_submission_reminder_fixture,
)


class ProjectSubmissionDeadlineReminderCommandTest(DeadlineReminderTestBase):
    @override_settings(
        **DATAMAILER_SETTINGS,
        **NO_PREFERENCE_LOOKUP,
        PUBLIC_BASE_URL="https://courses.example.com",
    )
    def test_project_deadline_reminders_use_7d_and_24h_windows(
        self,
    ):
        now = self.reminder_run_time()
        create_project_submission_reminder_fixture(self, now)

        with patch(
            "courses.management.commands.send_deadline_reminders.submit_transient_list"
        ) as submit_mock:
            self.run_deadline_reminders(now)

        assert_project_reminder_deliveries(self, submit_mock)
