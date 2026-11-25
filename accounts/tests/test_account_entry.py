"""No account entrance dead-ends, whatever the sign-in configuration.

Email + password signup is open everywhere (``accounts.auth.AccountAdapter``),
next to the Google/GitHub/Slack buttons.  The sign-in and signup pages offer
the configured providers with the visitor's ``next``, and the password form is
always there; with no provider configured the misconfiguration stays loud for
operators (system check ``accounts.W001`` and the
``account_entry_no_providers`` log event).

The course registration gate's side is
``courses/tests/test_registration_gate_production.py``.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.account_entry import NO_PROVIDERS_LOG_EVENT, provider_names_phrase
from accounts.checks import NO_PROVIDERS_CHECK_ID, check_production_sign_in_providers
from accounts.models import User
from accounts.tests.provider_fixtures import ALL_PROVIDERS, configure_site_providers
from accounts.views.login import get_available_providers

FRESH_EMAIL = "never-registered-before@example.invalid"


class ProviderCacheResetTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.delete("available_providers")
        self.addCleanup(cache.delete, "available_providers")


@override_settings(ENVIRONMENT="production", DEVELOPMENT_OWNER_LOGIN_ENABLED=False)
class ProductionLoginPageTests(ProviderCacheResetTestCase):
    def test_providers_and_the_password_form_render_with_next(self) -> None:
        configure_site_providers(*ALL_PROVIDERS)

        response = self.client.get("/accounts/login/?next=/courses/")

        self.assertEqual(response.status_code, 200)
        for provider, name in ALL_PROVIDERS:
            with self.subTest(provider=provider):
                self.assertContains(response, f"Continue with {name}")
                self.assertContains(response, f"/accounts/{provider}/login/?next=%2Fcourses%2F")
        self.assertContains(response, 'name="login"')
        self.assertContains(response, 'href="/accounts/signup/?next=%2Fcourses%2F"')

    def test_no_providers_still_offers_the_password_form_and_logs(self) -> None:
        with self.assertLogs("accounts.account_entry", level="WARNING") as logs:
            response = self.client.get("/accounts/login/?next=/courses/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="login"')
        [record] = logs.records
        self.assertEqual(record.getMessage(), NO_PROVIDERS_LOG_EVENT)
        self.assertEqual(getattr(record, "surface", None), "login")
        self.assertEqual(getattr(record, "environment", None), "production")

    def test_configured_providers_do_not_log(self) -> None:
        configure_site_providers(*ALL_PROVIDERS)

        with self.assertNoLogs("accounts.account_entry", level="WARNING"):
            self.client.get("/accounts/login/")


@override_settings(ENVIRONMENT="production")
class ProductionSignupPageTests(ProviderCacheResetTestCase):
    def test_signup_offers_the_providers_with_next_and_the_password_form(self) -> None:
        configure_site_providers(*ALL_PROVIDERS)

        response = self.client.get("/accounts/signup/?next=/courses/llm-zoomcamp/register")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup.html")
        for provider, name in ALL_PROVIDERS:
            with self.subTest(provider=provider):
                self.assertContains(response, f"Continue with {name}")
                self.assertContains(
                    response,
                    f"/accounts/{provider}/login/?next=%2Fcourses%2Fllm-zoomcamp%2Fregister",
                )
        self.assertContains(response, 'name="password1"')

    def test_signup_with_providers_does_not_log(self) -> None:
        configure_site_providers(*ALL_PROVIDERS)

        with self.assertNoLogs("accounts.account_entry", level="WARNING"):
            self.client.get("/accounts/signup/")

    def test_email_signup_creates_an_unverified_account(self) -> None:
        configure_site_providers(*ALL_PROVIDERS)

        self.client.post(
            "/accounts/signup/",
            {
                "email": FRESH_EMAIL,
                "password1": "a-sufficiently-long-password-1",
                "password2": "a-sufficiently-long-password-1",
            },
        )

        self.assertTrue(User.objects.filter(email=FRESH_EMAIL).exists())
        self.assertNotIn("_auth_user_id", self.client.session)


class ProductionProviderSystemCheckTests(TestCase):
    databases = {"default"}

    @override_settings(ENVIRONMENT="production")
    def test_production_without_providers_warns(self) -> None:
        messages = check_production_sign_in_providers(None, databases=["default"])

        self.assertEqual([message.id for message in messages], [NO_PROVIDERS_CHECK_ID])
        self.assertIn("nobody can sign in or create an account", messages[0].msg)
        self.assertFalse(messages[0].is_serious())

    @override_settings(ENVIRONMENT="production")
    def test_production_with_a_provider_is_clean(self) -> None:
        configure_site_providers(("github", "GitHub"))

        self.assertEqual(check_production_sign_in_providers(None, databases=["default"]), [])

    @override_settings(ENVIRONMENT="production")
    def test_the_check_only_reads_the_database_when_asked_to(self) -> None:
        self.assertEqual(check_production_sign_in_providers(None, databases=None), [])

    def test_non_production_without_providers_is_clean(self) -> None:
        self.assertEqual(check_production_sign_in_providers(None, databases=["default"]), [])

    @override_settings(ENVIRONMENT="production")
    def test_the_check_is_registered(self) -> None:
        from django.core import checks

        messages = checks.run_checks(databases=["default"])

        self.assertIn(NO_PROVIDERS_CHECK_ID, [message.id for message in messages])


class ProviderListCacheTests(ProviderCacheResetTestCase):
    def test_an_empty_provider_list_is_not_cached(self) -> None:
        self.assertEqual(get_available_providers("/"), [])

        configure_site_providers(("github", "GitHub"))

        self.assertEqual([p["id"] for p in get_available_providers("/")], ["github"])

    def test_provider_names_phrase(self) -> None:
        def records(*names):
            return [{"name": name} for name in names]

        self.assertEqual(provider_names_phrase(records()), "")
        self.assertEqual(provider_names_phrase(records("GitHub")), "GitHub")
        self.assertEqual(provider_names_phrase(records("Google", "Slack")), "Google or Slack")
        self.assertEqual(
            provider_names_phrase(records("Google", "GitHub", "Slack")),
            "Google, GitHub or Slack",
        )


@override_settings(ENVIRONMENT="production")
class CreateAccountCallsToActionTests(ProviderCacheResetTestCase):
    """The masthead, home and tour CTAs go through the same account-entry state.

    In production ``/accounts/signup/`` is the provider choice (above), so
    every CTA leads there and carries the visitor's way back.
    """

    def test_masthead_sign_up_carries_next_to_the_provider_choice(self) -> None:
        configure_site_providers(*ALL_PROVIDERS)

        response = self.client.get("/courses")

        self.assertContains(
            response,
            'class="signup-button interactive-lift" href="/accounts/signup/?next=%2Fcourses"',
        )
        choice = self.client.get("/accounts/signup/?next=%2Fcourses")
        self.assertContains(choice, "/accounts/github/login/?next=%2Fcourses")

    def test_home_and_about_create_account_ctas_lead_to_the_provider_choice(self) -> None:
        configure_site_providers(*ALL_PROVIDERS)
        for path in ("/", "/about"):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'href="/accounts/signup/">Create free account')
        choice = self.client.get("/accounts/signup/")
        self.assertContains(choice, "Continue with Google")

    def test_without_a_signup_route_ctas_fall_back_to_sign_in(self) -> None:
        from unittest.mock import patch

        from django.test import RequestFactory

        from accounts.account_entry import AccountEntry

        request = RequestFactory().get("/courses")
        with patch("accounts.account_entry.signup_url_or_none", return_value=None):
            entry = AccountEntry(request)
            self.assertEqual(entry.create_url, "/accounts/login/?next=%2Fcourses")
            self.assertEqual(entry.landing_create_url, "/accounts/login/?next=%2F")

    def test_reading_the_cta_url_does_not_query_providers(self) -> None:
        from django.test import RequestFactory

        from accounts.account_entry import AccountEntry

        request = RequestFactory().get("/courses")
        with self.assertNumQueries(0):
            self.assertEqual(
                AccountEntry(request).create_url,
                "/accounts/signup/?next=%2Fcourses",
            )
