from django.urls import reverse

from courses.models import HomeworkState
from courses.tests.dashboard_homework_base import (
    DashboardHomeworkStatsTestBase,
)


class DashboardHomeworkDifficultyTestCase(DashboardHomeworkStatsTestBase):
    def test_homework_difficulty_ranking(self):
        self.add_questions(self.homework, 3)
        harder_homework = self.create_homework_for_difficulty(
            "hw2",
            "Homework 2",
            14,
        )
        self.add_questions(harder_homework, 10)
        self.create_difficulty_submissions(harder_homework)

        url = reverse(
            "cohort_dashboard",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )
        response = self.client.get(url)

        self.assert_difficulty_ranking(response, harder_homework)
        # The difficulty ranking is still computed (asserted above through
        # the context), but the page no longer draws it as a second table
        # that repeats the homework titles and completion rates already
        # shown above -- ties in the ranking are marked on the merged
        # homework table as one "hardest tier" instead of a false 1-N order
        # (issue #237, finding 11).
        self.assertNotContains(response, "Assignment difficulty")
        self.assertContains(response, "hardest tier")

    def test_difficulty_excludes_unscored_homework(self):
        self.add_questions(self.homework, 3)
        unscored = self.create_homework_for_difficulty(
            "hw2",
            "Homework 2",
            14,
        )
        unscored.state = HomeworkState.OPEN.value
        unscored.save()
        self.add_questions(unscored, 3)
        self.create_difficulty_submissions(unscored)

        url = reverse(
            "cohort_dashboard",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )
        response = self.client.get(url)

        difficulty_stats = response.context["homework_difficulty_stats"]
        homework_titles = [
            hw_stat["homework"].title for hw_stat in difficulty_stats
        ]
        self.assertIn("Homework 1", homework_titles)
        self.assertNotIn("Homework 2", homework_titles)
