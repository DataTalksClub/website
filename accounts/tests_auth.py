from types import SimpleNamespace
from unittest.mock import Mock

from allauth.core.exceptions import ImmediateHttpResponse
from django.test import TestCase

from accounts.auth import ConsolidatingSocialAccountAdapter, extract_email
from accounts.models import AccountIdentityQuarantine, CustomUser


class ExtractEmailTestCase(TestCase):
    def email_address(self, email, verified=False):
        return SimpleNamespace(email=email, verified=verified)

    def sociallogin_with_emails(self, *email_addresses):
        email_address_list = list(email_addresses)
        return SimpleNamespace(email_addresses=email_address_list)

    def test_extract_email_ignores_unverified_provider_response(self):
        verified_email = self.email_address(
            "verified@example.com",
            verified=True,
        )
        sociallogin = self.sociallogin_with_emails(
            verified_email,
        )

        email = extract_email(
            {"email": "response@example.com"},
            sociallogin=sociallogin,
        )

        self.assertEqual(email, "verified@example.com")

    def test_extract_email_uses_verified_social_email(self):
        first_email = self.email_address("first@example.com")
        verified_email = self.email_address(
            "verified@example.com",
            verified=True,
        )
        sociallogin = self.sociallogin_with_emails(
            first_email,
            verified_email,
        )

        email = extract_email({}, sociallogin=sociallogin)

        self.assertEqual(email, "verified@example.com")

    def test_extract_email_rejects_unverified_social_email(self):
        first_email = self.email_address("first@example.com")
        second_email = self.email_address("second@example.com")
        sociallogin = self.sociallogin_with_emails(
            first_email,
            second_email,
        )

        with self.assertRaises(KeyError):
            extract_email({}, sociallogin=sociallogin)

    def test_extract_email_rejects_notification_email(self):
        with self.assertRaises(KeyError):
            extract_email({"notification_email": "notify@example.com"})

    def test_extract_email_raises_when_missing(self):
        with self.assertRaises(KeyError):
            extract_email({})


class ConsolidatingSocialAccountAdapterTestCase(TestCase):
    def test_social_login_rejects_unverified_legacy_username_match(self):
        email = "legacy-owner@example.invalid"
        user = CustomUser.objects.create_user(
            username=email,
            email="",
        )
        sociallogin = SimpleNamespace(
            account=SimpleNamespace(
                provider="github",
                uid="synthetic-unverified-uid",
                extra_data={"email": email},
            ),
            is_existing=False,
            email_addresses=[],
            connect=Mock(),
        )
        adapter = ConsolidatingSocialAccountAdapter()

        with self.assertRaises(ImmediateHttpResponse) as raised:
            adapter.pre_social_login(None, sociallogin)

        self.assertEqual(raised.exception.response.status_code, 409)
        sociallogin.connect.assert_not_called()
        user.refresh_from_db()
        self.assertEqual(user.email, "")
        self.assertEqual(AccountIdentityQuarantine.objects.count(), 1)
