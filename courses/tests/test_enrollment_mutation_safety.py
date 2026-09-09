"""BE-07: loading enrollment and review pages is read-only, and every
enrollment/preference mutation enforces POST, CSRF, and existing targets."""

from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from courses.models import (
    Cohort,
    Enrollment,
    PeerReview,
    ProjectSubmission,
    User,
)
from courses.tests.course_view_base import credentials
from courses.tests.project_eval_base import ProjectEvaluationTestBase
from courses.views.project_eval_submit_context import (
    ProjectEvalSubmitPage,
    project_eval_submit_context,
)


def enrollment_count_for(user, course):
    return Enrollment.objects.filter(student=user, course=course).count()


class EnrollmentToggleInputAllowlistTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(**credentials)
        self.course = Cohort.objects.create(
            slug="toggle-course",
            title="Toggle Course",
        )
        self.enrollment = Enrollment.objects.create(
            student=self.user,
            course=self.course,
        )
        self.toggle_url = reverse(
            "cohort_update_enrollment_toggle",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )

    def login(self):
        self.client.login(**credentials)

    def test_unsupported_field_returns_400_and_creates_no_enrollment(self):
        self.enrollment.delete()
        self.login()

        response = self.client.post(self.toggle_url, {"field": "total_score", "value": "999999"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(enrollment_count_for(self.user, self.course), 0)

    def test_valid_field_without_enrollment_returns_404(self):
        self.enrollment.delete()
        self.login()

        response = self.client.post(
            self.toggle_url,
            {"field": "display_on_leaderboard", "value": "false"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(enrollment_count_for(self.user, self.course), 0)

    def test_valid_field_with_existing_enrollment_updates_it(self):
        self.login()

        response = self.client.post(
            self.toggle_url,
            {"field": "display_on_leaderboard", "value": "false"},
        )

        self.assertEqual(response.status_code, 200)
        enrollment = Enrollment.objects.get(student=self.user, course=self.course)
        self.assertFalse(enrollment.display_on_leaderboard)

    def test_get_on_toggle_is_method_not_allowed(self):
        self.login()

        response = self.client.get(self.toggle_url)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(enrollment_count_for(self.user, self.course), 1)


class EnrollmentSettingsPageReadOnlyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(**credentials)
        self.course = Cohort.objects.create(
            slug="settings-course",
            title="Settings Course",
        )
        self.enrollment = Enrollment.objects.create(
            student=self.user,
            course=self.course,
        )
        self.enrollment_url = reverse(
            "cohort_enrollment",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )

    def test_get_without_enrollment_creates_nothing(self):
        self.enrollment.delete()
        self.client.login(**credentials)

        response = self.client.get(self.enrollment_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(enrollment_count_for(self.user, self.course), 0)
        form_instance = response.context["form"].instance
        self.assertIsNone(form_instance.pk)
        self.assertEqual(form_instance.student_id, self.user.id)

    def test_post_without_enrollment_creates_it(self):
        self.enrollment.delete()
        self.client.login(**credentials)

        response = self.client.post(
            self.enrollment_url,
            {
                "display_name": "Reader",
                "display_on_leaderboard": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        enrollment = Enrollment.objects.get(student=self.user, course=self.course)
        self.assertEqual(enrollment.display_name, "Reader")


class ProjectEvalPageReadOnlyTests(ProjectEvaluationTestBase):
    def test_eval_submit_context_creates_no_rows_and_reads_the_enrollment(self):
        # The reviewer's submission owns a non-nullable enrollment FK, so a
        # reachable review always has one; the contract under test is that
        # building the page context performs no write of its own and keeps
        # reading the enrollment's learning-in-public flag.
        self.enrollment.disable_learning_in_public = True
        self.enrollment.save(update_fields=["disable_learning_in_public"])
        request = RequestFactory().get("/eval")
        request.user = self.user
        page = ProjectEvalSubmitPage(
            course=self.course,
            project=self.project,
            review=self.peer_review,
            review_criteria=self.project.criteria_for_project(),
        )
        enrollments_before = Enrollment.objects.count()
        submissions_before = ProjectSubmission.objects.count()
        reviews_before = PeerReview.objects.count()

        context = project_eval_submit_context(request, page)

        self.assertTrue(context["disable_learning_in_public"])
        self.assertEqual(Enrollment.objects.count(), enrollments_before)
        self.assertEqual(ProjectSubmission.objects.count(), submissions_before)
        self.assertEqual(PeerReview.objects.count(), reviews_before)
