"""Registration-scoped email verification: a pragmatic slice of #243.

The full member email-verification design (#243) is blocked on a
``MemberProfile`` model (#248) and the durable ``EmailDelivery``/Relay job
pipeline (#49). Neither is needed to ask a course registrant to verify their
account's email: this slice reuses allauth's own ``EmailAddress`` model and
confirm-email route (already wired at ``accounts/confirm-email/<key>/``) and
folds the ask into the one confirmation email
``course_management.package_mail.send_registration_confirmation_mail``
already sends, rather than a second send or new machinery.

Covers, per `_docs/design/specs/signed-in-home.md` §8.2's "One final step"
neighbour, the "You are registered" screen
(`courses/templates/courses/_registration_done.html`): the confirmation
email queues a verify prompt exactly when the registering account's email
is not yet verified, the screen shows the reminder while unverified, and
both drop it once the account's ``EmailAddress`` is marked verified —
including through the real emailed link.
"""

import re

from allauth.account.models import EmailAddress
from community_base.mail.models import EmailDelivery
from django.test import override_settings
from django.urls import reverse

from course_management.package_mail import send_registration_confirmation_mail
from courses.models import CourseRegistration
from courses.tests.registration_campaign_base import RegistrationCampaignBase

CONFIRM_LINK_PATTERN = re.compile(r"/accounts/confirm-email/([-:\w]+)/")


@override_settings(REGISTRATION_REQUIRES_ACCOUNT=True)
class RegistrationConfirmationVerifyEmailMailTests(RegistrationCampaignBase):
    def test_registering_with_an_unverified_account_queues_a_verify_prompt(self):
        user = self.create_signed_user()
        self.client.force_login(user)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.campaign_url(), self.blank_optional_logged_in_payload())

        delivery = EmailDelivery.objects.get()
        prompt = delivery.context_data["verify_email_prompt"]
        self.assertIn("One last step: verify your email", prompt)
        self.assertIn("so we can email you when the course starts", prompt)
        match = CONFIRM_LINK_PATTERN.search(prompt)
        self.assertIsNotNone(match, prompt)
        address = EmailAddress.objects.get(user=user, email__iexact=user.email)
        self.assertFalse(address.verified)

    def test_registering_with_an_already_verified_account_queues_nothing(self):
        user = self.create_signed_user()
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        self.client.force_login(user)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.campaign_url(), self.blank_optional_logged_in_payload())

        delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.context_data["verify_email_prompt"], "")

    def test_a_registration_with_no_account_has_no_account_to_verify(self):
        # `CourseRegistration.user` is nullable (the un-gated anonymous path,
        # `REGISTRATION_REQUIRES_ACCOUNT=False`, still writes rows with no
        # user). Built directly here rather than through that path, since
        # this class needs the gate on for its other cases.
        registration = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.course,
            email="anonymous@example.com",
            name="Anonymous Student",
            accepted_newsletter=True,
        )
        self.assertIsNone(registration.user_id)

        with self.captureOnCommitCallbacks(execute=True):
            send_registration_confirmation_mail(registration)

        delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.context_data["verify_email_prompt"], "")


@override_settings(REGISTRATION_REQUIRES_ACCOUNT=True)
class RegistrationConfirmationVerifyEmailScreenTests(RegistrationCampaignBase):
    def test_the_screen_shows_the_reminder_for_an_unverified_registrant(self):
        user = self.create_signed_user()
        self.client.force_login(user)

        response = self.client.post(self.campaign_url(), self.blank_optional_logged_in_payload())

        self.assertContains(response, "You are registered")
        self.assertContains(response, "One last step: verify your email")
        self.assertContains(response, "Check your inbox")

    def test_the_screen_hides_the_reminder_for_an_already_verified_registrant(self):
        user = self.create_signed_user()
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        self.client.force_login(user)

        response = self.client.post(self.campaign_url(), self.blank_optional_logged_in_payload())

        self.assertContains(response, "You are registered")
        self.assertNotContains(response, "One last step: verify your email")

    def test_the_screen_always_offers_the_generic_next_steps(self):
        user = self.create_signed_user()
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        self.client.force_login(user)

        response = self.client.post(self.campaign_url(), self.blank_optional_logged_in_payload())

        self.assertContains(response, "Join the DataTalks.Club Slack")
        self.assertContains(response, "Tweet about it")
        self.assertContains(response, "Subscribe on YouTube")

    def test_revisiting_while_still_registered_reflects_current_verification(self):
        user = self.create_registered_course_user()
        self.client.force_login(user)

        response = self.client.get(self.campaign_url())

        self.assertContains(response, "You are already registered")
        self.assertContains(response, "One last step: verify your email")

        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

        response = self.client.get(self.campaign_url())

        self.assertNotContains(response, "One last step: verify your email")

    def test_clicking_the_emailed_link_verifies_and_the_screen_acknowledges_it(self):
        user = self.create_signed_user()
        self.client.force_login(user)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.campaign_url(), self.blank_optional_logged_in_payload())
        delivery = EmailDelivery.objects.get()
        match = CONFIRM_LINK_PATTERN.search(delivery.context_data["verify_email_prompt"])
        assert match is not None
        key = match.group(1)

        confirm_response = self.client.post(reverse("account_confirm_email", args=[key]))
        self.assertIn(confirm_response.status_code, (200, 302))

        address = EmailAddress.objects.get(user=user, email__iexact=user.email)
        self.assertTrue(address.verified)

        response = self.client.get(self.campaign_url())
        self.assertContains(response, "You are already registered")
        self.assertNotContains(response, "One last step: verify your email")
