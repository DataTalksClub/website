"""Live learner submissions that create an enrollment confirm it once (#460).

The enrollment confirmation is sent by ``courses.services.enrollment`` for
the learner action that creates the enrollment, never by row creation.
"""

from django.test import override_settings

from courses.models import Enrollment
from courses.services.project_form_adapter import build_project_submission_form
from courses.tests.homework_view_base import HomeworkDetailViewTestBase
from courses.tests.project_view_base import ProjectViewTestBase
from courses.tests.test_package_mail_flows import (
    FLOW_SETTINGS,
    assert_enrollment_confirmation,
    enrollment_confirmations,
)


@override_settings(**FLOW_SETTINGS)
class HomeworkSubmissionEnrollmentConfirmationTest(HomeworkDetailViewTestBase):
    def test_a_submission_that_creates_the_enrollment_confirms_it_once(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post_homework(self.answer_post_data())

        self.assertEqual(response.status_code, 302)
        enrollment = Enrollment.objects.get(student=self.user, course=self.course)
        assert_enrollment_confirmation(self, enrollment, self.user)

        with self.captureOnCommitCallbacks(execute=True):
            self.post_homework(self.updated_answer_post_data())

        self.assertEqual(enrollment_confirmations().count(), 1)

    def test_a_submission_for_an_existing_enrollment_sends_no_confirmation(self):
        self.create_enrollment()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.post_homework(self.answer_post_data())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(enrollment_confirmations().count(), 0)


@override_settings(**FLOW_SETTINGS)
class ProjectSubmissionEnrollmentConfirmationTest(ProjectViewTestBase):
    def save_submission(self):
        data = {
            "github_link": "https://github.com/learner/project",
            "commit_id": "abc1234",
            "certificate_name": "Learner Name",
        }
        form = build_project_submission_form(self.project, user=self.user, data=data)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_a_project_save_that_creates_the_enrollment_confirms_it_once(self):
        self.enrollment.delete()

        with self.captureOnCommitCallbacks(execute=True):
            submission, created = self.save_submission()

        self.assertTrue(created)
        assert_enrollment_confirmation(self, submission.enrollment, self.user)

        with self.captureOnCommitCallbacks(execute=True):
            self.save_submission()

        self.assertEqual(enrollment_confirmations().count(), 1)

    def test_a_project_save_for_an_existing_enrollment_sends_no_confirmation(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.save_submission()

        self.assertEqual(enrollment_confirmations().count(), 0)
