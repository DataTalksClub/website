from courses.models import (
    Enrollment,
    ProjectEvaluationScore,
    ProjectSubmission,
    User,
)
from courses.project_assignment import ProjectActionStatus
from courses.project_review_scores import calculate_median_score
from courses.project_scoring import score_project
from courses.tests.project_score_base import ProjectEvaluationTestBase

non_submitter_credentials = dict(
    username="bystander@test.com",
    email="bystander@test.com",
    password="12345",
)


class ProjectResultsTestCase(ProjectEvaluationTestBase):
    def test_project_results_shows_review_option_vote_counts(self):
        checkbox_criteria = self.create_checkbox_criteria()
        self.submit_checkbox_responses(checkbox_criteria)

        status, _ = score_project(self.project)
        self.assertEqual(status, ProjectActionStatus.OK)

        response = self.project_results_response()

        self.assertEqual(response.status_code, 200)
        self.assert_option_vote_counts(response)
        self.assert_option_vote_content(response)


class NoSubmissionResultsTests(ProjectEvaluationTestBase):
    """An authenticated reader without a submitted project gets the empty
    state, not a crash, and the GET creates nothing (audit BE-17)."""

    def setUp(self):
        super().setUp()
        self.bystander = User.objects.create_user(**non_submitter_credentials)
        Enrollment.objects.create(student=self.bystander, course=self.course)

    def results_response_for_bystander(self):
        from django.test import Client
        from django.urls import reverse

        client = Client()
        client.login(**non_submitter_credentials)
        results_url = reverse(
            "cohort_project_results",
            args=[self.course.course.slug, self.course.identifier, self.project.slug],
        )
        return client.get(results_url)

    def test_authenticated_non_submitter_sees_the_empty_state(self):
        submissions_before = ProjectSubmission.objects.count()

        response = self.results_response_for_bystander()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "You did not make a submission for this project."
        )
        self.assertEqual(response.context["submission"], None)
        self.assertEqual(response.context["scores"], [])
        self.assertEqual(response.context["feedback"], [])
        self.assertTrue(response.context["is_authenticated"])
        self.assertEqual(ProjectSubmission.objects.count(), submissions_before)

    def test_volunteer_only_submitter_still_gets_the_empty_state(self):
        # A volunteer reviewer technically has a submission row, but it is
        # excluded from graded results by volunteer_review_only.
        ProjectSubmission.objects.create(
            project=self.project,
            student=self.bystander,
            enrollment=self.bystander.enrollment_set.get(),
            github_link="https://github.com/volunteer/project",
            commit_id="volunt01",
            volunteer_review_only=True,
        )

        response = self.results_response_for_bystander()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "You did not make a submission for this project."
        )

    def test_anonymous_reader_still_sees_the_sign_in_message(self):
        from django.test import Client
        from django.urls import reverse

        response = Client().get(
            reverse(
                "cohort_project_results",
                args=[
                    self.course.course.slug,
                    self.course.identifier,
                    self.project.slug,
                ],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "log in")


class NoReviewFallbackResultsTests(ProjectEvaluationTestBase):
    """Median fallback scores exist without any submitted review responses;
    rendering them must not crash and must show zero votes per option."""

    def save_median_fallback_scores(self, criteria):
        _total, scores = calculate_median_score(self.submission, [criteria])
        for score in scores:
            score.save()
        return scores

    def test_median_scores_without_responses_render_zero_votes(self):
        scores = self.save_median_fallback_scores(self.criteria)

        response = self.project_results_response()

        self.assertEqual(response.status_code, 200)
        rendered = response.context["scores"]
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0].pk, scores[0].pk)
        for option in rendered[0].option_vote_counts:
            self.assertEqual(option["votes"], 0)

    def test_one_criterion_without_responses_among_populated_ones(self):
        # The peer reviews answer only the first criterion; the second one
        # still carries an evaluation score (median fallback) but no votes.
        answered = self.create_checkbox_criteria()
        self.submit_checkbox_responses(answered)
        status, message = score_project(self.project)
        self.assertEqual(status, ProjectActionStatus.OK, message)
        unanswered = self.create_review_criteria()
        self.save_median_fallback_scores(unanswered)

        response = self.project_results_response()

        self.assertEqual(response.status_code, 200)
        votes_by_description = {
            option["criteria"]: option["votes"]
            for score in response.context["scores"]
            for option in score.option_vote_counts
        }
        self.assertEqual(
            votes_by_description,
            {
                "Data cleaning": 1,
                "Feature engineering": 1,
                "Model evaluation": 3,
                "Poor": 0,
                "Satisfactory": 0,
                "Good": 0,
                "Excellent": 0,
            },
        )
        # Zero votes on the unanswered criterion do not change its awarded
        # median score.
        self.assertTrue(
            ProjectEvaluationScore.objects.filter(
                submission=self.submission,
                review_criteria=unanswered,
            ).exists()
        )
