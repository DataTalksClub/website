from django.utils import timezone

from courses.tests.course_view_base import CourseDetailViewTestBase


class CourseDetailLinksTest(CourseDetailViewTestBase):
    def test_course_detail_shows_registration_url(self):
        self.course.start_date = timezone.localdate() + timezone.timedelta(days=7)
        self.course.registration_url = "https://courses.datatalks.club/test-course/register"
        self.course.save()

        url = self.course_url()

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Register")
        self.assertContains(
            response,
            "https://courses.datatalks.club/test-course/register",
        )

    def test_course_detail_shows_github_repo_url(self):
        self.course.github_repo_url = "https://github.com/DataTalksClub/test-course"
        self.course.save()

        url = self.course_url()

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        # Design 5a (issue #179) dropped the copied Font Awesome glyphs with the rest of
        # the adopted stylesheet, so the action is asserted by its own words and target.
        self.assertContains(response, "Course materials on GitHub")
        self.assertContains(
            response,
            'href="https://github.com/DataTalksClub/test-course"',
        )
        self.assertNotContains(response, "fas fa-")
