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

    def test_providers_are_offered_inline_with_next_preserved(self):
        configure_site_providers(*ALL_PROVIDERS)
        campaign_url = self.campaign_url()

        response = self.client.get(campaign_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your free account to register")
        self.assertContains(response, "Create your account with Google, GitHub or Slack.")
        encoded_next = campaign_url.replace("/", "%2F")
        for provider, name in ALL_PROVIDERS:
            with self.subTest(provider=provider):
                self.assertContains(response, f"Continue with {name}")
                self.assertContains(
                    response,
                    f'action="/accounts/{provider}/login/?next={encoded_next}"',
                )
        self.assertNotContains(response, ">Create account</a>")
        self.assertNotContains(response, f"{reverse('account_signup')}?next={campaign_url}")
        self.assertNotContains(response, "Takes about twenty seconds")
        self.assertContains(response, f"{reverse('login')}?next={campaign_url}")

    def test_only_the_configured_providers_are_named(self):
        configure_site_providers(("github", "GitHub"))

        response = self.client.get(self.campaign_url())

        self.assertContains(response, "Create your account with GitHub.")
        self.assertContains(response, "Continue with GitHub")
        self.assertNotContains(response, "Continue with Google")

    def test_without_providers_the_gate_is_honest_and_gives_a_contact(self):
        campaign_url = self.campaign_url()
        with self.assertLogs("accounts.account_entry", level="WARNING") as logs:
            response = self.client.get(campaign_url)

        [record] = logs.records
        self.assertEqual(record.getMessage(), "account_entry_no_providers")
        self.assertEqual(getattr(record, "surface", None), "registration_campaign")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration needs an account")
        self.assertContains(response, "Signing in isn't available right now")
        self.assertContains(response, 'href="mailto:alexey@datatalks.club"')
        self.assertNotContains(response, "Create your free account to register")
        self.assertNotContains(response, f"{reverse('account_signup')}?next={campaign_url}")
        self.assertNotContains(response, 'name="accepted_newsletter"')

    @override_settings(ENVIRONMENT="local")
    def test_open_email_signup_keeps_the_create_account_link(self):
        configure_site_providers(*ALL_PROVIDERS)
        campaign_url = self.campaign_url()

        response = self.client.get(campaign_url)

        self.assertContains(response, f"{reverse('account_signup')}?next={campaign_url}")
        self.assertContains(response, ">Create account</a>")
