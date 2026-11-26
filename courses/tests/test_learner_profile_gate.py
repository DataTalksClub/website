"""The learner-profile gate in front of course registration.

No profile -> profile form -> registration; a stale profile -> the "still up
to date?" review; a fresh profile -> straight to registration; the 180-day
boundary; ``next=`` safety; the cohort-enrollment gate; post-signup
onboarding; immediate confirmation for a verified account email; and the
per-course questions (none for an open course, the application for MediaGen).
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from allauth.account.models import EmailAddress
from community_base.mail.models import EmailDelivery
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.auth import AccountAdapter
from accounts.models import User
from courses.genmedia_policy import CAMPAIGN_SLUG as GENMEDIA_SLUG
from courses.models import Cohort, CourseRegistration, RegistrationCampaign
from courses.models.learner_profile import LearnerProfile
from courses.services.learner_profile_gate import (
    GATE_MISSING,
    GATE_OK,
    GATE_STALE,
    profile_gate_state,
)

COMPLETE_PROFILE = {
    "certificate_name": "Ada Learner",
    "country": "Germany",
    "region": "Europe",
    "registration_role": CourseRegistration.Role.DATA_ENGINEER,
    "company_name": "Acme Data",
}


def _query(url: str) -> dict:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


@override_settings(
    REGISTRATION_REQUIRES_ACCOUNT=True, LEARNER_PROFILE_CONFIRMATION_MAX_AGE_DAYS=180
)
class LearnerProfileGateTestBase(TestCase):
    def setUp(self):
        mx = patch(
            "courses.email_validation._mx_records_for_domain",
            return_value=[SimpleNamespace(exchange="mx.example.net.", preference=10)],
        )
        mx.start()
        self.addCleanup(mx.stop)
        self.cohort = Cohort.objects.create(
            slug="gate-zoomcamp-2026", title="Gate Zoomcamp 2026", description="x"
        )
        self.campaign = RegistrationCampaign.objects.create(
            slug="gate-zoomcamp",
            title="Gate Zoomcamp",
            current_course=self.cohort,
        )
        self.user = User.objects.create_user(
            username="gate-learner", email="gate-learner@example.com", password="pw-123456789"
        )
        self.client.force_login(self.user)

    def campaign_url(self, campaign=None):
        campaign = campaign or self.campaign
        return reverse("registration_campaign", kwargs={"campaign_slug": campaign.slug})

    def give_profile(self, confirmed_days_ago=1, **overrides):
        values = {**COMPLETE_PROFILE, **overrides}
        confirmed_at = (
            None
            if confirmed_days_ago is None
            else timezone.now() - timedelta(days=confirmed_days_ago)
        )
        return LearnerProfile.objects.update_or_create(
            user=self.user, defaults={**values, "profile_confirmed_at": confirmed_at}
        )[0]


class GateRedirectTests(LearnerProfileGateTestBase):
    def test_no_profile_goes_to_the_profile_form_then_back_to_registration(self):
        response = self.client.get(self.campaign_url())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response["Location"]).path, reverse("account_welcome"))
        self.assertEqual(_query(response["Location"]), {"next": self.campaign_url()})

        form_page = self.client.get(response["Location"])
        self.assertContains(form_page, f'name="next" value="{self.campaign_url()}"')
        self.assertNotContains(form_page, "Skip for now")

        saved = self.client.post(
            reverse("account_welcome"),
            {
                "next": self.campaign_url(),
                "certificate_name": "Ada Learner",
                "country": "Germany",
                "registration_role": CourseRegistration.Role.DATA_ENGINEER,
                "company_name": "Acme Data",
                "years_experience": "3_6",
            },
        )
        self.assertRedirects(saved, self.campaign_url(), fetch_redirect_response=False)
        profile = LearnerProfile.objects.get(user=self.user)
        self.assertEqual(profile.company_name, "Acme Data")
        self.assertEqual(profile.years_experience, "3_6")
        self.assertEqual(profile.region, "Europe")
        self.assertIsNotNone(profile.profile_confirmed_at)

        registration_page = self.client.get(self.campaign_url())
        self.assertEqual(registration_page.status_code, 200)
        self.assertContains(registration_page, "One final step")
        self.assertContains(registration_page, "Acme Data")

    def test_a_profile_missing_a_required_field_goes_to_the_form(self):
        self.give_profile(country="")

        response = self.client.get(self.campaign_url())

        self.assertEqual(urlsplit(response["Location"]).path, reverse("account_welcome"))

    def test_the_form_refuses_to_save_without_the_required_fields(self):
        response = self.client.post(
            reverse("account_welcome"), {"next": self.campaign_url(), "certificate_name": "A"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(LearnerProfile.objects.filter(user=self.user).exists())

    def test_a_stale_profile_goes_to_the_review_step(self):
        self.give_profile(confirmed_days_ago=200)

        response = self.client.get(self.campaign_url())

        self.assertEqual(urlsplit(response["Location"]).path, reverse("account_profile_review"))
        review = self.client.get(response["Location"])
        self.assertContains(review, "Is this still up to date?")
        self.assertContains(review, "Ada Learner")
        self.assertContains(review, "Acme Data")
        self.assertContains(review, "Yes, still correct")
        self.assertContains(review, "data-profile-update")

    def test_a_never_confirmed_profile_goes_to_the_review_step(self):
        self.give_profile(confirmed_days_ago=None)

        response = self.client.get(self.campaign_url())

        self.assertEqual(urlsplit(response["Location"]).path, reverse("account_profile_review"))

    def test_yes_still_correct_records_the_date_and_continues(self):
        profile = self.give_profile(confirmed_days_ago=200)
        before = timezone.now()

        response = self.client.post(
            reverse("account_profile_review"), {"next": self.campaign_url()}
        )

        self.assertRedirects(response, self.campaign_url(), fetch_redirect_response=False)
        profile.refresh_from_db()
        self.assertGreaterEqual(profile.profile_confirmed_at, before)
        self.assertEqual(self.client.get(self.campaign_url()).status_code, 200)

    def test_update_on_the_review_step_is_the_profile_form_with_next(self):
        self.give_profile(confirmed_days_ago=200)
        review = self.client.get(reverse("account_profile_review"), {"next": self.campaign_url()})

        update_url = review.context["update_url"]
        self.assertEqual(urlsplit(update_url).path, reverse("account_welcome"))
        self.assertEqual(_query(update_url), {"next": self.campaign_url()})

    def test_a_fresh_profile_goes_straight_to_registration(self):
        self.give_profile(confirmed_days_ago=10)

        response = self.client.get(self.campaign_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "One final step")

    def test_an_existing_registration_is_never_gated(self):
        CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.cohort,
            user=self.user,
            email=self.user.email,
            name="Ada",
            accepted_newsletter=True,
        )

        self.assertEqual(self.client.get(self.campaign_url()).status_code, 200)

    def test_a_registration_post_is_gated_too(self):
        response = self.client.post(self.campaign_url(), {"accepted_newsletter": "on"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CourseRegistration.objects.exists())


class ConfirmationBoundaryTests(LearnerProfileGateTestBase):
    def test_exactly_180_days_is_still_current(self):
        now = timezone.now()
        LearnerProfile.objects.create(
            user=self.user, **COMPLETE_PROFILE, profile_confirmed_at=now - timedelta(days=180)
        )

        self.assertEqual(profile_gate_state(self.user, now=now), GATE_OK)

    def test_one_second_past_180_days_is_stale(self):
        now = timezone.now()
        LearnerProfile.objects.create(
            user=self.user,
            **COMPLETE_PROFILE,
            profile_confirmed_at=now - timedelta(days=180, seconds=1),
        )

        self.assertEqual(profile_gate_state(self.user, now=now), GATE_STALE)

    @override_settings(LEARNER_PROFILE_CONFIRMATION_MAX_AGE_DAYS=30)
    def test_the_max_age_is_a_setting(self):
        now = timezone.now()
        LearnerProfile.objects.create(
            user=self.user, **COMPLETE_PROFILE, profile_confirmed_at=now - timedelta(days=31)
        )

        self.assertEqual(profile_gate_state(self.user, now=now), GATE_STALE)

    def test_no_profile_is_missing(self):
        self.assertEqual(profile_gate_state(self.user), GATE_MISSING)


class NextUrlSafetyTests(LearnerProfileGateTestBase):
    def test_the_profile_form_never_redirects_off_site(self):
        for hostile in ("https://evil.example/", "//evil.example/x", "javascript:alert(1)"):
            with self.subTest(hostile=hostile):
                response = self.client.post(
                    reverse("account_welcome"),
                    {
                        "next": hostile,
                        **{k: v for k, v in COMPLETE_PROFILE.items() if k != "region"},
                    },
                )
                self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_the_review_step_never_redirects_off_site(self):
        self.give_profile(confirmed_days_ago=200)
        for hostile in ("https://evil.example/", "//evil.example/x"):
            with self.subTest(hostile=hostile):
                response = self.client.post(reverse("account_profile_review"), {"next": hostile})
                self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_a_hostile_next_is_not_echoed_into_the_form(self):
        response = self.client.get(reverse("account_welcome"), {"next": "https://evil.example/"})

        self.assertNotContains(response, "evil.example")


class EnrollmentGateTests(LearnerProfileGateTestBase):
    def enrollment_url(self):
        return reverse(
            "cohort_enrollment",
            kwargs={
                "course_slug": self.cohort.course.slug,
                "cohort_identifier": self.cohort.identifier,
            },
        )

    def test_enrolling_without_a_profile_goes_to_the_profile_form(self):
        response = self.client.get(self.enrollment_url())

        self.assertEqual(urlsplit(response["Location"]).path, reverse("account_welcome"))
        self.assertEqual(_query(response["Location"]), {"next": self.enrollment_url()})

    def test_enrolling_with_a_fresh_profile_shows_the_enrollment_form(self):
        self.give_profile()

        self.assertEqual(self.client.get(self.enrollment_url()).status_code, 200)


class PostSignupOnboardingTests(TestCase):
    def _request(self, path="/accounts/signup/"):
        request = RequestFactory().get(path)
        SessionMiddleware(HttpResponse).process_request(request)
        MessageMiddleware(HttpResponse).process_request(request)
        request.session.save()
        return request

    def test_a_new_account_lands_on_the_profile_step_with_next(self):
        user = User.objects.create_user(username="new-one", email="new@example.com")
        request = self._request()
        request.user = user

        response = AccountAdapter(request).post_login(
            request,
            user,
            email_verification="none",
            signal_kwargs=None,
            email=None,
            signup=True,
            redirect_url="/courses/register/gate-zoomcamp",
        )

        self.assertEqual(urlsplit(response["Location"]).path, reverse("account_welcome"))
        self.assertEqual(
            _query(response["Location"]),
            {"next": "/courses/register/gate-zoomcamp", "onboarding": "1"},
        )

    def test_an_ordinary_sign_in_is_not_redirected(self):
        user = User.objects.create_user(username="old-one", email="old@example.com")
        request = self._request()
        request.user = user

        response = AccountAdapter(request).post_login(
            request,
            user,
            email_verification="none",
            signal_kwargs=None,
            email=None,
            signup=False,
            redirect_url="/courses/",
        )

        self.assertEqual(response["Location"], "/courses/")

    def test_the_onboarding_step_can_be_skipped_back_to_next(self):
        user = User.objects.create_user(username="skip-one", email="skip@example.com")
        self.client.force_login(user)

        page = self.client.get(reverse("account_welcome"), {"next": "/events/", "onboarding": "1"})

        self.assertContains(page, "Skip for now")
        self.assertEqual(page.context["skip_url"], "/events/")


class VerifiedEmailRegistrationTests(LearnerProfileGateTestBase):
    def test_a_verified_account_email_confirms_the_registration_at_once(self):
        self.give_profile()
        EmailAddress.objects.create(
            user=self.user, email=self.user.email, verified=True, primary=True
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.campaign_url(), {"accepted_newsletter": "on"})

        registration = CourseRegistration.objects.get()
        self.assertTrue(registration.email_confirmed)
        self.assertEqual(registration.email_confirmation_token or "", "")
        self.assertFalse(EmailDelivery.objects.exists())
        self.assertNotContains(response, "Check your inbox to finish registering")
        self.assertNotContains(response, "One last step: verify your email")
        self.assertContains(response, "You are registered")

    def test_an_unverified_account_email_keeps_the_double_opt_in(self):
        self.give_profile()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.campaign_url(), {"accepted_newsletter": "on"})

        registration = CourseRegistration.objects.get()
        self.assertFalse(registration.email_confirmed)
        self.assertTrue(registration.email_confirmation_token)
        self.assertEqual(EmailDelivery.objects.count(), 1)
        self.assertContains(response, "Check your inbox to finish registering")


class RegistrationQuestionTests(LearnerProfileGateTestBase):
    def test_a_course_with_no_questions_skips_the_questions_block(self):
        self.give_profile()

        response = self.client.get(self.campaign_url())

        self.assertNotContains(response, "data-registration-questions")
        self.assertNotContains(response, 'name="experience"')
        self.assertNotContains(response, 'name="company_name"')

    def test_the_profile_is_copied_onto_the_registration(self):
        self.give_profile(years_experience="1_3", linkedin_url="https://linkedin.com/in/ada")
        EmailAddress.objects.create(
            user=self.user, email=self.user.email, verified=True, primary=True
        )

        self.client.post(self.campaign_url(), {"accepted_newsletter": "on"})

        registration = CourseRegistration.objects.get()
        self.assertEqual(registration.name, "Ada Learner")
        self.assertEqual(registration.country, "Germany")
        self.assertEqual(registration.region, "Europe")
        self.assertEqual(registration.role, CourseRegistration.Role.DATA_ENGINEER)
        self.assertEqual(registration.company_name, "Acme Data")
        self.assertEqual(registration.years_experience, "1_3")
        self.assertEqual(registration.linkedin_url, "https://linkedin.com/in/ada")

    def test_configured_questions_are_asked_and_stored(self):
        self.campaign.registration_questions = ["comment"]
        self.campaign.save()
        self.give_profile()

        page = self.client.get(self.campaign_url())
        self.assertContains(page, "data-registration-questions")
        self.assertContains(page, 'name="comment"')
        self.assertNotContains(page, 'name="experience"')

        self.client.post(
            self.campaign_url(), {"accepted_newsletter": "on", "comment": "See you there"}
        )
        self.assertEqual(CourseRegistration.objects.get().comment, "See you there")

    def test_mediagen_asks_its_application_and_lands_in_pending_review(self):
        cohort = Cohort.objects.create(
            slug="production-ai-agents-generative-media-2026",
            title="Production AI Agents for Generative Media",
            description="x",
        )
        mediagen = RegistrationCampaign.objects.create(
            slug=GENMEDIA_SLUG,
            title="Production AI",
            current_course=cohort,
            requires_approval=True,
        )
        self.give_profile()
        EmailAddress.objects.create(
            user=self.user, email=self.user.email, verified=True, primary=True
        )

        page = self.client.get(self.campaign_url(mediagen))
        self.assertContains(page, "data-registration-questions")
        for name in ("experience", "motivation", "prereq_python", "accepted_nebius_sharing"):
            self.assertContains(page, f'name="{name}"')

        missing = self.client.post(self.campaign_url(mediagen), {"accepted_newsletter": "on"})
        self.assertEqual(missing.status_code, 200)
        self.assertFalse(CourseRegistration.objects.exists())

        response = self.client.post(
            self.campaign_url(mediagen),
            {
                "accepted_newsletter": "on",
                "accepted_nebius_sharing": "on",
                "experience": "5 years of backend work",
                "motivation": "Ship media agents",
                "prereq_python": "on",
            },
        )

        registration = CourseRegistration.objects.get()
        self.assertEqual(registration.status, CourseRegistration.Status.PENDING)
        self.assertEqual(registration.experience, "5 years of backend work")
        self.assertTrue(registration.prereq_python)
        self.assertEqual(registration.company_name, "Acme Data")
        self.assertContains(response, "Application received")
