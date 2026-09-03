from courses.tests.homework_submission_validation_base import (
    HomeworkSubmissionValidationBase,
)


class HomeworkSubmissionValidationTests(HomeworkSubmissionValidationBase):
    def test_submit_homework_rejects_non_faq_contribution_url(self):
        self.enable_faq_contribution_field()
        faq_url = "https://gist.github.com/Sanjomwa/2dcb7a95baa01c07c10048fbac1a8461"
        post_data = self.updated_answer_post_data(
            faq_contribution_url=faq_url,
        )

        response = self.post_homework(post_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "FAQ contribution must be a DataTalksClub/faq issue or pull request URL",
        )
        self.assertContains(
            response,
            'value="https://gist.github.com/Sanjomwa/2dcb7a95baa01c07c10048fbac1a8461"',
        )
        # The design system port dropped the utility class the field used to carry
        # for its own spacing; the rejected-field marker is unchanged.
        self.assertContains(
            response,
            'class="form-control is-invalid"',
        )
        self.assert_no_submission()

    def test_submit_homework_accepts_faq_issue_url(self):
        self.enable_faq_contribution_field()
        self.client.login(
            username="test@test.com",
            password="12345",
        )

        faq_url = "https://github.com/DataTalksClub/faq/issues/281"
        post_data = self.updated_answer_post_data(
            faq_contribution_url=faq_url,
        )

        homework_url = self.homework_url()
        response = self.client.post(homework_url, post_data)

        self.assertEqual(response.status_code, 302)
        submission = self.get_saved_submission()
        self.assertEqual(submission.faq_contribution_url, faq_url)

    def test_submit_homework_url_is_not_remote_status_checked(self):
        self.enable_homework_url_field()
        homework_url = "https://github.com/nonexistent/repo"
        post_data = self.updated_answer_post_data(homework_url=homework_url)

        response = self.post_homework(post_data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.get_saved_submission().homework_link, homework_url)
