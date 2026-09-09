from django.test import override_settings

from courses.tests.deadline_reminder_base import (
    DATAMAILER_SETTINGS,
    NO_PREFERENCE_LOOKUP,
    DeadlineReminderTestBase,
)
from courses.tests.deadline_reminder_peer_review import (
    assert_peer_review_reminder_deliveries,
    create_peer_review_reminder_fixture,
)


class PeerReviewDeadlineReminderCommandTest(DeadlineReminderTestBase):
    @override_settings(
        **DATAMAILER_SETTINGS,
        **NO_PREFERENCE_LOOKUP,
        PUBLIC_BASE_URL="https://courses.example.com",
    )
    def test_peer_review_deadline_reminder_targets_unfinished_reviewers(
        self,
    ):
        now = self.reminder_run_time()
        fixture = create_peer_review_reminder_fixture(self, now)

        self.run_deadline_reminders(now)

        assert_peer_review_reminder_deliveries(self, fixture)
