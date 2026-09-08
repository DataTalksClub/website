"""Kernel-only community-base installation contract (D0.1a).

The released kernel is installed with declarations only: no other shared app,
no jobs/mail adoption, no schema change, and the site user model stays
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
    def test_kernel_and_jobs_apps_are_installed(self):
        installed = [app for app in settings.INSTALLED_APPS if app.startswith("community_base.")]
        self.assertEqual(
            installed,
            ["community_base.jobs", "community_base.kernel.apps.KernelConfig"],
        )
        self.assertEqual(apps.get_app_config("cb_kernel").name, "community_base.kernel")

    def test_site_declarations(self):
        self.assertEqual(kernel_get("SITE_KEY"), "dtc")
        self.assertEqual(
            kernel_get("ACCESS_POLICY"), "community_base.kernel.access.RegisteredOnlyPolicy"
        )
        self.assertEqual(kernel_get("JOBS_BACKEND"), "relay")
        self.assertEqual(kernel_get("MAIL_BACKEND"), "relay")
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
