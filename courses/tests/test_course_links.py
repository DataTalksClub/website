from django.utils import timezone

from courses.models.cohort import CurriculumFormat, RegistrationCampaign
from courses.models.shared_curriculum import SharedCurriculum, SharedModule
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
        # Design system (issue #179) dropped the copied Font Awesome glyphs with the rest of
        # the adopted stylesheet, so the action is asserted by its own words and target.
        self.assertContains(response, "Course materials on GitHub")
        self.assertContains(
            response,
            'href="https://github.com/DataTalksClub/test-course"',
        )
        self.assertNotContains(response, "fas fa-")

    def test_shared_curriculum_cohort_keeps_materials_on_the_platform(self):
        # A cohort whose curriculum was imported has real module pages, so the
        # materials action should keep the visitor on the platform instead of
        # sending them to the source repository -- even though the repo URL
        # is still set (it's still the source, just not the destination).
        self.course.github_repo_url = "https://github.com/DataTalksClub/test-course"
        self.course.curriculum_format = CurriculumFormat.SHARED
        self.course.save()
        curriculum = SharedCurriculum.objects.create(course=self.course.course)
        module = SharedModule.objects.create(
            curriculum=curriculum, position=0, slug="module-one", title="Module One"
        )

        response = self.client.get(self.course_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Course materials")
        self.assertNotContains(response, "Course materials on GitHub")
        self.assertContains(
            response,
            f'href="/courses/{self.course.course.slug}/{module.slug}"',
        )
        self.assertNotContains(response, "https://github.com/DataTalksClub/test-course")
