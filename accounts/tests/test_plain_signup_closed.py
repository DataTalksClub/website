"""Plain email/password signup is closed, the same as OAuth signup.

`accounts.auth.ConsolidatingSocialAccountAdapter` closes OAuth-based signup by
answering `is_open_for_signup` with `False`, but a site that mounts
`allauth.urls` wholesale (`website/urls.py`) still exposes
`/accounts/signup/` — the plain email/password path — through whichever
adapter `ACCOUNT_ADAPTER` names, and nothing named one. allauth's
`DefaultAccountAdapter` defaults that method to `True`, so a POST to that
address with a fresh email and password created a real, already-signed-in
account: an account matched by email to the ~20,000 CMP-imported members'
enrollments, submissions, scores and certificates
(`accounts/tests/test_imported_account_social_matching.py`), gaining
whichever of them the address happened to touch, with none of the
provider-verification evidence the social adapter requires before it will
link anything.  `ACCOUNT_ALLOW_REGISTRATION = False` sat in settings and read
like a gate; nothing in allauth or this codebase ever checked it.

This is what `accounts.auth.ClosedAccountAdapter` closes, registered as
`ACCOUNT_ADAPTER`. These tests pin it shut with the same kind of proof the
signup path lacked: a live HTTP POST that would have created an account,
checked against a database that has no such account afterward — not a check
that a setting equals `False`.
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


class PlainSignupIsClosedTests(TestCase):
    def test_a_signup_post_does_not_create_an_account(self) -> None:
        self.assertFalse(User.objects.filter(email=FRESH_EMAIL).exists())

        self.client.post("/accounts/signup/", _signup_payload(FRESH_EMAIL))

        self.assertFalse(User.objects.filter(email=FRESH_EMAIL).exists())

    def test_a_signup_post_does_not_sign_the_caller_in(self) -> None:
        self.client.post("/accounts/signup/", _signup_payload(FRESH_EMAIL))

        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_signup_post_renders_the_closed_page_not_the_form(self) -> None:
        response = self.client.post("/accounts/signup/", _signup_payload(FRESH_EMAIL))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup_closed.html")
        self.assertTemplateNotUsed(response, "account/signup.html")
        body = response.content.decode()
        self.assertIn("Sign-up is closed", body)
        self.assertNotIn('name="password1"', body)

    def test_a_plain_get_meets_the_same_closed_page_as_the_post(self) -> None:
        # `is_open_for_signup` gates both verbs identically; a visitor must
        # not be shown a live form that a submission is then refused for.
        response = self.client.get("/accounts/signup/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup_closed.html")

    def test_the_closed_page_still_offers_a_way_in_and_around(self) -> None:
        body = self.client.get("/accounts/signup/").content.decode()

        self.assertIn('href="/accounts/login/"', body)
        self.assertIn('href="/courses"', body)

    def test_posting_an_existing_members_address_still_creates_nothing(self) -> None:
        # The sharper-edged case: an attacker who knows a real member's
        # address cannot use the plain form to attach a guessed password to
        # that identity either. The adapter refuses before allauth's own
        # "email already registered" handling would even run.
        existing = User.objects.create_user(
            username="existing.member@example.invalid",
            email="existing.member@example.invalid",
            password="not-the-password-being-guessed",
        )

        response = self.client.post("/accounts/signup/", _signup_payload(existing.email))

        self.assertTemplateUsed(response, "account/signup_closed.html")
        self.assertNotIn("_auth_user_id", self.client.session)
        # The existing account is untouched: still exactly one row for that
        # address, and its real password still authenticates it.
        self.assertEqual(User.objects.filter(email=existing.email).count(), 1)
        self.assertTrue(
            self.client.login(username=existing.email, password="not-the-password-being-guessed")
        )
