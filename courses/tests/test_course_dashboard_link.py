from django.urls import reverse

from courses.tests.course_view_base import CourseDetailViewTestBase


class CourseDashboardLinkTest(CourseDetailViewTestBase):
    def test_course_detail_keeps_dashboard_link_before_first_homework_scored(self):
        route_kwargs = {
            "course_slug": self.course.course.slug,
            "cohort_year": self.course.year,
        }
        url = reverse("course", kwargs=route_kwargs)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Course dashboard")
        dashboard_url = reverse(
            "dashboard",
            kwargs=route_kwargs,
        )
        self.assertContains(
            response,
            dashboard_url,
        )

    def test_course_detail_shows_dashboard_after_first_homework_scored(self):
        self.course.first_homework_scored = True
        self.course.save()
        route_kwargs = {
            "course_slug": self.course.course.slug,
            "cohort_year": self.course.year,
        }
        url = reverse("course", kwargs=route_kwargs)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Course dashboard")
        dashboard_url = reverse(
            "dashboard",
            kwargs=route_kwargs,
        )
        self.assertContains(
            response,
            dashboard_url,
        )
