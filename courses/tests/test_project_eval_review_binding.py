from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse

from courses.models import (
    Cohort,
    CriteriaResponse,
    PeerReviewState,
    Project,
    ProjectCriteriaAssignment,
    ProjectState,
    ReviewCriteria,
    ReviewCriteriaTypes,
)
from courses.project_assignment import ProjectActionStatus
from courses.project_scoring import score_project
from courses.tests.project_eval_base import (
    ProjectEvaluationTestBase,
    credentials,
    fetch_fresh,
)
from courses.views.project_eval_submit_save import (
    project_eval_post_submission,
)


class ReviewProjectBindingTestBase(ProjectEvaluationTestBase):
    """The review belongs to ``self.project``; ``project_b`` is a second,
    independently owned project in the same cohort."""

    def setUp(self):
        super().setUp()
        self.project_b = Project.objects.create(
            course=self.course,
            slug="project-b",
            title="Project B",
            submission_due_date=self.project.submission_due_date,
            peer_review_due_date=self.project.peer_review_due_date,
            state=ProjectState.PEER_REVIEWING.value,
        )
        self.close_own_project()

    def close_own_project(self):
        Project.objects.filter(pk=self.project.pk).update(state=ProjectState.COMPLETED.value)
        self.project.refresh_from_db()

    def eval_submit_url_for(self, project_slug, review_id=None):
        return reverse(
            "cohort_projects_eval_submit",
            args=[
                self.course.course.slug,
                self.course.identifier,
                project_slug,
                review_id or self.peer_review.id,
            ],
        )

    def assert_review_unchanged(self):
        review = fetch_fresh(self.peer_review)
        self.assertEqual(review.state, PeerReviewState.TO_REVIEW.value)
        self.assertIsNone(review.submitted_at)
        self.assertEqual(review.note_to_peer, "")
        self.assertFalse(CriteriaResponse.objects.filter(review=review).exists())


class CrossProjectReviewUrlDeniedTests(ReviewProjectBindingTestBase):
    def post_to_open_project_url(self, post_data=None):
        self.client.login(**credentials)
        return self.client.post(
            self.eval_submit_url_for(self.project_b.slug),
            post_data or self.review_post_data(),
        )

    def test_get_via_open_project_url_does_not_expose_closed_review(self):
        self.client.login(**credentials)
        response = self.client.get(self.eval_submit_url_for(self.project_b.slug))

        self.assertEqual(response.status_code, 302)
        self.assert_review_unchanged()

    def test_post_via_open_project_url_does_not_change_closed_review(self):
        response = self.post_to_open_project_url()

        self.assertEqual(response.status_code, 302)
        self.assert_review_unchanged()

    def test_post_via_open_project_url_with_forged_criteria_is_denied(self):
        post_data = self.review_post_data(**{"answer_999999": "1"})
        response = self.post_to_open_project_url(post_data)

        self.assertEqual(response.status_code, 302)
        self.assert_review_unchanged()

    def test_other_cohort_url_returns_not_found(self):
        cohort_b = Cohort.objects.create(
            slug="test-course-spring-2027",
            course=self.course.course,
            identifier="spring-2027",
            year=2027,
            title="Test Course spring 2027",
        )
        self.client.login(**credentials)
        url = reverse(
            "cohort_projects_eval_submit",
            args=[
                self.course.course.slug,
                cohort_b.identifier,
                self.project_b.slug,
                self.peer_review.id,
            ],
        )
        get_response = self.client.get(url)
        post_response = self.client.post(url, self.review_post_data())

        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)
        self.assert_review_unchanged()

    def test_other_learner_cannot_read_or_write_review(self):
        self.client.login(username="student", email="email@email.com", password="12345")
        url = self.eval_submit_url_for(self.project.slug)
        get_response = self.client.get(url)
        post_response = self.client.post(url, self.review_post_data())

        self.assertEqual(get_response.status_code, 302)
        self.assertEqual(post_response.status_code, 302)
        self.assert_review_unchanged()


class DistinctRubricBindingTests(ReviewProjectBindingTestBase):
    """Project B has its own assigned rubric, so answers are valid for B's
    criteria and must still not be written onto the closed project's review."""

    def create_project_criteria(self, project, description):
        criteria = ReviewCriteria.objects.create(
            course=self.course,
            description=description,
            options=[
                {"criteria": "Poor", "score": 0},
                {"criteria": "Good", "score": 2},
            ],
            review_criteria_type=ReviewCriteriaTypes.RADIO_BUTTONS.value,
        )
        ProjectCriteriaAssignment.objects.create(
            project=project,
            criteria=criteria,
            position=0,
        )
        return criteria

    def test_open_project_answers_never_reach_closed_project_review(self):
        criteria_b = self.create_project_criteria(self.project_b, "Project B quality")
        post_data = {
            "note_to_peer": "Smuggled via project B",
            f"answer_{criteria_b.id}": "2",
        }

        self.client.login(**credentials)
        response = self.client.post(self.eval_submit_url_for(self.project_b.slug), post_data)

        self.assertEqual(response.status_code, 302)
        self.assert_review_unchanged()


class MutationTransactionStateRecheckTests(ReviewProjectBindingTestBase):
    def test_save_rejects_completed_project_inside_transaction(self):
        factory = RequestFactory()
        request = factory.post("/eval", {"note_to_peer": "late entry"})

        with self.assertRaises(ValidationError) as error:
            project_eval_post_submission(
                request,
                self.project,
                self.peer_review,
                self.project.criteria_for_project(),
            )

        self.assertIn("Peer review form is closed.", error.exception.messages)
        self.assert_review_unchanged()


class ScoreProjectStateRecheckTests(ReviewProjectBindingTestBase):
    def setUp(self):
        super().setUp()
        self.course.project_passing_score = 10
        self.course.save()

    def test_score_project_rechecks_state_from_database(self):
        # The caller's instance still claims PEER_REVIEWING; the database row
        # has already been moved to COMPLETED.
        self.project.state = ProjectState.PEER_REVIEWING.value
        Project.objects.filter(pk=self.project.pk).update(state=ProjectState.COMPLETED.value)

        status, message = score_project(self.project)

        self.assertEqual(status, ProjectActionStatus.FAIL)
        self.assertIn("PEER_REVIEWING", message)
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, ProjectState.COMPLETED.value)
