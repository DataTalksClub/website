from datetime import timedelta

from django.test import Client
from django.utils import timezone

from courses.models import (
    Enrollment,
    PeerReview,
    PeerReviewState,
    Project,
    ProjectState,
    ProjectSubmission,
    User,
)
from courses.project_assignment import ProjectActionStatus
from courses.tests.project_assign_base import (
    ProjectActionsTestBase,
    credentials,
)


class ProjectOptionalEvaluationTestCase(ProjectActionsTestBase):
    def test_add_optional_project_eval_flow(self):
        num_submissions = 10
        self.generate_submissions(num_submissions)

        my_submission = self.create_my_submission()

        self.project.number_of_peers_to_evaluate = 3
        self.project.save()

        status, _ = self.assign_peer_reviews()
        self.assertEqual(status, ProjectActionStatus.OK)

        self.client.login(**credentials)
        other_submission_id = self.find_optional_eval_candidate_id()
        other_submission = ProjectSubmission.objects.get(
            id=other_submission_id
        )

        self.add_optional_eval_and_assert_redirect(other_submission)

        peer_review = self.get_peer_review(my_submission, other_submission)

        self.assertEqual(peer_review.optional, True)
        self.assertEqual(
            peer_review.state, PeerReviewState.TO_REVIEW.value
        )

    def test_add_optional_project_eval(self):
        my_submission = self.create_my_submission()

        num_submissions = 5
        other_submissions = self.generate_submissions(num_submissions)
        other_submission = other_submissions[0]

        self.add_optional_eval_and_assert_redirect(other_submission)
        self.assert_optional_peer_review_created(
            my_submission,
            other_submission,
        )

    def test_add_optional_project_self_eval_not_possible(self):
        my_submission = self.create_my_submission()

        num_submissions = 5
        self.generate_submissions(num_submissions)

        self.add_optional_eval_and_assert_redirect(my_submission)
        self.assert_no_peer_review(my_submission, my_submission)

    def test_delete_optional_project_eval_non_optional(self):
        my_submission = self.create_my_submission()
        other_submission = self.generate_submissions(5)[0]
        peer_review = self.create_peer_review(
            my_submission,
            other_submission,
            optional=False,
        )

        self.client.login(**credentials)

        delete_url = self.delete_eval_url(peer_review.id)
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 302)
        redirect_url = self.projects_eval_url()
        self.assertRedirects(
            response,
            redirect_url,
            fetch_redirect_response=False,
        )

        peer_review_exists = PeerReview.objects.filter(
            id=peer_review.id
        ).exists()
        self.assertTrue(peer_review_exists)

    def test_delete_optional_project_eval_optional(self):
        my_submission = self.create_my_submission()
        other_submissions = self.generate_submissions(5)
        other_submission = other_submissions[0]
        peer_review = self.create_peer_review(
            my_submission,
            other_submission,
            optional=True,
        )

        response = self.delete_peer_review_response(peer_review)
        self.assertEqual(response.status_code, 302)

        self.assert_peer_review_deleted(peer_review)

    def test_delete_project_eval_from_other_user(self):
        my_submission = self.create_my_submission()
        other_submissions = self.generate_submissions(5)
        other_submission = other_submissions[0]
        peer_review = self.create_peer_review(
            other_submission,
            my_submission,
            optional=True,
        )

        response = self.delete_peer_review_response(peer_review)
        self.assertEqual(response.status_code, 302)

        self.assert_peer_review_still_exists(peer_review)

class ProjectEvalMutationMethodSafetyTests(ProjectActionsTestBase):
    """Audit BE-07: the optional-review add/delete mutations are POST-only
    with CSRF, and a rejected request never creates volunteer records."""

    def counts(self):
        return (
            ProjectSubmission.objects.count(),
            PeerReview.objects.count(),
            Enrollment.objects.count(),
        )

    def setUp(self):
        super().setUp()
        self.my_submission = self.create_my_submission()
        self.other_submission = self.generate_submissions(3)[0]

    def test_get_and_head_on_add_and_delete_are_refused_and_change_nothing(self):
        before = self.counts()
        self.client.login(**credentials)

        for method in ("get", "head"):
            add_response = getattr(self.client, method)(
                self.add_eval_url(self.other_submission.id)
            )
            delete_response = getattr(self.client, method)(
                self.delete_eval_url(1)
            )
            with self.subTest(method=method):
                self.assertEqual(add_response.status_code, 405)
                self.assertEqual(delete_response.status_code, 405)

        self.assertEqual(self.counts(), before)

    def test_post_without_csrf_is_rejected_and_changes_nothing(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(**credentials)
        before = self.counts()

        add_response = csrf_client.post(
            self.add_eval_url(self.other_submission.id)
        )
        delete_response = csrf_client.post(self.delete_eval_url(1))

        self.assertEqual(add_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        self.assertEqual(self.counts(), before)

    def test_add_with_missing_target_is_not_found_and_creates_nothing(self):
        self.client.login(**credentials)
        before = self.counts()

        response = self.client.post(self.add_eval_url(999999))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.counts(), before)

    def test_add_with_cross_project_target_is_not_found_and_creates_nothing(self):
        other_project = Project.objects.create(
            course=self.course,
            slug="other-project",
            title="Other Project",
            submission_due_date=timezone.now() - timedelta(hours=1),
            peer_review_due_date=timezone.now() + timedelta(hours=1),
            state=ProjectState.PEER_REVIEWING.value,
        )
        owner = User.objects.create_user(
            username="elsewhere-owner",
            email="elsewhere-owner@email.com",
            password="12345",
        )
        foreign_submission = ProjectSubmission.objects.create(
            project=other_project,
            student=owner,
            enrollment=Enrollment.objects.create(
                student=owner,
                course=self.course,
            ),
            github_link="https://github.com/elsewhere/project",
        )
        self.client.login(**credentials)
        before = self.counts()

        response = self.client.post(self.add_eval_url(foreign_submission.id))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.counts(), before)

    def test_add_with_self_target_creates_no_volunteer_records(self):
        self.client.login(**credentials)
        before = self.counts()

        response = self.client.post(self.add_eval_url(self.my_submission.id))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.counts(), before)
        self.assertFalse(
            ProjectSubmission.objects.filter(
                student=self.user, volunteer_review_only=True
            ).exists()
        )
