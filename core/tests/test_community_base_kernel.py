"""Community-base installation contract (D0.1a, extended by D1.1 and D1.2a).

The released kernel, jobs app and mail app are installed. D1.1 moved durable
intents onto the package jobs app; D1.2a installs the mail app without
switching any send path yet. The site user model stays
``accounts.CustomUser``. The configured RegisteredOnlyPolicy must gate levels
exactly as the site's tier model does today.
"""

from community_base.kernel import access
from community_base.kernel.conf import get as kernel_get
from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase


class _AuthenticatedUser:
    is_authenticated = True


class KernelInstallationTests(SimpleTestCase):
    def test_community_base_apps_are_installed(self):
        installed = [app for app in settings.INSTALLED_APPS if app.startswith("community_base.")]
        self.assertEqual(
            installed,
            [
                "community_base.jobs",
                "community_base.mail",
                "community_base.kernel.apps.KernelConfig",
                "community_base.config",
                "community_base.api",
            ],
        )
        self.assertEqual(apps.get_app_config("cb_kernel").name, "community_base.kernel")
        self.assertEqual(apps.get_app_config("cb_mail").name, "community_base.mail")

    def test_site_declarations(self):
        self.assertEqual(kernel_get("SITE_KEY"), "dtc")
        self.assertEqual(
            kernel_get("ACCESS_POLICY"), "community_base.kernel.access.RegisteredOnlyPolicy"
        )
        # Test settings pin both backends to their process-local loops; base.py
        # declares the relay direction development and production run.
        self.assertEqual(kernel_get("JOBS_BACKEND"), "sync")
        self.assertEqual(kernel_get("MAIL_BACKEND"), "memory")
        self.assertEqual(kernel_get("STUDIO_TITLE"), "DataTalks.Club Studio")

    def test_auth_user_model_is_unchanged(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.CustomUser")

    def test_registered_only_policy_gates(self):
        self.assertTrue(access.can_access(None, 0))
        self.assertTrue(access.can_access(AnonymousUser(), 0))
        self.assertTrue(access.can_access(_AuthenticatedUser(), 0))

        self.assertFalse(access.can_access(None, 5))
        self.assertFalse(access.can_access(AnonymousUser(), 5))
        self.assertTrue(access.can_access(_AuthenticatedUser(), 5))

        for paid_level in (10, 20, 30):
            self.assertFalse(access.can_access(_AuthenticatedUser(), paid_level))

    def test_level_label_uses_the_shared_registry(self):
        self.assertEqual(access.level_label(5), "Registered")
