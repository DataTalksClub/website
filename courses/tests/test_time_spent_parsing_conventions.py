"""One optional-hours rule for homework, project, and review forms (BE-05).

The acceptance table is identical everywhere: empty means "not answered";
zero, decimal dot and comma parse; negative, malformed, NaN, and infinity
inputs are rejected with a field-specific ``ValidationError``, never saved
and never a 500.
"""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from courses.models import PeerReviewState
from courses.tests.homework_submission_validation_base import (
    HomeworkSubmissionValidationBase,
)
from courses.tests.project_eval_base import (
    ProjectEvaluationTestBase,
    fetch_fresh,
)
from courses.tests.project_submission_view_base import (
    ProjectSubmissionViewTestBase,
)
from courses.views.submission_formatting import parse_time_spent_hours


class ParseTimeSpentHoursTests(SimpleTestCase):
    def assert_invalid(self, raw):
        with self.assertRaises(ValidationError):
            parse_time_spent_hours(raw, "time spent on homework")

    def test_empty_and_missing_mean_not_answered(self):
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_time_spent_hours(raw, "hours"))

    def test_zero_and_positive_decimal_dot_and_comma_parse(self):
        table = {
            "0": 0.0,
            "2": 2.0,
            "2.5": 2.5,
            "2,5": 2.5,
            " 3,25 ": 3.25,
        }
        for raw, expected in table.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_time_spent_hours(raw, "hours"), expected)

    def test_negative_hours_are_rejected(self):
        for raw in ("-2", "-0,5"):
            with self.subTest(raw=raw):
                self.assert_invalid(raw)

    def test_non_finite_values_are_rejected(self):
        for raw in ("nan", "NaN", "inf", "-inf", "infinity", "1e999"):
            with self.subTest(raw=raw):
                self.assert_invalid(raw)

    def test_malformed_text_is_rejected(self):
        for raw in ("abc", "2 hrs", "2.5.1"):
            with self.subTest(raw=raw):
                self.assert_invalid(raw)

    def test_error_message_names_the_field(self):
        with self.assertRaises(ValidationError) as error:
            parse_time_spent_hours("nan", "time spent reviewing")

        self.assertIn("time spent reviewing", error.exception.messages[0])


class ProjectFormTimeSpentTests(ProjectSubmissionViewTestBase):
    def enable_time_spent_field(self):
        self.project.time_spent_project_field = True
        self.project.save()

    def test_decimal_comma_is_saved(self):
        self.enable_time_spent_field()

        response = self.post_project(self.project_submission_data(time_spent="2,5"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.get_project_submission().time_spent, 2.5)

    def test_rejected_values_render_the_form_and_save_nothing(self):
        self.enable_time_spent_field()
        for raw in ("-2", "nan", "inf", "abc"):
            with self.subTest(raw=raw):
                response = self.post_project(self.project_submission_data(time_spent=raw))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.project_submission_count(), 0)


class ReviewFormTimeSpentTests(ProjectEvaluationTestBase):
    def enable_time_spent_field(self):
        self.project.time_spent_evaluation_field = True
        self.project.save()

    def assert_review_unchanged(self):
        self.peer_review = fetch_fresh(self.peer_review)
        self.assertEqual(self.peer_review.state, PeerReviewState.TO_REVIEW.value)
        self.assertIsNone(self.peer_review.submitted_at)
        self.assertIsNone(self.peer_review.time_spent_reviewing)
        self.assertFalse(self.criteria_responses().exists())

    def test_decimal_comma_is_saved(self):
        self.enable_time_spent_field()

        response = self.post_eval_submit(self.review_post_data(time_spent_reviewing="2,5"))

        self.assertEqual(response.status_code, 302)
        self.peer_review = fetch_fresh(self.peer_review)
        self.assertEqual(self.peer_review.time_spent_reviewing, 2.5)

    def test_rejected_values_render_the_form_and_change_nothing(self):
        self.enable_time_spent_field()
        for raw in ("-2", "nan", "inf", "2 hrs"):
            with self.subTest(raw=raw):
                response = self.post_eval_submit(self.review_post_data(time_spent_reviewing=raw))

                self.assertEqual(response.status_code, 200)
                self.assert_review_unchanged()


class HomeworkFormTimeSpentTests(HomeworkSubmissionValidationBase):
    def post_homework_with_time_spent(self, raw):
        self.disable_homework_url_field()
        return self.post_homework(
            {
                f"answer_{self.question1.id}": ["1"],
                "time_spent_lectures": raw,
            }
        )

    def test_decimal_comma_is_saved(self):
        response = self.post_homework_with_time_spent("1,5")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.get_saved_submission().time_spent_lectures, 1.5)

    def test_rejected_values_render_the_form_and_save_nothing(self):
        for raw in ("nan", "inf", "abc"):
            with self.subTest(raw=raw):
                response = self.post_homework_with_time_spent(raw)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "valid number of hours")
                self.assert_no_submission()

    def test_negative_hours_name_the_rule(self):
        response = self.post_homework_with_time_spent("-2")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be negative")
        self.assert_no_submission()
