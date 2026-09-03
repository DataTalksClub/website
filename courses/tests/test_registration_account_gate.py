"""Course registration for people who have an account, and for people who don't.

`_docs/design/specs/signed-in-home.md` §8: an anonymous visitor is offered an
account instead of a seven-field form (§8.3), a signed-in member is shown what
the account already knows instead of being asked for it again (§8.2), and
registering finally hands off somewhere that knows about the registration.

The newsletter consent ruling of 2026-09-02 (§8.4, §13-Q1) is not touched by
any of this and is asserted here from the signed-in side too: required to tick,
never pre-ticked, never carried over from a previous registration.
"""

from django.test import override_settings
from django.urls import reverse

from accounts.models import CustomUser
from courses.models import CourseRegistration
from courses.tests.registration_campaign_base import RegistrationCampaignBase


@override_settings(REGISTRATION_REQUIRES_ACCOUNT=True)
class RegistrationAccountGateTests(RegistrationCampaignBase):
    def test_anonymous_visitor_is_offered_an_account_instead_of_the_form(self):
        response = self.client.get(self.campaign_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your free account to register")
        self.assertContains(response, "Create account")
        self.assertContains(response, "Sign in")
        self.assertNotContains(response, 'name="accepted_newsletter"')

    def test_the_gate_keeps_the_campaign_marketing_visible(self):
        response = self.client.get(self.campaign_url())

        self.assertContains(response, "LLM Zoomcamp")
        self.assertContains(response, "Build useful apps")

    def test_the_gate_returns_the_visitor_to_the_campaign(self):
        campaign_url = self.campaign_url()

        response = self.client.get(campaign_url)

        signup_url = reverse("account_signup")
        login_url = reverse("login")
        self.assertContains(response, f"{signup_url}?next={campaign_url}")
        self.assertContains(response, f"{login_url}?next={campaign_url}")

    def test_an_anonymous_post_is_sent_to_sign_in_and_registers_nobody(self):
        campaign_url = self.campaign_url()

        response = self.client.post(campaign_url, self.registration_payload())

        login_url = reverse("login")
        self.assertRedirects(
            response,
            f"{login_url}?next={campaign_url}",
            fetch_redirect_response=False,
        )
        self.assertEqual(CourseRegistration.objects.count(), 0)

    @override_settings(REGISTRATION_REQUIRES_ACCOUNT=False)
    def test_turning_the_gate_off_restores_the_anonymous_form(self):
        response = self.client.get(self.campaign_url())

        self.assertContains(response, "data-registration-form")
        self.assertContains(response, 'name="accepted_newsletter"')
        self.assertNotContains(response, "Create your free account to register")


@override_settings(REGISTRATION_REQUIRES_ACCOUNT=True)
class RegistrationFinalStepTests(RegistrationCampaignBase):
    def blank_profile_user(self):
        return CustomUser.objects.create_user(
            username="blank-profile",
            email="blank-profile@example.com",
            password="test",
        )

    def test_a_known_profile_is_shown_rather_than_asked_for_again(self):
        user = self.create_signed_user()
        self.client.force_login(user)

        response = self.client.get(self.campaign_url())

        self.assertContains(response, "One final step")
        self.assertContains(response, "You're registered in ten seconds")
        # The read-only identity block, filled from the profile.
        self.assertContains(response, "Signed Student")
        self.assertContains(response, "signed@example.com")
        self.assertContains(response, "Canada")
        # The stored role reads as its label, not as its stored value.
        self.assertContains(response, "Data Scientist")
        # And the same three fields stay correctable behind the fold.
        self.assertContains(response, "Edit these details")
        self.assertContains(response, "A couple of details we don't have yet", count=0)

    def test_the_email_is_never_editable_and_the_escape_hatch_stays(self):
        user = self.create_signed_user()
        self.client.force_login(user)

        response = self.client.get(self.campaign_url())

        self.assertNotContains(response, 'name="email"')
        self.assertContains(response, "to use a different email address")

    def test_only_the_missing_details_are_asked_for(self):
        user = self.blank_profile_user()
        self.client.force_login(user)

        response = self.client.get(self.campaign_url())

        self.assertContains(response, "A couple of details we don't have yet")
        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="country"')
        self.assertContains(response, 'name="role"')
        # Nothing is known, so there is nothing to fold away.
        self.assertNotContains(response, "Edit these details")

    def test_registering_answers_the_missing_details_once_and_for_all(self):
        user = self.blank_profile_user()
        self.client.force_login(user)
        payload = {
            "email": "ignored@example.com",
            "name": "Blank Profile",
            "country": "Germany",
            "role": CourseRegistration.Role.DATA_ENGINEER,
            "accepted_newsletter": "on",
        }

        response = self.client.post(self.campaign_url(), payload)

        self.assertEqual(response.status_code, 200)
        registration = CourseRegistration.objects.get()
        self.assertEqual(registration.user, user)
        user.refresh_from_db()
        self.assertEqual(user.certificate_name, "Blank Profile")
        self.assertEqual(user.country, "Germany")
        self.assertEqual(user.region, "Europe")
        self.assertEqual(user.registration_role, CourseRegistration.Role.DATA_ENGINEER)

    def test_registering_hands_the_member_off_to_their_home(self):
        user = self.create_signed_user()
        self.client.force_login(user)
        payload = {
            "email": "ignored@example.com",
            "accepted_newsletter": "on",
        }

        response = self.client.post(self.campaign_url(), payload)

        self.assertContains(response, "You are registered")
        self.assertContains(response, "Go to your home")
        self.assertContains(response, f'href="{reverse("home")}"')

    def test_the_hand_off_is_offered_to_a_member_who_is_already_registered(self):
        user = self.create_registered_course_user()
        self.client.force_login(user)

        response = self.client.get(self.campaign_url())

        self.assertContains(response, "You are already registered")
        self.assertContains(response, "Go to your home")

    def _fold_tag(self, response) -> str:
        """The rendered `<details>` opening tag of the "Edit these details" fold."""

        body = response.content.decode()
        marker = body.index("row-fold registration-fold")
        start = body.rindex("<details", 0, marker)
        return body[start : body.index(">", marker) + 1]

    def test_a_folded_field_that_fails_opens_the_fold_it_is_hiding(self):
        """The error summary may never point at a control a `<details>` hides."""

        user = self.create_signed_user()
        self.client.force_login(user)
        payload = {
            "email": "ignored@example.com",
            "name": "Signed Student",
            "country": "Nowhere",
            "role": CourseRegistration.Role.DATA_SCIENTIST,
            "accepted_newsletter": "on",
        }

        response = self.client.post(self.campaign_url(), payload)

        self.assertContains(response, "Select a valid country.")
        self.assertIn("open", self._fold_tag(response))

    def test_an_unrelated_error_leaves_the_fold_closed(self):
        user = self.create_signed_user()
        self.client.force_login(user)

        response = self.client.post(
            self.campaign_url(),
            {"email": "ignored@example.com"},
        )

        self.assertNotIn("open", self._fold_tag(response))

    def test_consent_is_still_required_and_still_arrives_unticked(self):
        user = self.create_signed_user()
        self.client.force_login(user)

        rendered = self.client.get(self.campaign_url())

        body = rendered.content.decode()
        consent = body[body.index('name="accepted_newsletter"') - 40 :][:200]
        self.assertNotIn("checked", consent.split(">")[0])

        response = self.client.post(
            self.campaign_url(),
            {"email": "ignored@example.com"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertEqual(CourseRegistration.objects.count(), 0)
