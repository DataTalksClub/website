import os
import secrets
import subprocess
import sys
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from website.settings.base import (
    BASE_DIR,
    EXAMPLE_SECRET_KEY,
    LOCAL_DEVELOPMENT_SECRET_KEY,
    TEST_SECRET_KEY,
    UNSAFE_SECRET_KEYS,
    database_from_environment,
)


class ProductionSettingsTests(SimpleTestCase):
    def import_production_settings(self, secret: str | None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "DTC_ENVIRONMENT": "production",
                "DATABASE_URL": "postgresql://check:check@127.0.0.1:5432/check",
                "DJANGO_ALLOWED_HOSTS": "example.invalid",
                "DJANGO_CSRF_TRUSTED_ORIGINS": "https://example.invalid",
            }
        )
        if secret is None:
            environment.pop("DJANGO_SECRET_KEY", None)
        else:
            environment["DJANGO_SECRET_KEY"] = secret
        return subprocess.run(
            [sys.executable, "-c", "import website.settings.production"],
            cwd=os.getcwd(),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_production_settings_fail_closed_without_critical_configuration(self) -> None:
        environment = os.environ.copy()
        for name in (
            "DJANGO_SECRET_KEY",
            "DATABASE_URL",
            "DJANGO_ALLOWED_HOSTS",
            "DJANGO_CSRF_TRUSTED_ORIGINS",
        ):
            environment.pop(name, None)
        environment["DTC_ENVIRONMENT"] = "production"
        result = subprocess.run(
            [sys.executable, "-c", "import website.settings.production"],
            cwd=os.getcwd(),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Required bootstrap setting", result.stderr)

    def test_production_rejects_known_unsafe_secret_values(self) -> None:
        for secret in (LOCAL_DEVELOPMENT_SECRET_KEY, EXAMPLE_SECRET_KEY, TEST_SECRET_KEY):
            with self.subTest(secret=secret):
                result = self.import_production_settings(secret)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("uses a known unsafe value", result.stderr)

    def test_production_accepts_a_real_strong_secret(self) -> None:
        result = self.import_production_settings(secrets.token_urlsafe(64))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_committed_example_secret_is_in_the_production_denylist(self) -> None:
        example_line = next(
            line
            for line in (BASE_DIR / ".env.example").read_text().splitlines()
            if line.startswith("DJANGO_SECRET_KEY=")
        )
        self.assertIn(example_line.partition("=")[2], UNSAFE_SECRET_KEYS)

    def test_database_url_selects_postgresql(self) -> None:
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://dtc:placeholder@db.example.invalid/dtc"},
            clear=False,
        ):
            database = database_from_environment()
        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")

    def test_sqlite_is_not_an_implicit_local_fallback(self) -> None:
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ImproperlyConfigured):
            database_from_environment(allow_sqlite=True)
