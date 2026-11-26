"""Course and community emails on the signup onboarding step (owner rule 2026-11-26).

Pre-ticked box; receiving starts only after a confirmed click.  A social
signup gets one ``newsletter-confirm`` email and stays pending; an email +
password signup already confirmed through its account verification link;
unticked sends nothing.  Account settings shows the state and unsubscribes.
"""

from urllib.parse import parse_qs, urlsplit

from allauth.account.models import EmailAddress
from allauth.account.signals import user_signed_up
from allauth.socialaccount.models import SocialAccount
from community_base.mail.models import EmailDelivery
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from accounts.services.newsletter_consent import (
    SIGNUP_ONBOARDING_SOURCE,
    STATE_ACTIVE,
    STATE_OFF,
    STATE_PENDING,
    newsletter_state,
)
from courses.models import CourseRegistration

PROFILE = {
    "certificate_name": "Nia Newcomer",
    "country": "Germany",
    "registration_role": CourseRegistration.Role.DATA_ANALYST,
}


@override_settings(SIGNUP_NEWSLETTER_CONSENT_VERSION="test-v1")
class SignupNewsletterConsentTests(TestCase):
    def social_user(self):
        user = User.objects.create_user(username="social-new", email="social@example.com")
        user.set_unusable_password()
        user.newsletter_subscribed = False
        user.save()
        SocialAccount.objects.create(user=user, provider="google", uid="g-1")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        return user

    def email_user(self):
        user = User.objects.create_user(
            username="email-new", email="email@example.com", password="pw-123456789"
        )
        user.newsletter_subscribed = False
        user.save()
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        return user

    def post_onboarding(self, user, **extra):
        self.client.force_login(user)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("account_welcome"),
                {"next": "/courses/", "onboarding": "1", **PROFILE, **extra},
            )

    def confirm_deliveries(self):
        return EmailDelivery.objects.filter(template_key="newsletter-confirm")

    def test_the_box_is_ticked_by_default_on_the_signup_step(self):
        self.client.force_login(self.social_user())

        page = self.client.get(reverse("account_welcome"), {"onboarding": "1", "next": "/"})

        self.assertContains(page, "data-signup-newsletter")
        self.assertTrue(page.context["form"]["newsletter"].value())
        self.assertContains(page, "unsubscribe anytime")

    def test_the_box_is_not_on_the_registration_gate(self):
        self.client.force_login(self.social_user())

        page = self.client.get(reverse("account_welcome"), {"next": "/courses/register/x"})

        self.assertNotContains(page, "data-signup-newsletter")

    def test_a_social_signup_stays_pending_until_the_link_is_clicked(self):
        user = self.social_user()

        response = self.post_onboarding(user, newsletter="on")

        self.assertRedirects(response, "/courses/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertEqual(newsletter_state(user), STATE_PENDING)
        self.assertFalse(user.newsletter_subscribed)
        self.assertIsNone(user.newsletter_consented_at)
        delivery = self.confirm_deliveries().get()
        confirm_url = delivery.context_data["confirm_url"]

        page = self.client.get(
            urlsplit(confirm_url).path, {"token": parse_qs(urlsplit(confirm_url).query)["token"][0]}
        )

        self.assertContains(page, "You're subscribed")
        user.refresh_from_db()
        self.assertEqual(newsletter_state(user), STATE_ACTIVE)
        self.assertEqual(user.newsletter_consent_source, SIGNUP_ONBOARDING_SOURCE)
        self.assertEqual(user.newsletter_consent_version, "test-v1")
        self.assertIsNotNone(user.newsletter_consented_at)

    def test_an_email_signup_is_confirmed_by_its_verification_click(self):
        user = self.email_user()

        self.post_onboarding(user, newsletter="on")

        user.refresh_from_db()
        self.assertEqual(newsletter_state(user), STATE_ACTIVE)
        self.assertEqual(user.newsletter_consent_source, SIGNUP_ONBOARDING_SOURCE)
        self.assertFalse(self.confirm_deliveries().exists())

    def test_unticked_means_no_subscription_and_no_email(self):
        user = self.social_user()

        self.post_onboarding(user)

        user.refresh_from_db()
        self.assertEqual(newsletter_state(user), STATE_OFF)
        self.assertFalse(self.confirm_deliveries().exists())

    def test_a_bad_link_activates_nothing(self):
        user = self.social_user()
        self.post_onboarding(user, newsletter="on")

        page = self.client.get(reverse("newsletter_confirm"), {"token": "tampered"})

        self.assertEqual(page.status_code, 400)
        user.refresh_from_db()
        self.assertEqual(newsletter_state(user), STATE_PENDING)

    def test_a_new_signup_starts_unsubscribed(self):
        user = User.objects.create_user(username="fresh", email="fresh@example.com")
        self.assertTrue(user.newsletter_subscribed)

        user_signed_up.send(sender=User, request=None, user=user)

        user.refresh_from_db()
        self.assertFalse(user.newsletter_subscribed)


class AccountSettingsNewsletterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="settings-n", email="settings-n@example.com", password="pw-123456789"
        )
        self.client.force_login(self.user)

    def test_the_settings_page_shows_a_pending_subscription(self):
        from django.utils import timezone

        self.user.newsletter_subscribed = False
        self.user.newsletter_confirmation_sent_at = timezone.now()
        self.user.save()

        page = self.client.get(reverse("account_settings"))

        self.assertContains(page, 'data-newsletter-state="pending"')
        self.assertContains(page, "check your inbox to confirm")

    def test_unticking_in_settings_unsubscribes(self):
        self.assertTrue(self.user.newsletter_subscribed)
        page = self.client.get(reverse("account_settings"))
        self.assertContains(page, 'data-newsletter-state="active"')

        self.client.post(reverse("account_settings"), {"preferred_timezone": ""})

        self.user.refresh_from_db()
        self.assertFalse(self.user.newsletter_subscribed)
