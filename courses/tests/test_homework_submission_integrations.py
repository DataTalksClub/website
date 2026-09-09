from unittest.mock import patch

from django.test import override_settings

from courses.tests.homework_submission_confirmation_helpers import (
    assert_confirmation_context,
    assert_confirmation_send,
    assert_confirmation_summary,
    assert_submission_fields,
    assert_submitted_answers,
    confirmation_post_data,
    minimal_submission_post_data,
    public_base_url_post_data,
)
from courses.tests.homework_submission_integration_base import (
    HomeworkSubmissionIntegrationBase,
)


SEND_TARGET = "courses.views.homework_confirmation.send_package_mail"


class HomeworkSubmissionConfirmationTest(HomeworkSubmissionIntegrationBase):
    @override_settings(PUBLIC_BASE_URL="")
    @patch(SEND_TARGET)
    def test_homework_submission_sends_confirmation_email(
        self,
        send_package_mail,
    ):
        post_data = confirmation_post_data(self)
        response = self.post_homework(post_data)

        self.assertEqual(response.status_code, 302)
        submission = self.get_submission()
        context = assert_confirmation_send(self, send_package_mail, submission)

        assert_confirmation_context(self, context, submission)
        assert_submission_fields(self, context)
        assert_submitted_answers(self, context)
        assert_confirmation_summary(self, context)

    @patch(SEND_TARGET)
    def test_homework_submission_sends_without_a_stored_preference(
        self,
        send_package_mail,
    ):
        post_data = minimal_submission_post_data(self)

        response = self.post_homework(post_data)

        self.assertEqual(response.status_code, 302)
        self.assert_submission_exists()
        send_package_mail.assert_called_once()
        kwargs = send_package_mail.call_args.kwargs
        self.assertEqual(kwargs["to"], "student@example.com")
        self.assertEqual(kwargs["category"], "submission-results")

    @override_settings(PUBLIC_BASE_URL="https://dev.courses.datatalks.club")
    @patch(SEND_TARGET)
    def test_homework_confirmation_uses_public_base_url(
        self,
        send_package_mail,
    ):
        post_data = public_base_url_post_data(self)
        response = self.post_homework(post_data)

        self.assertEqual(response.status_code, 302)
        context = send_package_mail.call_args.kwargs["context"]
        self.assertEqual(
            context["update_url"],
            f"https://dev.courses.datatalks.club{self.homework_url()}",
        )
