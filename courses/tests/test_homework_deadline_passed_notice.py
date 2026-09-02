from django.utils import timezone

from courses.tests.homework_submission_validation_base import (
    HomeworkSubmissionValidationBase,
)

# One state notice per state, in the learner's own voice.  A signed-in student
# is told what still works; a signed-out visitor is told why to log in.  Neither
# repeats the retired "The deadline has passed, but this homework is still open"
# heading that used to sit beside a second callout saying the same thing.
SIGNED_IN_NOTICE = "Still accepting late submissions."
SIGNED_OUT_NOTICE = "Log in to submit — late submissions are still accepted."
RETIRED_NOTICE = "The deadline has passed, but this homework is still open"
HELPER_BEFORE = "You can save partial answers and update them until the deadline"
HELPER_AFTER = "You can still save and update your answers until this homework is closed"


class HomeworkDeadlinePassedNoticeTests(HomeworkSubmissionValidationBase):
    def pass_deadline(self):
        self.homework.due_date = timezone.now() - timezone.timedelta(days=2)
        self.homework.save(update_fields=["due_date"])

    def test_open_homework_past_deadline_shows_notice(self):
        self.pass_deadline()
        self.client.login(**self.credentials())

        response = self.client.get(self.homework_url())

        self.assertContains(response, SIGNED_IN_NOTICE)
        self.assertContains(
            response,
            "Your latest saved version will be scored when the homework closes.",
        )
        self.assertNotContains(response, RETIRED_NOTICE)
        # The hero's live countdown already says the deadline passed.
        self.assertNotContains(response, "The deadline has passed")

    def test_open_homework_before_deadline_hides_notice(self):
        self.client.login(**self.credentials())

        response = self.client.get(self.homework_url())

        self.assertNotContains(response, SIGNED_IN_NOTICE)
        self.assertNotContains(response, SIGNED_OUT_NOTICE)
        self.assertNotContains(response, RETIRED_NOTICE)

    def test_signed_in_open_homework_before_deadline_states_nothing(self):
        """The ordinary path is quiet: an enabled form needs no announcement."""

        self.client.login(**self.credentials())

        response = self.client.get(self.homework_url())

        self.assertContains(response, '<div class="homework-state-notices">')
        self.assertNotContains(response, 'class="callout callout-')

    def test_closed_homework_past_deadline_hides_notice(self):
        self.pass_deadline()
        self.close_homework()
        self.client.login(**self.credentials())

        response = self.client.get(self.homework_url())

        self.assertNotContains(response, SIGNED_IN_NOTICE)
        self.assertNotContains(response, SIGNED_OUT_NOTICE)
        self.assertNotContains(response, RETIRED_NOTICE)

    def test_notice_shown_to_anonymous_visitor(self):
        self.pass_deadline()

        response = self.client.get(self.homework_url())

        self.assertContains(response, SIGNED_OUT_NOTICE)
        self.assertContains(response, "Log in to submit")
        self.assertNotContains(response, RETIRED_NOTICE)

    def test_helper_text_past_deadline_does_not_mention_the_deadline(self):
        self.pass_deadline()
        self.client.login(**self.credentials())

        response = self.client.get(self.homework_url())

        self.assertContains(response, HELPER_AFTER)
        self.assertNotContains(response, HELPER_BEFORE)

    def test_helper_text_before_deadline_mentions_the_deadline(self):
        self.client.login(**self.credentials())

        response = self.client.get(self.homework_url())

        self.assertContains(response, HELPER_BEFORE)
        self.assertNotContains(response, HELPER_AFTER)

    def credentials(self):
        return dict(username="test@test.com", password="12345")
