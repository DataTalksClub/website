from django.utils import timezone

from courses.models.cohort import RegistrationCampaign
from courses.tests.course_list_base import CourseListViewTestBase


class CourseListRegistrationTest(CourseListViewTestBase):
    """The catalogue registers on this site, or does not offer registration at all.

    The previous assertion pinned ``Cohort.registration_url`` -- the course management
    platform's own campaign page -- into the catalogue card.  A card that links there
    sends a reader off the site that is replacing it, and it did so whenever a start date
    was in the future regardless of whether this site could take the registration.
    """

    def _open_course(self):
        self.course.start_date = timezone.localdate() + timezone.timedelta(days=7)
        self.course.end_date = timezone.localdate() + timezone.timedelta(days=77)
        self.course.registration_url = "https://courses.datatalks.club/register/test-course/"
        self.course.save()

    def test_course_list_registers_through_the_campaign_on_this_site(self):
        self._open_course()
        campaign = RegistrationCampaign.objects.create(
            slug="test-course",
            title="Test Course",
            current_course=self.course,
        )

        response = self.course_list_response()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="/courses/register/{campaign.slug}/"')
        self.assertNotContains(response, self.course.registration_url)

    def test_course_list_never_links_registration_off_this_site(self):
        self._open_course()

        response = self.course_list_response()

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.course.registration_url)
