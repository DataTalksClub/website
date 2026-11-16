from django.template.loader import render_to_string
from django.urls import reverse

from accounts.services.timezones import format_user_datetime
from courses.models import (
    PeerReview,
    ProjectState,
    ProjectSubmission,
)
from courses.tests.project_eval_base import (
    ProjectEvaluationTestBase,
    credentials,
)


class ProjectEvaluationViewTestCase(ProjectEvaluationTestBase):
    def open_peer_review_without_submission(self):
        self.project.state = ProjectState.PEER_REVIEWING.value
        self.project.save()
        self.submission.delete()

    def create_volunteer_review(self):
        volunteer_submission = ProjectSubmission.objects.create(
            project=self.project,
            student=self.user,
            enrollment=self.enrollment,
            github_link="https://github.com/example/volunteer",
            commit_id="abcdef1",
            volunteer_review_only=True,
        )
        PeerReview.objects.create(
            submission_under_evaluation=self.other_submission,
            reviewer=volunteer_submission,
            note_to_peer="",
            optional=True,
        )

    def get_authenticated_eval_view(self):
        self.client.login(**credentials)
        eval_url = self.eval_view_url()
        return self.client.get(eval_url)

    def assert_optional_reviews_visible_without_submission(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_submission"])
        self.assertContains(response, "Selected reviews")
        self.assertContains(
            response,
            "these reviews are for practice and feedback",
        )
        self.assertContains(response, self.other_enrollment.display_name)
        self.assertNotContains(response, "Review progress")

    def test_eval_view_authenticated_no_submission(self):
        self.project.state = ProjectState.PEER_REVIEWING.value
        self.project.save()
        self.submission.delete()

        self.client.login(**credentials)
        url = reverse(
            "cohort_projects_eval",
            args=[self.course.course.slug, self.course.identifier, self.project.slug],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/eval.html")
        self.assertFalse(response.context["has_submission"])
        self.assertContains(
            response,
            "you can still volunteer to evaluate submissions",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Review progress",
            status_code=200,
        )

    def test_eval_view_shows_optional_reviews_without_submission(self):
        self.open_peer_review_without_submission()
        self.create_volunteer_review()

        response = self.get_authenticated_eval_view()

        self.assert_optional_reviews_visible_without_submission(response)

    def test_eval_view_authenticated_with_submission(self):
        self.project.state = ProjectState.PEER_REVIEWING.value
        self.project.save()
        self.client.login(**credentials)
        url = reverse(
            "cohort_projects_eval",
            args=[self.course.course.slug, self.course.identifier, self.project.slug],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/eval.html")
        self.assertTrue(response.context["has_submission"])
        self.assertNotContains(
            response,
            "you did not submit your project",
            status_code=200,
        )
        self.assertContains(
            response,
            "Evaluate",
            status_code=200,
        )

    def test_eval_view_separates_assigned_and_selected_reviews(self):
        self.project.state = ProjectState.PEER_REVIEWING.value
        self.project.save()
        PeerReview.objects.create(
            submission_under_evaluation=self.other_submission,
            reviewer=self.submission,
            note_to_peer="",
            optional=True,
        )

        self.client.login(**credentials)
        eval_url = reverse(
            "cohort_projects_eval",
            args=[self.course.course.slug, self.course.identifier, self.project.slug],
        )
        response = self.client.get(eval_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review progress")
        self.assertContains(response, "Selected reviews")
        assigned_reviews_count = len(response.context["assigned_reviews"])
        selected_reviews_count = len(response.context["selected_reviews"])
        self.assertEqual(assigned_reviews_count, 1)
        self.assertEqual(selected_reviews_count, 1)

    def test_eval_view_shows_closed_message_when_project_is_not_peer_reviewing(
        self,
    ):
        self.project.state = ProjectState.COMPLETED.value
        self.project.save()
        self.client.login(**credentials)
        url = reverse(
            "cohort_projects_eval",
            args=[self.course.course.slug, self.course.identifier, self.project.slug],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/eval.html")
        self.assertContains(
            response,
            "Peer review form is closed.",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Review progress",
            status_code=200,
        )

    def test_eval_view_shows_no_submission_closed_message_when_completed(self):
        self.project.state = ProjectState.COMPLETED.value
        self.project.save()
        self.submission.delete()
        self.client.login(**credentials)
        url = reverse(
            "cohort_projects_eval",
            args=[self.course.course.slug, self.course.identifier, self.project.slug],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/eval.html")
        self.assertContains(
            response,
            "submission window is closed",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Submission details",
            status_code=200,
        )


class UpcomingProjectEvaluationViewTests(ProjectEvaluationTestBase):
    def assert_upcoming_review(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["eval_not_open"])
        self.assertFalse(response.context["eval_closed"])
        self.assertContains(response, "Peer review has not opened yet.")
        self.assertContains(response, "peer reviewers have been assigned")
        self.assertNotContains(response, "Peer review form is closed.")
        self.assertNotContains(response, "submission window is closed")
        self.assertNotContains(response, "Review progress")
        self.assertNotContains(response, "Selected reviews")
        self.assertNotContains(response, "Choose a project to evaluate")
        self.assertContains(
            response,
            format_user_datetime(self.project.submission_due_date, response.wsgi_request),
        )
        self.assertContains(
            response,
            format_user_datetime(self.project.peer_review_due_date, response.wsgi_request),
        )

    def assert_upcoming_state_matrix(self):
        for state in (ProjectState.CLOSED, ProjectState.COLLECTING_SUBMISSIONS):
            self.project.state = state.value
            self.project.save(update_fields=["state"])
            for authenticated in (False, True):
                with self.subTest(state=state, authenticated=authenticated):
                    self.client.logout()
                    if authenticated:
                        self.client.login(**credentials)

                    response = self.client.get(self.eval_view_url())

                    self.assert_upcoming_review(response)
                    if not authenticated:
                        self.assertContains(response, 'href="' + reverse("login") + '"')

    def test_upcoming_with_submission(self):
        self.assert_upcoming_state_matrix()

    def test_upcoming_without_submission(self):
        self.submission.delete()
        self.assert_upcoming_state_matrix()

    def test_upcoming_dates_use_browser_timezone(self):
        self.client.cookies["browser_timezone"] = "Asia/Tokyo"
        self.project.state = ProjectState.CLOSED.value
        self.project.save(update_fields=["state"])

        response = self.client.get(self.eval_view_url())

        self.assert_upcoming_review(response)
        self.assertNotEqual(
            format_user_datetime(self.project.submission_due_date, response.wsgi_request),
            format_user_datetime(self.project.submission_due_date),
        )

    def test_upcoming_get_does_not_change_review_or_submission_rows(self):
        self.project.state = ProjectState.COLLECTING_SUBMISSIONS.value
        self.project.save(update_fields=["state"])
        self.client.login(**credentials)
        before = (ProjectSubmission.objects.count(), list(PeerReview.objects.values()))

        self.client.get(self.eval_view_url())

        self.assertEqual(
            before, (ProjectSubmission.objects.count(), list(PeerReview.objects.values()))
        )

    def test_upcoming_template_without_dates_has_readable_copy(self):
        self.project.state = ProjectState.CLOSED.value
        self.project.save(update_fields=["state"])
        response = self.client.get(self.eval_view_url())
        context = response.context[0].flatten()
        self.project.submission_due_date = None
        self.project.peer_review_due_date = None
        context["project"] = self.project

        html = render_to_string("projects/eval.html", context, request=response.wsgi_request)

        self.assertIn("Peer review has not opened yet.", html)
        self.assertNotIn("Project deadline:", html)
        self.assertNotIn("Peer review deadline:", html)
        self.assertNotIn("None", html)
