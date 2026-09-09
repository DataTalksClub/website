"""BE-06 immediate containment for the project submission flow.

A rejected field must leave the submission, enrollment, certificate name,
and success events unchanged, and the re-rendered form must echo the raw
POST values without re-running the persisting parser (which used to save
again and re-raise on invalid learning links).
"""

from courses.models import Enrollment, User
from courses.tests.project_submission_view_base import (
    ProjectSubmissionViewTestBase,
)


class ProjectSubmissionContainmentTests(ProjectSubmissionViewTestBase):
    def enable_time_spent_field(self):
        self.project.time_spent_project_field = True
        self.project.save()

    def test_rejected_submission_rolls_back_certificate_and_enrollment(self):
        self.user.certificate_name = "Name Before"
        self.user.save(update_fields=["certificate_name"])
        self.enable_time_spent_field()
        enrollments_before = Enrollment.objects.count()

        response = self.post_project(
            self.project_submission_data(
                time_spent="-2",
                certificate_name="Changed While Rejected",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.user = User.objects.get(pk=self.user.pk)
        self.assertEqual(self.user.certificate_name, "Name Before")
        self.assertEqual(Enrollment.objects.count(), enrollments_before)
        self.assertEqual(self.project_submission_count(), 0)

    def test_rejected_update_leaves_the_saved_submission_unchanged(self):
        self.enable_time_spent_field()
        self.create_existing_project_submission(
            github_link="https://github.com/alexeygrigorev/llm-rag-workshop",
        )
        submission_before = self.get_project_submission()

        response = self.post_project(
            self.project_submission_data(
                github_link="https://github.com/new/attempt",
                time_spent="not a number",
            )
        )

        self.assertEqual(response.status_code, 200)
        submission_after = self.get_project_submission()
        self.assertEqual(submission_after.pk, submission_before.pk)
        self.assertEqual(
            submission_after.github_link,
            "https://github.com/alexeygrigorev/llm-rag-workshop",
        )
        self.assertEqual(submission_after.submitted_at, submission_before.submitted_at)

    def test_error_response_echoes_the_raw_posted_values(self):
        self.enable_time_spent_field()
        raw_links = ["https://example.com/my-post"]

        response = self.post_project(
            self.project_submission_data(
                github_link="https://github.com/my/work",
                commit_id="abc1234",
                time_spent="nan",
                **{"learning_in_public_links[]": raw_links},
            )
        )

        self.assertEqual(response.status_code, 200)
        submission = response.context["submission"]
        self.assertEqual(submission.github_link, "https://github.com/my/work")
        self.assertEqual(submission.commit_id, "abc1234")
        self.assertEqual(submission.learning_in_public_links, raw_links)
        self.assertEqual(submission.pk, None)

    def test_invalid_learning_link_renders_the_form_instead_of_a_500(self):
        # The invalid link raises during parsing; re-running the parser while
        # building the error response used to raise a second time and escape
        # the handler.
        self.prepare_project_with_learning_cap()

        response = self.post_project(
            self.project_submission_data(**{"learning_in_public_links[]": ["not-a-url-at-all"]})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.project_submission_count(), 0)
