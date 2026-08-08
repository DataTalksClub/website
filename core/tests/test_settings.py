import json
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
        self.assertIn("Invalid bootstrap setting DJANGO_SECRET_KEY", result.stderr)

    def test_production_rejects_known_unsafe_secret_values(self) -> None:
        for secret in (LOCAL_DEVELOPMENT_SECRET_KEY, EXAMPLE_SECRET_KEY, TEST_SECRET_KEY):
            with self.subTest(secret=secret):
                result = self.import_production_settings(secret)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid bootstrap setting DJANGO_SECRET_KEY", result.stderr)
                self.assertNotIn(secret, result.stderr)

    def test_production_accepts_a_real_strong_secret(self) -> None:
        result = self.import_production_settings(secrets.token_urlsafe(64))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_deployment_settings_keep_exact_https_and_readiness_contract(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "DATABASE_URL": "postgresql://check:check@127.0.0.1:5432/check",
                "DJANGO_ALLOWED_HOSTS": "web.dtcdev.click",
                "DJANGO_CSRF_TRUSTED_ORIGINS": "https://web.dtcdev.click",
                "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
            }
        )
        command = (
            "import importlib, json, sys; "
            "settings = importlib.import_module(f'website.settings.{sys.argv[1]}'); "
            "print(json.dumps({"
            "'ssl_redirect': settings.SECURE_SSL_REDIRECT, "
            "'proxy_header': settings.SECURE_PROXY_SSL_HEADER, "
            "'redirect_exempt': settings.SECURE_REDIRECT_EXEMPT"
            "}))"
        )

        expected = {
            "ssl_redirect": True,
            "proxy_header": ["HTTP_X_FORWARDED_PROTO", "https"],
            "redirect_exempt": [r"^health/ready$"],
        }
        for module in ("development", "production"):
            with self.subTest(module=module):
                module_environment = {**environment, "DTC_ENVIRONMENT": module}
                result = subprocess.run(
                    [sys.executable, "-c", command, module],
                    cwd=os.getcwd(),
                    env=module_environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)

    def test_committed_example_secret_is_in_the_production_denylist(self) -> None:
        example_line = next(
            line
            for line in (BASE_DIR / ".env.example").read_text().splitlines()
            if line.startswith("DJANGO_SECRET_KEY=")
        )
        self.assertIn(example_line.partition("=")[2], UNSAFE_SECRET_KEYS)

    def test_development_accepts_only_the_exact_host_and_trusted_origin(self) -> None:
        base_environment = os.environ.copy()
        base_environment.update(
            {
                "DATABASE_URL": "postgresql://check:check@127.0.0.1:5432/check",
                "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
                "DTC_ENVIRONMENT": "development",
            }
        )
        command = (
            "import json; import website.settings.development as s; "
            "print(json.dumps([s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS, "
            "s.SECURE_PROXY_SSL_HEADER]))"
        )
        accepted_environment = {
            **base_environment,
            "DJANGO_ALLOWED_HOSTS": "web.dtcdev.click",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://web.dtcdev.click",
        }
        accepted = subprocess.run(
            [sys.executable, "-c", command],
            cwd=os.getcwd(),
            env=accepted_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(
            json.loads(accepted.stdout),
            [
                ["web.dtcdev.click"],
                ["https://web.dtcdev.click"],
                ["HTTP_X_FORWARDED_PROTO", "https"],
            ],
        )

        for name, value in (
            ("DJANGO_ALLOWED_HOSTS", "web.dtcdev.click,unrelated.invalid"),
            ("DJANGO_CSRF_TRUSTED_ORIGINS", "https://unrelated.invalid"),
        ):
            with self.subTest(name=name):
                rejected = subprocess.run(
                    [sys.executable, "-c", command],
                    cwd=os.getcwd(),
                    env={**accepted_environment, name: value},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn(f"exact {name}", rejected.stderr)

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


class CollectstaticSettingsTests(SimpleTestCase):
    def import_collectstatic_settings(
        self, *, environment_name: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "DATABASE_URL",
            "DJANGO_ALLOWED_HOSTS",
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "DJANGO_SECRET_KEY",
            "DJANGO_SETTINGS_MODULE",
            "DTC_ENVIRONMENT",
        ):
            environment.pop(name, None)
        if environment_name is not None:
            environment["DTC_ENVIRONMENT"] = environment_name
        command = (
            "import json; import website.settings.collectstatic as s; "
            "print(json.dumps({"
            "'backend': s.STORAGES['staticfiles']['BACKEND'], "
            "'database': s.DATABASES['default']['ENGINE']"
            "}))"
        )
        return subprocess.run(
            [sys.executable, "-c", command],
            cwd=os.getcwd(),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_collectstatic_settings_are_self_contained_and_use_runtime_manifest_storage(
        self,
    ) -> None:
        result = self.import_collectstatic_settings()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "backend": "whitenoise.storage.CompressedManifestStaticFilesStorage",
                "database": "django.db.backends.sqlite3",
            },
        )

    def test_collectstatic_settings_reject_deployed_environments(self) -> None:
        for environment_name in ("development", "production"):
            with self.subTest(environment_name=environment_name):
                result = self.import_collectstatic_settings(environment_name=environment_name)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Collectstatic settings are build-only", result.stderr)
