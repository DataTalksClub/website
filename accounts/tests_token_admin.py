from django.contrib import admin
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from accounts.models import Token


class LegacyTokenAdminAccessTests(TestCase):
    def test_plaintext_legacy_token_model_is_not_an_admin_surface(self) -> None:
        self.assertFalse(admin.site.is_registered(Token))
        with self.assertRaises(NoReverseMatch):
            reverse("admin:accounts_token_add")
