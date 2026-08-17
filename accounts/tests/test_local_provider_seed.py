"""The local placeholder provider seed refuses to run anywhere real.

A fresh local database has no ``SocialApp``, so the entrance pages draw no
provider buttons and a design reviewed there is reviewed against a page the
site does not serve.  The seed closes that gap with obviously fake credentials,
and — like the local course seed — it fails closed on anything but a local or
test SQLite database, so a placeholder cannot reach a deployed one.
"""

from __future__ import annotations

from io import StringIO

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management import CommandError, call_command
from django.test import TestCase

from accounts.services.local_provider_seed import (
    PLACEHOLDER_MARKER,
    PLACEHOLDER_PROVIDERS,
    LocalProviderSeedError,
    assert_local_database,
    seed_local_social_providers,
)
from core.bootstrap import RuntimeEnvironment


class LocalProviderSeedTests(TestCase):
    def setUp(self) -> None:
        Site.objects.get_or_create(
            pk=settings.SITE_ID,
            defaults={"domain": "testserver", "name": "testserver"},
        )

    def test_it_writes_one_app_per_installed_provider_bound_to_the_current_site(self) -> None:
        result = seed_local_social_providers()

        self.assertEqual(
            sorted(app.provider for app in SocialApp.objects.all()),
            sorted(provider for provider, _name in PLACEHOLDER_PROVIDERS),
        )
        for app in SocialApp.objects.all():
            with self.subTest(provider=app.provider):
                self.assertEqual(list(app.sites.values_list("pk", flat=True)), [settings.SITE_ID])
        self.assertEqual(result.summary()["providers_created"], 3)

    def test_running_it_twice_changes_nothing(self) -> None:
        seed_local_social_providers()
        second = seed_local_social_providers()

        self.assertEqual(SocialApp.objects.count(), len(PLACEHOLDER_PROVIDERS))
        self.assertEqual(second.summary()["providers_created"], 0)

    def test_every_written_credential_is_an_obvious_placeholder(self) -> None:
        seed_local_social_providers()

        for app in SocialApp.objects.all():
            with self.subTest(provider=app.provider):
                self.assertIn(PLACEHOLDER_MARKER, app.client_id)
                self.assertIn(PLACEHOLDER_MARKER, app.secret)

    def test_it_refuses_outside_a_local_or_test_environment(self) -> None:
        with self.settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.PRODUCTION):
            with self.assertRaises(LocalProviderSeedError) as refusal:
                assert_local_database()

        self.assertEqual(str(refusal.exception), "environment-not-local")
        self.assertEqual(SocialApp.objects.count(), 0)

    def test_it_refuses_against_a_database_that_is_not_local_sqlite(self) -> None:
        databases = {"default": {**settings.DATABASES["default"]}}
        databases["default"]["ENGINE"] = "django.db.backends.postgresql"

        with self.settings(DATABASES=databases):
            with self.assertRaises(LocalProviderSeedError) as refusal:
                assert_local_database()

        self.assertEqual(str(refusal.exception), "database-not-local-sqlite")
        self.assertEqual(SocialApp.objects.count(), 0)

    def test_the_command_checks_without_writing_and_then_seeds(self) -> None:
        output = StringIO()
        call_command("seed_local_social_providers", "--check", stdout=output)

        self.assertIn('"written": false', output.getvalue())
        self.assertEqual(SocialApp.objects.count(), 0)

        call_command("seed_local_social_providers", stdout=StringIO())
        self.assertEqual(SocialApp.objects.count(), len(PLACEHOLDER_PROVIDERS))

    def test_the_command_reports_a_refusal_instead_of_a_traceback(self) -> None:
        with self.settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.PRODUCTION):
            with self.assertRaises(CommandError) as refusal:
                call_command("seed_local_social_providers", "--check", stdout=StringIO())

        self.assertEqual(str(refusal.exception), "environment-not-local")
