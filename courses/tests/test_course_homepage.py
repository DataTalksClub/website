from django.test import SimpleTestCase

from courses.models.cohort import Cohort
from courses.views.course_homepage import get_course_outcome


class CourseHomepageOutcomeTests(SimpleTestCase):
    def test_uses_model_outcome_before_description(self):
        course = Cohort(
            slug="ml-2026",
            title="Machine Learning 2026",
            description="The model description.",
            outcome="The model outcome.",
        )

        self.assertEqual(get_course_outcome(course), "The model outcome.")

    def test_falls_back_to_model_description_when_outcome_is_blank(self):
        course = Cohort(
            slug="de-2026",
            title="Data Engineering 2026",
            description="The model description.",
            outcome="",
        )

        self.assertEqual(get_course_outcome(course), "The model description.")

    def test_does_not_invent_outcome_from_slug(self):
        course = Cohort(
            slug="llm-2026",
            title="LLM Zoomcamp 2026",
            description="",
            outcome="",
        )

        self.assertEqual(get_course_outcome(course), "")
