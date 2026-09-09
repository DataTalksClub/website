from datetime import timedelta
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from courses.models import (
    CriteriaResponse,
    InvalidCriteriaAnswerError,
    PeerReviewState,
    ProjectEvaluationScore,
    ProjectState,
    ReviewCriteriaTypes,
)
from courses.project_assignment import ProjectActionStatus
from courses.project_scoring import score_project
from courses.tests.project_eval_base import (
    ProjectEvaluationTestBase,
    fetch_fresh,
)
from courses.views.project_eval_submit_save import (
    ProjectCriteriaValidationError,
    validate_criteria_answer_value,
    validate_project_criteria_answers,
)


def make_criteria(**overrides):
    fields = dict(
        id=11,
        description="Code quality",
        options=[
            {"criteria": "Poor", "score": 0},
            {"criteria": "Satisfactory", "score": 1},
            {"criteria": "Good", "score": 2},
        ],
        review_criteria_type=ReviewCriteriaTypes.RADIO_BUTTONS.value,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def assert_message_contains(error, fragment):
    if not any(fragment in message for message in error.messages):
        raise AssertionError(f"Expected {fragment!r} in {error.messages}")


class ProjectCriteriaAnswerValidationTests(SimpleTestCase):
    def test_accepts_only_fields_for_the_project_rubric(self):
        criteria = [make_criteria(id=11), make_criteria(id=12)]

        validate_project_criteria_answers(
            criteria,
            {"answer_11": "1", "answer_12": "2"},
        )

    def test_rejects_unassigned_criterion_identifier(self):
        criteria = [make_criteria(id=11)]

        with self.assertRaises(ProjectCriteriaValidationError) as error:
            validate_project_criteria_answers(
                criteria,
                {"answer_11": "1", "answer_99": "2"},
            )

        self.assertIsInstance(error.exception, ValidationError)

    def test_accepts_canonical_option_indexes(self):
        validate_criteria_answer_value(make_criteria(), "1")
        validate_criteria_answer_value(make_criteria(), "3")

    def test_accepts_empty_answer_as_not_answered(self):
        validate_criteria_answer_value(make_criteria(), "")
        validate_criteria_answer_value(make_criteria(), None)

    def test_rejects_choice_beyond_the_option_count(self):
        with self.assertRaises(ValidationError) as error:
            validate_criteria_answer_value(make_criteria(), "4")

        assert_message_contains(error.exception, "not one of the available choices")

    def test_rejects_zero_and_negative_choices(self):
        for raw in ("0", "-1"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    validate_criteria_answer_value(make_criteria(), raw)

    def test_rejects_nonnumeric_and_noncanonical_choices(self):
        for raw in ("abc", "1.5", "01", "+1", "1 ", "1,"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    validate_criteria_answer_value(make_criteria(), raw)

    def test_rejects_repeated_choices(self):
        with self.assertRaises(ValidationError) as error:
            validate_criteria_answer_value(make_criteria(), "2,2")

        assert_message_contains(error.exception, "at most once")

    def test_radio_criterion_accepts_exactly_one_choice(self):
        with self.assertRaises(ValidationError) as error:
            validate_criteria_answer_value(make_criteria(), "1,2")

        assert_message_contains(error.exception, "Select exactly one option")

    def test_checkbox_criterion_accepts_distinct_subsets(self):
        checkbox = make_criteria(
            id=12,
            description="Best practices",
            review_criteria_type=ReviewCriteriaTypes.CHECKBOXES.value,
        )
        validate_criteria_answer_value(checkbox, "1,2,3")
        validate_criteria_answer_value(checkbox, "")

        with self.assertRaises(ValidationError):
            validate_criteria_answer_value(checkbox, "1,1,2")

    def test_rejects_unsupported_criterion_type(self):
        with self.assertRaises(ValidationError):
            validate_criteria_answer_value(
                make_criteria(review_criteria_type="XX"), "1"
            )


class CriteriaResponseParsingTests(ProjectEvaluationTestBase):
    def store_answer(self, answer):
        return CriteriaResponse.objects.create(
            review=self.peer_review,
            criteria=self.criteria1,
            answer=answer,
        )

    def test_empty_answer_scores_zero(self):
        response = self.store_answer("")

        self.assertEqual(response.parse_answer_indexes(), [])
        self.assertEqual(response.get_scores(), [0])
        self.assertEqual(response.get_score(), 0)

    def test_valid_answer_maps_to_option_scores(self):
        response = self.store_answer("2")

        self.assertEqual(response.parse_answer_indexes(), [2])
        self.assertEqual(response.get_scores(), [1])
        self.assertEqual(response.get_score(), 1)

    def test_unreadable_stored_answer_raises_controlled_error(self):
        response = self.store_answer("abc")

        with self.assertRaises(InvalidCriteriaAnswerError) as error:
            response.get_scores()

        self.assertEqual(error.exception.response_id, response.pk)
        self.assertEqual(error.exception.review_id, self.peer_review.id)
        self.assertEqual(error.exception.criteria_id, self.criteria1.id)

    def test_out_of_range_stored_answer_raises_controlled_error(self):
        for answer in ("0", "999", "-1"):
            with self.subTest(answer=answer):
                response = self.store_answer(answer)
                with self.assertRaises(InvalidCriteriaAnswerError):
                    response.get_scores()


class ProjectCriteriaAnswerSubmissionTests(ProjectEvaluationTestBase):
    def post_answer(self, criteria, value):
        post_data = self.review_post_data(**{f"answer_{criteria.id}": value})
        return self.post_eval_submit(post_data)

    def assert_submission_rejected(self, response, message_fragment):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, message_fragment, status_code=200)
        self.peer_review = fetch_fresh(self.peer_review)
        self.assertEqual(
            self.peer_review.state, PeerReviewState.TO_REVIEW.value
        )
        self.assertIsNone(self.peer_review.submitted_at)
        self.assertFalse(
            CriteriaResponse.objects.filter(review=self.peer_review).exists()
        )

    def test_multiple_radio_choices_are_rejected_atomically(self):
        response = self.post_answer(self.criteria1, "1,2")
        self.assert_submission_rejected(response, "Select exactly one option")

    def test_out_of_range_choice_is_rejected_atomically(self):
        response = self.post_answer(self.criteria1, "999")
        self.assert_submission_rejected(
            response, "not one of the available choices"
        )

    def test_repeated_checkbox_choices_are_rejected_atomically(self):
        response = self.post_answer(self.criteria3, "1,1,2")
        self.assert_submission_rejected(response, "at most once")

    def test_nonnumeric_choice_is_rejected_atomically(self):
        response = self.post_answer(self.criteria1, "2.5")
        self.assert_submission_rejected(response, "are not valid choices")

    def test_three_option_radio_cannot_score_beyond_its_maximum(self):
        three_option = self.create_review_criteria(
            "Effort",
            [
                {"criteria": "Low", "score": 0},
                {"criteria": "Medium", "score": 1},
                {"criteria": "High", "score": 2},
            ],
            ReviewCriteriaTypes.RADIO_BUTTONS.value,
        )
        response = self.post_answer(three_option, "4")

        self.assert_submission_rejected(
            response, "not one of the available choices"
        )

    def test_valid_choices_still_save(self):
        response = self.post_answer(self.criteria3, "1,2,5")

        self.assertEqual(response.status_code, 302)
        self.peer_review = fetch_fresh(self.peer_review)
        self.assertEqual(
            self.peer_review.state, PeerReviewState.SUBMITTED.value
        )
        saved = CriteriaResponse.objects.get(
            review=self.peer_review, criteria=self.criteria3
        )
        self.assertEqual(saved.answer, "1,2,5")


class CriteriaScoringDefensivenessTests(ProjectEvaluationTestBase):
    def make_project_scoreable(self):
        self.course.project_passing_score = 10
        self.course.save()
        self.project.peer_review_due_date = timezone.now() - timedelta(hours=1)
        self.project.save()
        self.peer_review.state = PeerReviewState.SUBMITTED.value
        self.peer_review.submitted_at = timezone.now()
        self.peer_review.save()

    def test_scoring_fails_on_invalid_stored_answer(self):
        self.make_project_scoreable()
        CriteriaResponse.objects.create(
            review=self.peer_review,
            criteria=self.criteria1,
            answer="999",
        )

        status, message = score_project(self.project)

        self.assertEqual(status, ProjectActionStatus.FAIL)
        self.assertIn(f"review {self.peer_review.id}", message)
        self.assertIn(f"criterion {self.criteria1.id}", message)
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, ProjectState.PEER_REVIEWING.value)
        self.assertFalse(ProjectEvaluationScore.objects.exists())

    def test_scoring_succeeds_with_valid_stored_answers(self):
        self.make_project_scoreable()
        CriteriaResponse.objects.create(
            review=self.peer_review,
            criteria=self.criteria1,
            answer="2",
        )
        CriteriaResponse.objects.create(
            review=self.peer_review,
            criteria=self.criteria2,
            answer="3",
        )

        status, message = score_project(self.project)

        self.assertEqual(status, ProjectActionStatus.OK, message)
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, ProjectState.COMPLETED.value)
