from django.test import override_settings

from courses.models import (
    CourseRegistration,
    Enrollment,
)
from courses.tests.registration_campaign_base import RegistrationCampaignBase


@override_settings(REGISTRATION_REQUIRES_ACCOUNT=False)
class RegistrationApplicationGateTests(RegistrationCampaignBase):
    """Application-style registration for Mikhail's Production AI course.

    Limited seats, medium-advanced level: name, role and experience are
    required, submissions stay pending for operator review, and nothing
    auto-enrolls the applicant into the cohort.
    """

    def setUp(self):
        super().setUp()
        self.campaign.requires_approval = True
        self.campaign.application_notice = (
            "4 weeks, about 8-10 hours per week. "
            "Prerequisites: Python, Docker basics, HTTP APIs, command line."
        )
        self.campaign.save(update_fields=["requires_approval", "application_notice"])

    def application_payload(self, **overrides):
        payload = {
            "email": "applicant@example.com",
            "name": "Applicant One",
            "country": "Germany",
            "role": CourseRegistration.Role.ML_ENGINEER,
            "experience": "3 years of ML, shipped two LLM services",
            "prereq_python": "on",
            "prereq_docker": "on",
            "prereq_http_apis": "on",
            "prereq_cli": "on",
            "linkedin_url": "https://www.linkedin.com/in/applicant-one",
            "github_url": "https://github.com/applicant-one",
            "motivation": "I want to run media models on GPUs in production",
            "comment": "Looking forward to it",
            "accepted_newsletter": "on",
        }
        payload.update(overrides)
        return payload

    def test_approval_page_shows_application_disclaimer(self):
        response = self.client.get(self.campaign_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Limited seats")
        self.assertContains(response, "medium–advanced level")
        self.assertContains(response, "acceptance is not")
        self.assertContains(response, "Prerequisites: Python")

    def test_approval_campaign_requires_role_and_experience(self):
        url = self.campaign_url()
        payload = self.application_payload()
        del payload["role"]
        del payload["experience"]

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CourseRegistration.objects.count(), 0)
        self.assertContains(response, "This field is required.")

    def test_approval_submission_stays_pending_with_new_fields(self):
        url = self.campaign_url()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, self.application_payload())

        self.assertEqual(response.status_code, 200)
        registration = CourseRegistration.objects.get()
        self.assertEqual(registration.status, CourseRegistration.Status.PENDING)
        self.assertEqual(registration.experience, "3 years of ML, shipped two LLM services")
        self.assertTrue(registration.prereq_python)
        self.assertTrue(registration.prereq_docker)
        self.assertTrue(registration.prereq_http_apis)
        self.assertTrue(registration.prereq_cli)
        self.assertEqual(registration.linkedin_url, "https://www.linkedin.com/in/applicant-one")
        self.assertEqual(registration.github_url, "https://github.com/applicant-one")
        self.assertEqual(
            registration.motivation,
            "I want to run media models on GPUs in production",
        )
        self.assertEqual(registration.region, "Europe")
        self.assertContains(response, "Application received")
        self.assertContains(response, "pending review")

    def test_approval_submission_does_not_create_enrollment(self):
        url = self.campaign_url()

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, self.application_payload())

        registration = CourseRegistration.objects.get()
        self.assertEqual(Enrollment.objects.filter(course=registration.course).count(), 0)

    def test_open_campaign_keeps_experience_optional_and_approves(self):
        self.campaign.requires_approval = False
        self.campaign.application_notice = ""
        self.campaign.save(update_fields=["requires_approval", "application_notice"])

        url = self.campaign_url()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, self.registration_payload())

        self.assertEqual(response.status_code, 200)
        registration = CourseRegistration.objects.get()
        self.assertEqual(registration.status, CourseRegistration.Status.APPROVED)
        self.assertEqual(registration.experience, "")
        self.assertContains(response, "You are registered")
        self.assertNotContains(response, "Limited seats")
