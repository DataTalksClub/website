from __future__ import annotations

import json
import os
import subprocess
import sys

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib import admin
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse

from accounts.models import CustomUser
from accounts.services.local_provider_seed import PLACEHOLDER_PROVIDERS, seed_local_social_providers
from website.settings.base import BASE_DIR


class RuntimeAuthBoundaryTests(SimpleTestCase):
    @staticmethod
    def import_runtime_setting(module: str, environment: str) -> dict[str, object]:
        child_environment = os.environ.copy()
        child_environment.update(
            {
                "DTC_ENVIRONMENT": environment,
                "DJANGO_SETTINGS_MODULE": f"website.settings.{module}",
                "VERSION": "20260809-143205-aaaaaaa",
                "SOURCE_SHA": "a" * 40,
                "IMAGE_DIGEST": f"sha256:{'b' * 64}",
            }
        )
        child_environment.pop("DTC_SQLITE_PATH", None)
        child_environment.pop("APP_VERSION", None)
        command = (
            "import importlib, json, sys; "
            "settings = importlib.import_module(sys.argv[1]); "
            "print(json.dumps({"
            "'environment': settings.ENVIRONMENT, "
            "'owner_login': settings.DEVELOPMENT_OWNER_LOGIN_ENABLED"
            "}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", command, f"website.settings.{module}"],
            cwd=BASE_DIR,
            env=child_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_owner_login_is_local_and_development_only(self) -> None:
        expected = (
            ("local", "local", True),
            ("test", "test", False),
            ("base", "development", True),
            ("base", "production", False),
        )
        for module, environment, enabled in expected:
            with self.subTest(module=module, environment=environment):
                settings_snapshot = self.import_runtime_setting(module, environment)
                self.assertEqual(settings_snapshot["environment"], environment)
                self.assertEqual(settings_snapshot["owner_login"], enabled)


class LoginPageConfigurationTests(TestCase):
    def setUp(self) -> None:
        cache.delete("available_providers")
        Site.objects.get_or_create(
            pk=settings.SITE_ID,
            defaults={"domain": "testserver", "name": "testserver"},
        )

    def test_local_password_form_is_available_without_social_provider_rows(self) -> None:
        with override_settings(DEVELOPMENT_OWNER_LOGIN_ENABLED=True):
            response = self.client.get("/accounts/login/?next=/books")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, "Choose your preferred sign-in method")
        self.assertNotContains(response, "Back to courses")
        self.assertNotContains(response, "Sign-in is temporarily unavailable")

    def test_empty_auth_configuration_has_no_misleading_course_fallback(self) -> None:
        with override_settings(DEVELOPMENT_OWNER_LOGIN_ENABLED=False):
            for next_path in ("/books", "/accounts/signup/"):
                with self.subTest(next_path=next_path):
                    response = self.client.get(f"/accounts/login/?next={next_path}")

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Sign-in is temporarily unavailable")
                self.assertNotContains(response, "Back to courses")
                self.assertNotContains(response, "Choose your preferred sign-in method")

    def test_seeded_cmp_providers_render_on_login_and_signup(self) -> None:
        seed_local_social_providers()

        for path in ("/accounts/login/?next=/books", "/accounts/signup/?next=/books"):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                if "login" in path:
                    self.assertContains(response, "Sign in")
                    self.assertContains(response, "Choose your preferred sign-in method")
                self.assertNotContains(response, "Sign-in is temporarily unavailable")
                for provider, name in PLACEHOLDER_PROVIDERS:
                    with self.subTest(path=path, provider=provider):
                        self.assertContains(response, f"Continue with {name}")
                        self.assertContains(response, f"/accounts/{provider}/login/")
                if "login" in path:
                    self.assertContains(
                        response,
                        "Secure login — we never store your social media passwords",
                    )

    def test_social_methods_precede_the_email_method_on_both_entrance_pages(self) -> None:
        seed_local_social_providers()

        with override_settings(DEVELOPMENT_OWNER_LOGIN_ENABLED=True):
            login_body = self.client.get("/accounts/login/?next=/books").content.decode()
        signup_body = self.client.get("/accounts/signup/?next=/books").content.decode()

        for page, body, divider, form in (
            ("login", login_body, "auth-or", "auth-form"),
            ("signup", signup_body, "entrance-or", "entrance-form"),
        ):
            with self.subTest(page=page):
                provider_at = body.index(
                    'class="auth-choices"' if page == "login" else 'class="provider-choices"'
                )
                divider_at = body.index(f'class="{divider}"')
                form_at = body.index(f'class="{form}"')
                self.assertLess(provider_at, divider_at)
                self.assertLess(divider_at, form_at)
                self.assertIn(
                    "or sign in with email" if page == "login" else "or sign up with email",
                    body,
                )

    def test_site_bound_social_apps_render_cmp_provider_choices_and_next(self) -> None:
        site = Site.objects.get(pk=settings.SITE_ID)
        for provider, name in (("google", "Google"), ("github", "GitHub"), ("slack", "Slack")):
            app = SocialApp.objects.create(
                provider=provider,
                name=name,
                client_id=f"test-{provider}-client-id",
                secret="test-not-a-secret",
            )
            app.sites.add(site)

        response = self.client.get("/accounts/login/?next=/books")

        self.assertEqual(response.status_code, 200)
        for provider, name in (("google", "Google"), ("github", "GitHub"), ("slack", "Slack")):
            with self.subTest(provider=provider):
                self.assertContains(response, f"Continue with {name}")
                self.assertContains(response, f"/accounts/{provider}/login/?next=%2Fbooks")
        self.assertNotContains(response, "Sign-in is temporarily unavailable")

    def test_installed_social_providers_have_login_and_callback_routes(self) -> None:
        for provider in ("google", "github", "slack"):
            with self.subTest(provider=provider):
                self.assertEqual(reverse(f"{provider}_login"), f"/accounts/{provider}/login/")
                self.assertEqual(
                    reverse(f"{provider}_callback"),
                    f"/accounts/{provider}/login/callback/",
                )

    def test_social_callbacks_fail_closed_without_an_oauth_response(self) -> None:
        seed_local_social_providers()

        for provider in ("google", "github", "slack"):
            with self.subTest(provider=provider):
                response = self.client.get(reverse(f"{provider}_callback"))
                self.assertEqual(response.status_code, 401)


class AdminEntryPointTests(TestCase):
    def test_admin_login_is_django_admin_and_reaches_admin_index(self) -> None:
        match = resolve("/admin/login/")
        self.assertIs(match.func.__self__, admin.site)

        user = CustomUser.objects.create_user(
            username="admin",
            email="admin@example.invalid",
            password="local-admin-password-107",
            is_staff=True,
            is_superuser=True,
        )

        with override_settings(DEVELOPMENT_OWNER_LOGIN_ENABLED=True):
            response = self.client.post(
                "/admin/login/?next=/admin/",
                {
                    "username": user.username,
                    "password": "local-admin-password-107",
                    "next": "/admin/",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/")
        admin_home = self.client.get("/admin/")
        self.assertEqual(admin_home.status_code, 200)
        self.assertContains(admin_home, "DataTalks.Club Django admin")
        for broken_overlay_marker in (
            "Available shortcuts",
            "Open command tool",
            "Toggle sidebar",
            "shortcutsOpen",
            "openCommandResults",
            "searchCommand()",
        ):
            with self.subTest(marker=broken_overlay_marker):
                self.assertNotContains(admin_home, broken_overlay_marker)
        for csp_incompatible_asset in (
            "unfold/js/alpine/",
            "unfold/js/app.js",
        ):
            with self.subTest(asset=csp_incompatible_asset):
                self.assertNotContains(admin_home, csp_incompatible_asset)
        self.assertNotIn("'unsafe-eval'", admin_home.headers["Content-Security-Policy"])
