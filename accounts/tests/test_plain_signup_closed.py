"""Plain email/password signup is open; a matching email is refused plainly.

`accounts.auth.AccountAdapter` (registered as `ACCOUNT_ADAPTER`) and
`ConsolidatingSocialAccountAdapter` (`SOCIALACCOUNT_ADAPTER`) both answer
`is_open_for_signup` with `True` — a genuinely new email creates a real
account through `/accounts/signup/`. Most members already have a
CMP-imported identity, so the common case at this form is a returning
member, not a stranger: `ACCOUNT_PREVENT_ENUMERATION = False`
(`website/settings/base.py`) makes a signup attempt with an email that
already has an account fail with a plain "an account already exists" error
instead of allauth's default silent no-op, and it creates no second,
conflicting row — the original risk `AccountAdapter` closed against.

`accounts/tests/test_imported_account_social_matching.py` covers the social
sign-in path, where a matching, provider-verified email resolves and
connects to the existing member's real account before allauth ever asks
whether signup is open (`accounts.auth.ConsolidatingSocialAccountAdapter`).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()

FRESH_EMAIL = "never-registered-before@example.invalid"


def _signup_payload(email: str) -> dict[str, str]:
    return {
        "email": email,
        "password1": "a-sufficiently-long-password-1",
        "password2": "a-sufficiently-long-password-1",
    }


class PlainSignupIsOpenTests(TestCase):
    def test_a_signup_post_with_a_fresh_email_creates_an_account(self) -> None:
        self.assertFalse(User.objects.filter(email=FRESH_EMAIL).exists())

        self.client.post("/accounts/signup/", _signup_payload(FRESH_EMAIL))

        self.assertTrue(User.objects.filter(email=FRESH_EMAIL).exists())

    def test_a_signup_post_with_a_fresh_email_signs_the_caller_in(self) -> None:
        self.client.post("/accounts/signup/", _signup_payload(FRESH_EMAIL))

        self.assertIn("_auth_user_id", self.client.session)

    def test_a_plain_get_renders_the_real_form_not_a_closed_page(self) -> None:
        response = self.client.get("/accounts/signup/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup.html")
        self.assertTemplateNotUsed(response, "account/signup_closed.html")
        self.assertContains(response, 'name="password1"')

    def test_posting_an_existing_members_address_creates_nothing_and_says_so(self) -> None:
        existing = User.objects.create_user(
            username="existing.member@example.invalid",
            email="existing.member@example.invalid",
            password="not-the-password-being-guessed",
        )

        response = self.client.post("/accounts/signup/", _signup_payload(existing.email))

        self.assertTemplateUsed(response, "account/signup.html")
        self.assertTemplateNotUsed(response, "account/signup_closed.html")
        body = response.content.decode()
        self.assertIn("already exists", body)
        self.assertIn("Google", body)
        self.assertIn("reset your password", body)
        self.assertNotIn("_auth_user_id", self.client.session)
        # No second account: still exactly one row for that address, and its
        # real password still authenticates it.
        self.assertEqual(User.objects.filter(email=existing.email).count(), 1)
        self.assertTrue(
            self.client.login(username=existing.email, password="not-the-password-being-guessed")
        )
