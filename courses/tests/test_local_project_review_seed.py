import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.bootstrap import RuntimeEnvironment
from courses.models import (
    Enrollment,
    PeerReview,
    PeerReviewState,
    Project,
    ProjectState,
    ProjectSubmission,
    ReviewCriteria,
    User,
)
from courses.services.local_course_seed import seed_local_courses
from courses.services.local_project_review_seed import (
    DEFAULT_COURSE_SLUG,
    DEFAULT_PROJECT_SLUG,
    SYNTHETIC_USERNAME_PREFIX,
    LocalProjectReviewSeedError,
    seed_local_project_review,
)


def cohort_model():
    """Resolve the project cohort relation without importing legacy Course."""

    return Project._meta.get_field("course").remote_field.model


class LocalProjectReviewSeedTests(TestCase):
    def setUp(self) -> None:
        self.result = seed_local_project_review()
        self.course = cohort_model().objects.get(slug=DEFAULT_COURSE_SLUG)
        self.project = Project.objects.get(
            course=self.course,
            slug=DEFAULT_PROJECT_SLUG,
        )

    def test_seed_creates_submissions_and_opens_peer_review(self) -> None:
        submissions = ProjectSubmission.objects.filter(project=self.project)
        reviews = PeerReview.objects.filter(
            submission_under_evaluation__project=self.project,
        )

        self.assertEqual(self.result.submission_count, 6)
        self.assertEqual(submissions.count(), 6)
        self.assertEqual(
            reviews.count(),
            6 * self.project.number_of_peers_to_evaluate,
        )
        self.assertEqual(self.project.state, ProjectState.PEER_REVIEWING.value)
        self.assertEqual(
            set(reviews.values_list("state", flat=True)),
            {PeerReviewState.TO_REVIEW.value},
        )
        self.assertEqual(ReviewCriteria.objects.filter(course=self.course).count(), 3)
        self.assertTrue(
            all(
                submission.student.username.startswith(SYNTHETIC_USERNAME_PREFIX)
                for submission in submissions.select_related("student")
            )
        )

    def test_seed_is_idempotent_for_the_synthetic_scenario(self) -> None:
        second = seed_local_project_review()

        self.assertEqual(second.summary(), self.result.summary())
        self.assertEqual(
            ProjectSubmission.objects.filter(project=self.project).count(),
            6,
        )
        self.assertEqual(
            PeerReview.objects.filter(
                submission_under_evaluation__project=self.project,
            ).count(),
            18,
        )
        self.assertEqual(
            User.objects.filter(
                username__startswith=SYNTHETIC_USERNAME_PREFIX,
            ).count(),
            6,
        )
        self.assertEqual(
            Enrollment.objects.filter(
                course=self.course,
                student__username__startswith=SYNTHETIC_USERNAME_PREFIX,
            ).count(),
            6,
        )

    def test_command_reports_only_scenario_counts_and_state(self) -> None:
        stdout = StringIO()

        call_command("seed_local_project_review", stdout=stdout)

        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["course_slug"], DEFAULT_COURSE_SLUG)
        self.assertEqual(summary["project_slug"], DEFAULT_PROJECT_SLUG)
        self.assertEqual(summary["submissions"], 6)
        self.assertEqual(summary["peer_reviews"], 18)
        self.assertEqual(summary["state"], ProjectState.PEER_REVIEWING.value)
        self.assertNotIn("email", summary)
        self.assertNotIn("username", summary)

    def test_project_and_review_routes_render_the_scenario(self) -> None:
        learner = User.objects.get(username=f"{SYNTHETIC_USERNAME_PREFIX}01")
        self.client.force_login(learner)

        project_response = self.client.get(
            reverse(
                "project",
                kwargs={
                    "course_slug": self.course.slug,
                    "project_slug": self.project.slug,
                },
            )
        )
        self.assertEqual(project_response.status_code, 200)
        self.assertContains(project_response, 'class="needs-validation cmp-form project-form ')
        self.assertContains(project_response, 'class="field learning-in-public-field"')
        self.assertContains(project_response, "peer-reviewing phase")

        list_response = self.client.get(
            reverse(
                "project_list",
                kwargs={
                    "course_slug": self.course.slug,
                    "project_slug": self.project.slug,
                },
            )
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "6 total")
        self.assertContains(list_response, "Local Project 1 learner 01")
        self.assertContains(list_response, "Review")

        eval_response = self.client.get(
            reverse(
                "projects_eval",
                kwargs={
                    "course_slug": self.course.slug,
                    "project_slug": self.project.slug,
                },
            )
        )
        self.assertEqual(eval_response.status_code, 200)
        self.assertContains(eval_response, "Review progress")

        review = (
            PeerReview.objects.filter(
                reviewer__student=learner,
                submission_under_evaluation__project=self.project,
            )
            .order_by("id")
            .first()
        )
        self.assertIsNotNone(review)
        assert review is not None
        review_response = self.client.get(
            reverse(
                "projects_eval_submit",
                kwargs={
                    "course_slug": self.course.slug,
                    "project_slug": self.project.slug,
                    "review_id": review.id,
                },
            )
        )
        self.assertEqual(review_response.status_code, 200)
        self.assertContains(review_response, 'class="needs-validation cmp-form review-form ')
        self.assertContains(review_response, 'class="field learning-in-public-field"')
        self.assertContains(review_response, "Review criteria")


class LocalProjectReviewSeedSafetyTests(TestCase):
    @override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.DEVELOPMENT)
    def test_command_refuses_to_run_outside_local_or_test(self) -> None:
        with self.assertRaises(CommandError) as refusal:
            call_command("seed_local_project_review", stdout=StringIO())

        self.assertEqual(str(refusal.exception), "environment-not-local")

    def test_seed_refuses_to_replace_non_synthetic_project_submissions(self) -> None:
        seed_local_courses()
        course = cohort_model().objects.get(slug=DEFAULT_COURSE_SLUG)
        project = Project.objects.get(
            course=course,
            slug=DEFAULT_PROJECT_SLUG,
        )
        user = User.objects.create_user(
            email="existing-local-learner@example.invalid",
            password=None,
            username="existing-local-learner",
        )
        enrollment = Enrollment.objects.create(student=user, course=course)
        submission = ProjectSubmission.objects.create(
            project=project,
            student=user,
            enrollment=enrollment,
            github_link="https://github.com/example/existing-local-project",
            commit_id="1234567",
        )

        with self.assertRaises(LocalProjectReviewSeedError) as refusal:
            seed_local_project_review()

        self.assertEqual(str(refusal.exception), "project-has-unowned-submissions")
        self.assertTrue(ProjectSubmission.objects.filter(pk=submission.pk).exists())
