from django.urls import reverse

from courses.models import Cohort
from courses.tests.dashboard_view_base import DashboardViewTestBase


class DashboardEmptyStateTestCase(DashboardViewTestBase):
    def test_dashboard_with_invalid_course(self):
        url = reverse(
            "dashboard",
            kwargs={"course_slug": "non-existent-course", "cohort_year": 2026},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_dashboard_with_no_enrollments(self):
        empty_course = Cohort.objects.create(
            slug="empty-course",
            title="Empty Course",
            first_homework_scored=True,
        )

        url = reverse(
            "dashboard",
            kwargs={
                "course_slug": empty_course.course.slug,
                "cohort_year": empty_course.year,
            },
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_enrollments"], 0)
