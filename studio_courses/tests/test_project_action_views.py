from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse

from courses.models import ProjectState
from courses.project_assignment import ProjectActionStatus
from studio_courses.tests.project_view_base import ProjectStudioCoursesViewTestBase


class ProjectActionViewTests(ProjectStudioCoursesViewTestBase):
    def test_project_score_queues_background_jobs(self):
        from community_base.jobs.models import JobIntent

        from courses.scoring_jobs import (
            PROJECT_NOTIFY_HANDLER,
            PROJECT_SCORE_HANDLER,
        )

        url = self.studio_courses_project_score_url()
        course_admin_url = self.studio_courses_course_url()

        self.login_admin()
        response = self.client.post(url, follow=True)

        self.assertRedirects(response, course_admin_url)
        messages = list(response.context["messages"])
        message_count = len(messages)
        self.assertEqual(message_count, 1)
        self.assertIn("queued", str(messages[0]).lower())
        self.assertTrue(
            JobIntent.objects.filter(handler=PROJECT_SCORE_HANDLER).exists()
        )
        self.assertTrue(
            JobIntent.objects.filter(handler=PROJECT_NOTIFY_HANDLER).exists()
        )

    def test_project_score_can_redirect_back_to_project_submissions(self):
        from community_base.jobs.models import JobIntent

        from courses.scoring_jobs import PROJECT_SCORE_HANDLER

        next_url = self.studio_courses_project_submissions_url()
        url = self.studio_courses_project_score_url()

        self.login_admin()
        response = self.client.post(
            url, {"next": next_url}, follow=True
        )

        self.assertRedirects(response, next_url)
        self.assertTrue(
            JobIntent.objects.filter(handler=PROJECT_SCORE_HANDLER).exists()
        )

    def test_project_assign_reviews_shows_message(
        self,
    ):
        url = self.studio_courses_project_assign_reviews_url()
        course_admin_url = self.studio_courses_course_url()

        self.login_admin()
        response = self.client.post(url, follow=True)

        self.assertRedirects(response, course_admin_url)
        messages = list(response.context["messages"])
        message_count = len(messages)
        self.assertEqual(message_count, 1)

    @patch(
        "studio_courses.views.projects.send_peer_review_assignment_notification"
    )
    def test_project_assign_reviews_can_redirect_back_to_project_submissions(
        self,
        send_assignment_notification,
    ):
        next_url = self.studio_courses_project_submissions_url()
        url = self.studio_courses_project_assign_reviews_url()

        self.login_admin()
        response = self.client.post(
            url, {"next": next_url}, follow=True
        )

        self.assertRedirects(response, next_url)
        send_assignment_notification.assert_not_called()


class ProjectExtendDeadlineViewTests(ProjectStudioCoursesViewTestBase):
    def extend_deadline_url(self):
        kwargs = {
            "course_slug": self.course.slug,
            "project_slug": self.project.slug,
        }
        return reverse("studio_courses_project_extend_deadline", kwargs=kwargs)

    def test_extend_deadline_moves_both_dates(self):
        self.login_admin()
        original_submission = self.project.submission_due_date
        original_review = self.project.peer_review_due_date

        response = self.client.post(
            self.extend_deadline_url(), {"days": 7}, follow=True
        )

        self.assertRedirects(response, self.studio_courses_course_url())
        self.project.refresh_from_db()
        self.assertEqual(
            self.project.submission_due_date,
            original_submission + timedelta(days=7),
        )
        self.assertEqual(
            self.project.peer_review_due_date,
            original_review + timedelta(days=7),
        )

    def test_extend_deadline_can_redirect_back_to_submissions(self):
        self.login_admin()
        next_url = self.studio_courses_project_submissions_url()

        response = self.client.post(
            self.extend_deadline_url(),
            {"days": 1, "next": next_url},
        )

        self.assertRedirects(response, next_url)

    def test_extend_deadline_rejects_invalid_days(self):
        self.login_admin()
        original_submission = self.project.submission_due_date
        original_review = self.project.peer_review_due_date

        response = self.client.post(
            self.extend_deadline_url(), {"days": 2}
        )

        self.assertRedirects(response, self.studio_courses_course_url())
        self.project.refresh_from_db()
        self.assertEqual(
            self.project.submission_due_date, original_submission
        )
        self.assertEqual(
            self.project.peer_review_due_date, original_review
        )

    def test_extend_deadline_during_peer_review_moves_only_review(self):
        self.project.state = ProjectState.PEER_REVIEWING.value
        self.project.save(update_fields=["state"])
        self.login_admin()
        original_submission = self.project.submission_due_date
        original_review = self.project.peer_review_due_date

        response = self.client.post(
            self.extend_deadline_url(), {"days": 3}, follow=True
        )

        self.assertRedirects(response, self.studio_courses_course_url())
        self.project.refresh_from_db()
        self.assertEqual(
            self.project.submission_due_date, original_submission
        )
        self.assertEqual(
            self.project.peer_review_due_date,
            original_review + timedelta(days=3),
        )

    def test_extend_deadline_not_allowed_when_completed(self):
        self.project.state = ProjectState.COMPLETED.value
        self.project.save(update_fields=["state"])
        self.login_admin()
        original_submission = self.project.submission_due_date
        original_review = self.project.peer_review_due_date

        response = self.client.post(
            self.extend_deadline_url(), {"days": 3}
        )

        self.assertRedirects(response, self.studio_courses_course_url())
        self.project.refresh_from_db()
        self.assertEqual(
            self.project.submission_due_date, original_submission
        )
        self.assertEqual(
            self.project.peer_review_due_date, original_review
        )
