from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from courses.views.project_eval_submit_save import (
    ProjectCriteriaValidationError,
    validate_project_criteria_answers,
)


class ProjectCriteriaAnswerValidationTests(SimpleTestCase):
    def test_accepts_only_fields_for_the_project_rubric(self):
        criteria = [SimpleNamespace(id=11), SimpleNamespace(id=12)]

        validate_project_criteria_answers(
            criteria,
            {"answer_11": "1", "answer_12": "2"},
        )

    def test_rejects_unassigned_criterion_identifier(self):
        criteria = [SimpleNamespace(id=11)]

        with self.assertRaises(ProjectCriteriaValidationError) as error:
            validate_project_criteria_answers(
                criteria,
                {"answer_11": "1", "answer_99": "2"},
            )

        self.assertIsInstance(error.exception, ValidationError)
