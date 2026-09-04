from django.urls import reverse

from courses.tests.course_view_base import (
    CourseDetailViewTestBase,
    credentials,
)


class CourseHomeworkDisplayTest(CourseDetailViewTestBase):
    def test_course_detail_does_not_show_time_left_for_scored_homework(self):
        url = reverse(
            "course",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_year": self.course.year,
            },
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Already scored")
        # Design system (issue #179) replaced the copied badge with the design system's
        # status pill; the state is still carried by the word, not by colour alone.
        self.assertContains(
            response,
            'class="status-pill status-pill-mint">Scored',
        )
        self.assertNotContains(response, "app-badge")
        self.assertNotContains(response, "Scored:")
        self.assertNotContains(
            response,
            f'data-deadline="{self.homework1.due_date.isoformat()}"',
        )

    def test_homeworks_sorted_by_due_date(self):
        self.create_sorted_homework_fixture()

        course_url = self.course_url()
        response = self.client.get(course_url)

        self.assertEqual(response.status_code, 200)
        self.assert_homeworks_in_due_order(response)

        self.client.login(**credentials)
        response = self.client.get(course_url)

        self.assertEqual(response.status_code, 200)
        self.assert_homeworks_in_due_order(response)
