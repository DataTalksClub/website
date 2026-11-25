"""The registration gate under production's sign-in configuration.

Production closes email/password signup (``accounts.auth.AccountAdapter``) and
lets a social sign-in with no matching member create the account
(``ConsolidatingSocialAccountAdapter``).  The owner-reported dead end was
Apply -> "Create account" -> "Sign-up is closed" -> "Sign in" -> "temporarily
unavailable".  So in production the gate must never link to the closed form:
with providers it offers them inline (with ``next`` back to the campaign), and
with none it says so and gives the site's contact address.
"""

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from accounts.tests.provider_fixtures import ALL_PROVIDERS, configure_site_providers
from courses.tests.registration_campaign_base import RegistrationCampaignBase


@override_settings(REGISTRATION_REQUIRES_ACCOUNT=True, ENVIRONMENT="production")
class ProductionRegistrationGateTests(RegistrationCampaignBase):
    def setUp(self):
        super().setUp()
        cache.delete("available_providers")
        self.addCleanup(cache.delete, "available_providers")

    def test_gate_leads_to_open_signup_and_sign_in_with_next_preserved(self):
        # Email + password accounts are open to everyone, so production shows the
        # same gate as every environment: create an account or sign in, each
        # carrying `next`.  Providers are offered on the signup page, not inline.
        configure_site_providers(*ALL_PROVIDERS)
        campaign_url = self.campaign_url()

        response = self.client.get(campaign_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your free account to register")
        self.assertContains(response, f"{reverse('account_signup')}?next={campaign_url}")
        self.assertContains(response, ">Create account</a>")
        self.assertContains(response, f"{reverse('login')}?next={campaign_url}")
        self.assertNotContains(response, "Continue with Google")
        self.assertNotContains(response, 'name="accepted_newsletter"')

    def test_signup_page_names_only_the_configured_providers(self):
        configure_site_providers(("github", "GitHub"))

        response = self.client.get(f"{reverse('account_signup')}?next={self.campaign_url()}")

        self.assertContains(response, "Continue with GitHub")
        self.assertNotContains(response, "Continue with Google")

    def test_without_providers_the_gate_still_offers_email_signup(self):
        campaign_url = self.campaign_url()

        response = self.client.get(campaign_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your free account to register")
        self.assertContains(response, f"{reverse('account_signup')}?next={campaign_url}")
        self.assertNotContains(response, "Signing in isn't available right now")

    @override_settings(ENVIRONMENT="local")
    def test_open_email_signup_keeps_the_create_account_link(self):
        configure_site_providers(*ALL_PROVIDERS)
        campaign_url = self.campaign_url()

        response = self.client.get(campaign_url)

        self.assertContains(response, f"{reverse('account_signup')}?next={campaign_url}")
        self.assertContains(response, ">Create account</a>")
