"""Kernel integration contract (D0.1a).

The released community-base kernel is installed beside the existing runtime
owners. This contract proves the staging claim behind that installation: the
kernel loads and answers access decisions without any other shared model
application, and the declarations in ``COMMUNITY_BASE`` stay declarative
(backend names are recorded, nothing dials Relay, and no migration arises).
"""

from community_base.kernel import access
from community_base.kernel.conf import get
from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, override_settings

# AUTH_USER_MODEL remains accounts.CustomUser, so the accounts app stays
# installed even in the kernel-only registry. Every other site and package
# model application is absent: the kernel has no model dependencies.
KERNEL_ONLY_INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "accounts.apps.AccountsConfig",
    "community_base.kernel.apps.KernelConfig",
]


class KernelInstallationTests(SimpleTestCase):
    def test_kernel_app_is_installed_with_package_label_and_no_models(self):
        config = apps.get_app_config("cb_kernel")
        self.assertEqual(config.name, "community_base.kernel")
        self.assertEqual(list(config.get_models()), [])

    def test_kernel_declares_site_configuration_without_runtime_owners(self):
        self.assertEqual(get("SITE_KEY"), "dtc")
        self.assertEqual(get("ACCESS_POLICY"), "community_base.kernel.access.RegisteredOnlyPolicy")
        self.assertEqual(get("JOBS_BACKEND"), "relay")
        self.assertEqual(get("MAIL_BACKEND"), "relay")
        self.assertEqual(get("STUDIO_TITLE"), "DataTalks.Club Studio")
        # Existing runtime owners are untouched: jobs and email_app stay the
        # Django apps handling background work and mail until adoption issues
        # cut over, and no Relay credential is configured.
        self.assertIn("jobs.apps.JobsConfig", settings.INSTALLED_APPS)
        self.assertIn("email_app.apps.EmailAppConfig", settings.INSTALLED_APPS)
        self.assertEqual(get("RELAY_BASE_URL"), "")
        self.assertEqual(get("RELAY_API_KEY"), "")

    def test_auth_user_model_is_unchanged(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.CustomUser")


class KernelStandaloneTests(SimpleTestCase):
    """The kernel alone answers access decisions without shared model apps."""

    def run_kernel_only(self):
        return override_settings(INSTALLED_APPS=KERNEL_ONLY_INSTALLED_APPS)

    def assert_kernel_registry(self):
        labels = {config.label for config in apps.get_app_configs()}
        self.assertIn("cb_kernel", labels)
        self.assertIn("accounts", labels)
        for absent in ("content", "courses", "jobs", "email_app", "events", "studio", "api"):
            self.assertNotIn(absent, labels)
        # AUTH_USER_MODEL still resolves with only the kernel installed beside
        # accounts, and the kernel app still exposes no models.
        self.assertEqual(apps.get_model(settings.AUTH_USER_MODEL).__name__, "CustomUser")
        kernel = apps.get_app_config("cb_kernel")
        self.assertEqual(list(kernel.get_models()), [])

    def test_kernel_loads_and_configures_without_other_shared_model_apps(self):
        with self.run_kernel_only():
            self.assert_kernel_registry()
            self.assertEqual(get("SITE_KEY"), "dtc")
            self.assertEqual(get("STUDIO_TITLE"), "DataTalks.Club Studio")

    def test_anonymous_level_registered_is_denied_and_open_allowed(self):
        with self.run_kernel_only():
            self.assert_kernel_registry()
            self.assertFalse(access.can_access(AnonymousUser(), access.LEVEL_REGISTERED))
            self.assertTrue(access.can_access(AnonymousUser(), access.LEVEL_OPEN))

    def test_authenticated_level_registered_allowed_and_paid_levels_denied(self):
        authenticated = type("StubUser", (), {"is_authenticated": True})()
        with self.run_kernel_only():
            self.assert_kernel_registry()
            self.assertTrue(access.can_access(authenticated, access.LEVEL_REGISTERED))
            self.assertFalse(access.can_access(authenticated, access.LEVEL_BASIC))
            self.assertFalse(access.can_access(authenticated, access.LEVEL_MAIN))
            self.assertFalse(access.can_access(authenticated, access.LEVEL_PREMIUM))
            self.assertEqual(access.level_label(access.LEVEL_REGISTERED), "Registered")
