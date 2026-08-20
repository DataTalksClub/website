from django.urls import reverse

from accounts.models import CustomUser
from courses.models import Enrollment
from courses.tests.registration_campaign_base import RegistrationCampaignBase


class RegistrationCampaignCoursePageTests(RegistrationCampaignBase):
    def assert_no_registration_action(self, response):
        """Assert the page offers no way to register.

        Design 5a (issue #179) inlines the page stylesheet, whose comments mention
        "Register buttons in list rows", so the absence of the action is asserted
        against the action itself: its words and its campaign target.
        """

        self.assertNotContains(response, "Register for the cohort")
        self.assertNotContains(response, self.campaign_url())

    def test_empty_course_redirects_non_staff_to_campaign(self):
        url = self.course_url()
        response = self.client.get(url)

        redirect_url = reverse(
            "registration_campaign",
            kwargs={"campaign_slug": self.campaign.slug},
        )
        self.assertRedirects(
            response,
            redirect_url,
        )

    def test_course_with_homework_shows_workspace_and_registration_link(
        self,
    ):
        self.create_intro_homework()

        url = self.course_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Register for the cohort")
        self.assertContains(response, self.campaign_url())
        self.assertContains(response, "Intro")

    def test_course_page_hides_registration_button_when_registered(
        self,
    ):
        user = self.create_registered_course_user()
        self.create_intro_homework()
        self.client.force_login(user)

        url = self.course_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assert_no_registration_action(response)

    def test_course_page_hides_registration_button_when_enrolled(self):
        user = CustomUser.objects.create_user(
            username="enrolled",
            email="enrolled@example.com",
            password="test",
        )
        Enrollment.objects.create(student=user, course=self.course)
        self.create_intro_homework()
        self.client.force_login(user)

        url = self.course_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assert_no_registration_action(response)
