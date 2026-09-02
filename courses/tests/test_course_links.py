from django.utils import timezone

from courses.models.cohort import RegistrationCampaign
from courses.tests.course_view_base import CourseDetailViewTestBase


class CourseDetailLinksTest(CourseDetailViewTestBase):
    def test_course_detail_registers_on_this_site_not_on_the_source_platform(self):
        """Registration is ours.

        This test used to assert that ``Cohort.registration_url`` was rendered as the
        Register button.  That URL is the course management platform's own campaign page:
        following it left the site for the platform this one replaces, and it was rendered
        with no regard for whether the edition was still open.  The internal campaign is
        the only registration a public page offers now.
        """

        self.course.start_date = timezone.localdate() + timezone.timedelta(days=7)
        self.course.registration_url = "https://courses.datatalks.club/register/test-course/"
        self.course.save()
        campaign = RegistrationCampaign.objects.create(
            slug="test-course",
            title="Test Course",
            current_course=self.course,
        )

        response = self.client.get(self.course_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Register for the cohort")
        self.assertContains(response, f'href="/courses/register/{campaign.slug}/"')
        self.assertNotContains(response, self.course.registration_url)

    def test_course_detail_with_no_campaign_offers_no_registration_at_all(self):
        self.course.start_date = timezone.localdate() + timezone.timedelta(days=7)
        self.course.registration_url = "https://courses.datatalks.club/register/test-course/"
        self.course.save()

        response = self.client.get(self.course_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.course.registration_url)
        self.assertNotContains(response, "Register for")

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
