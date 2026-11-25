"""Email + password accounts: open signup, mandatory verification, Relay mail.

Signup is open in every environment.  A new password account must confirm
its address before a password login works; a signup with an address that
already has an account creates nothing and says nothing on the page (the
owner gets an "already exists" email).  Every allauth email is a package
mail delivery (``accounts.auth.AccountAdapter.send_mail``).

`accounts/tests/test_imported_account_social_matching.py` covers the social
sign-in path.
"""

from __future__ import annotations

from allauth.account.models import EmailAddress
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.auth import AccountAdapter

User = get_user_model()

FRESH_EMAIL = "never-registered-before@example.invalid"
PASSWORD = "a-sufficiently-long-password-1"


def _signup_payload(email: str) -> dict[str, str]:
    return {"email": email, "password1": PASSWORD, "password2": PASSWORD}


def _purposes(email: str) -> list[str]:
    return list(
        EmailDelivery.objects.filter(recipient_email=email)
        .order_by("created_at")
        .values_list("purpose", flat=True)
    )


class PasswordSignupTests(TestCase):
    def test_fresh_signup_creates_an_unverified_account_and_mails_the_link(self) -> None:
        response = self.client.post("/accounts/signup/", _signup_payload(FRESH_EMAIL))

        self.assertRedirects(response, "/accounts/confirm-email/", fetch_redirect_response=False)
        user = User.objects.get(email=FRESH_EMAIL)
        self.assertTrue(user.username)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(EmailAddress.objects.get(user=user).verified)
        self.assertEqual(_purposes(FRESH_EMAIL), ["account-verify-email"])
        delivery = EmailDelivery.objects.get(recipient_email=FRESH_EMAIL)
        self.assertIn("/accounts/confirm-email/", delivery.context_data["verify_url"])

    def test_two_signups_get_distinct_usernames(self) -> None:
        self.client.post("/accounts/signup/", _signup_payload(FRESH_EMAIL))
        self.client.post("/accounts/signup/", _signup_payload("second@example.invalid"))

        self.assertEqual(User.objects.filter(username="").count(), 0)
        self.assertEqual(User.objects.count(), 2)

    def test_password_login_waits_for_verification(self) -> None:
        self.client.post("/accounts/signup/", _signup_payload(FRESH_EMAIL))
        self.client.logout()

        refused = self.client.post("/accounts/login/", {"login": FRESH_EMAIL, "password": PASSWORD})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(refused.status_code, 302)

        EmailAddress.objects.filter(email=FRESH_EMAIL).update(verified=True)
        self.client.post("/accounts/login/", {"login": FRESH_EMAIL.upper(), "password": PASSWORD})
        self.assertIn("_auth_user_id", self.client.session)

    def test_existing_address_creates_nothing_and_does_not_say_so(self) -> None:
        existing = User.objects.create_user(
            username="existing-member",
            email="Existing.Member@Example.invalid",
            password="not-the-password-being-guessed",
        )

        response = self.client.post(
            "/accounts/signup/", _signup_payload("existing.member@example.invalid")
        )

        self.assertRedirects(response, "/accounts/confirm-email/", fetch_redirect_response=False)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(existing.check_password("not-the-password-being-guessed"))
        self.assertEqual(_purposes("existing.member@example.invalid"), ["account-already-exists"])

    @override_settings(ENVIRONMENT="production")
    def test_production_signup_is_open(self) -> None:
        self.assertTrue(AccountAdapter().is_open_for_signup(None))


class PasswordResetTests(TestCase):
    def test_reset_reaches_an_account_without_a_password(self) -> None:
        user = User.objects.create_user(username="social-only", email="Social.Only@Example.invalid")
        user.set_unusable_password()
        user.save()

        response = self.client.post(
            "/accounts/password/reset/", {"email": "social.only@example.invalid"}
        )

        self.assertRedirects(
            response, "/accounts/password/reset/done/", fetch_redirect_response=False
        )
        self.assertEqual(_purposes("social.only@example.invalid"), ["account-password-reset"])

    def test_unknown_address_gets_the_same_page_and_no_mail(self) -> None:
        response = self.client.post(
            "/accounts/password/reset/", {"email": "nobody@example.invalid"}
        )

        self.assertRedirects(
            response, "/accounts/password/reset/done/", fetch_redirect_response=False
        )
        self.assertEqual(EmailDelivery.objects.count(), 0)
