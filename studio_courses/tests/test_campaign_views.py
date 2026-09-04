from django.urls import reverse

from courses.models import Cohort, CourseRegistration, RegistrationCampaign
from studio_courses.tests.campaign_view_base import (
    CampaignStudioCoursesViewBase,
    admin_credentials,
    credentials,
)


class CampaignStudioCoursesViewTests(CampaignStudioCoursesViewBase):
    def test_campaign_registrations_staff_allowed(self):
        campaign = RegistrationCampaign.objects.create(
            slug="llm-zoomcamp",
            title="LLM Zoomcamp",
            current_course=self.course,
        )
        CourseRegistration.objects.create(
            campaign=campaign,
            course=self.course,
            email="student@example.com",
            name="Student One",
            company_name="Acme Data",
            country="Germany",
            region="Europe",
            role=CourseRegistration.Role.DATA_ENGINEER,
            accepted_newsletter=True,
        )

        self.client.login(**admin_credentials)
        url = reverse(
            "studio_courses_campaign_registrations",
            kwargs={"campaign_slug": campaign.slug},
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LLM Zoomcamp")
        self.assertContains(response, "student@example.com")
        self.assertContains(response, "Acme Data")
        self.assertContains(response, "Europe")

    def test_campaign_create_staff_allowed(self):
        self.client.login(**admin_credentials)
        url = reverse("studio_courses_campaign_create")
        response = self.client.get(f"{url}?course={self.course.slug}")

        self.assert_campaign_create_page(response)

        payload = self.campaign_create_payload()
        response = self.client.post(url, payload)

        campaign = RegistrationCampaign.objects.get(slug="llm-zoomcamp")
        redirect_url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )
        self.assertRedirects(response, redirect_url)
        self.assert_created_campaign_saved(campaign)

    def test_campaign_create_non_staff_denied(self):
        self.client.login(**credentials)
        url = reverse("studio_courses_campaign_create")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        campaign_exists = RegistrationCampaign.objects.exists()
        self.assertFalse(campaign_exists)

    def test_campaign_edit_staff_allowed(self):
        campaign = self.create_llm_registration_campaign(
            marketing_markdown="Old copy",
        )
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )

        self.client.login(**admin_credentials)
        response = self.client.get(url)

        self.assert_campaign_edit_page(response)

        payload = self.campaign_edit_payload()
        response = self.client.post(url, payload)

        self.assertRedirects(response, url)
        self.assert_campaign_updated(campaign)

    def test_campaign_edit_shows_datamailer_campaign_controls(self):
        campaign = self.create_llm_registration_campaign()
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )

        self.client.login(**admin_credentials)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Datamailer campaign")
        self.assertContains(response, "cmp-registration-llm-zoomcamp")
        self.assertContains(response, self.course.slug)
        self.assertContains(response, "Sync draft")
        self.assertContains(response, "Test send")

    def test_campaign_edit_shows_stop_registration_when_open(self):
        campaign = self.create_llm_registration_campaign()
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )

        self.client.login(**admin_credentials)
        response = self.client.get(url)

        self.assertContains(response, "Stop registration")
        self.assertContains(response, self.course.title)
        self.assertNotContains(response, "Open new cohort")

    def test_campaign_edit_shows_open_new_cohort_when_none(self):
        campaign = self.create_llm_registration_campaign(current_course=None)
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )

        self.client.login(**admin_credentials)
        response = self.client.get(url)

        self.assertContains(response, "Open new cohort")
        self.assertContains(response, "No cohort is currently open")
        self.assertNotContains(response, "Stop registration")

    def test_stop_registration_closes_the_current_cohort(self):
        campaign = self.create_llm_registration_campaign()
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )

        self.client.login(**admin_credentials)
        response = self.client.post(
            url, {"campaign_action": "stop_registration"}, follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration stopped")
        campaign.refresh_from_db()
        self.assertIsNone(campaign.current_course)
        self.assertContains(response, "Open new cohort")

    def test_stop_registration_fails_closed_when_already_stopped(self):
        campaign = self.create_llm_registration_campaign(current_course=None)
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )

        self.client.login(**admin_credentials)
        response = self.client.post(
            url, {"campaign_action": "stop_registration"}, follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no open cohort")
        campaign.refresh_from_db()
        self.assertIsNone(campaign.current_course)

    def test_open_new_cohort_opens_registration(self):
        campaign = self.create_llm_registration_campaign(current_course=None)
        next_course = Cohort.objects.create(
            slug="test-course-2027",
            title="Test Course 2027",
            description="Next edition",
        )
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )

        self.client.login(**admin_credentials)
        response = self.client.post(
            url,
            {"campaign_action": "open_new_cohort", "cohort": next_course.pk},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration opened")
        campaign.refresh_from_db()
        self.assertEqual(campaign.current_course, next_course)
        self.assertContains(response, "Stop registration")

    def test_open_new_cohort_guard_rejects_a_still_open_campaign(self):
        campaign = self.create_llm_registration_campaign()
        next_course = Cohort.objects.create(
            slug="test-course-2027",
            title="Test Course 2027",
            description="Next edition",
        )
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )

        self.client.login(**admin_credentials)
        response = self.client.post(
            url,
            {"campaign_action": "open_new_cohort", "cohort": next_course.pk},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stop registration for the current cohort")
        campaign.refresh_from_db()
        self.assertEqual(campaign.current_course, self.course)

    def test_general_save_cannot_change_current_course(self):
        campaign = self.create_llm_registration_campaign()
        other_course = Cohort.objects.create(
            slug="test-course-2027",
            title="Test Course 2027",
            description="Next edition",
        )
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )
        payload = self.campaign_edit_payload()
        payload["current_course"] = other_course.pk

        self.client.login(**admin_credentials)
        response = self.client.post(url, payload)

        self.assertRedirects(response, url)
        campaign.refresh_from_db()
        self.assertEqual(campaign.current_course, self.course)

    def test_lifecycle_actions_denied_for_non_staff(self):
        campaign = self.create_llm_registration_campaign()
        url = reverse(
            "studio_courses_campaign_edit",
            kwargs={"campaign_slug": campaign.slug},
        )

        self.client.login(**credentials)
        response = self.client.post(url, {"campaign_action": "stop_registration"})

        self.assertEqual(response.status_code, 302)
        campaign.refresh_from_db()
        self.assertEqual(campaign.current_course, self.course)
