from django.test import override_settings

from courses.tests.deadline_reminder_base import (
    DATAMAILER_SETTINGS,
    NO_PREFERENCE_LOOKUP,
    DeadlineReminderTestBase,
)
from courses.tests.deadline_reminder_homework import (
    assert_homework_reminder_deliveries,
    create_homework_reminder_fixture,
)


class HomeworkDeadlineReminderCommandTest(DeadlineReminderTestBase):
    @override_settings(
        **DATAMAILER_SETTINGS,
        **NO_PREFERENCE_LOOKUP,
        PUBLIC_BASE_URL="https://courses.example.com",
    )
    def test_homework_deadline_reminder_sends_transient_eligible_learners(
        self,
    ):
        now = self.reminder_run_time()
        fixture = create_homework_reminder_fixture(self, now)

        self.run_deadline_reminders(now)

        assert_homework_reminder_deliveries(self, fixture)
