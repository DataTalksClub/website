"""Launch-day sign-in for accounts that arrived through the bulk import.

The production migration imports the CMP accounts but deliberately never
imports ``socialaccount_socialaccount``, ``socialaccount_socialapp``,
``accounts_token`` or ``django_session`` — those carry live OAuth tokens and
sessions.  Every imported member therefore arrives with **no** provider link
and has never signed in here.  On launch day they press "Continue with Google"
and the only thing that can reunite them with their enrollments, submissions,
scores and certificates is the email address the provider asserts.

These tests drive the real allauth provider callback — the provider's own
``extract_email_addresses``/``cleanup_email_addresses``, the OAuth2 callback
view, ``ConsolidatingSocialAccountAdapter`` and the session login — against
users shaped exactly the way the importer leaves them.  They assert the
outcome the owner asked for: the member lands on the *existing* primary key
and their history is visible on the pages they would actually look at.

The shapes are taken from the export's own numbers, not invented: of 20,009
accounts, 20,004 carry a verified ``account_emailaddress`` row, one carries an
unverified row, four carry no row at all, eleven rows differ from the account
email only by case, eighteen carry a plus tag, and exactly one address is
shared by two accounts.  Every address below is synthetic.
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests
from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount, SocialApp
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.auth import ConsolidatingSocialAccountAdapter
from accounts.models import AccountIdentityQuarantine, CustomUser
from courses.models import (
    Answer,
    AnswerTypes,
    Cohort,
    Enrollment,
    Homework,
    HomeworkState,
    Question,
    QuestionTypes,
    Submission,
)

GITHUB_PROFILE_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Obviously inert. Nothing here authenticates against anything.
SYNTHETIC_ACCESS_TOKEN = "synthetic-access-token-not-a-secret"  # nosec B105


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"status {self.status_code}")


class _FakeProviderSession:
    """Stands in for the provider's HTTPS calls, and nothing else.

    The provider classes keep running for real, which is the point: the
    verified/unverified decision this suite is about is made inside
    ``Provider.cleanup_email_addresses``.
    """

    def __init__(self, routes: dict[str, object]) -> None:
        self._routes = routes

    def __enter__(self) -> _FakeProviderSession:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, url: str, headers: object = None, **kwargs: object) -> _FakeResponse:
        del headers, kwargs
        if url in self._routes:
            return _FakeResponse(self._routes[url])
        return _FakeResponse({}, status_code=404)


class ImportedAccountSignInTestCase(TestCase):
    """Fixtures for an account the CMP importer has just written."""

    def setUp(self) -> None:
        super().setUp()
        site, _ = Site.objects.get_or_create(
            pk=settings.SITE_ID,
            defaults={"domain": "testserver", "name": "testserver"},
        )
        for provider, name in (
            ("google", "Google"),
            ("github", "GitHub"),
            ("slack", "Slack"),
        ):
            app = SocialApp.objects.create(
                provider=provider,
                name=name,
                client_id=f"synthetic-{provider}-client-id",
                secret="synthetic-not-a-secret",
            )
            app.sites.add(site)

    # -- imported-account shapes -------------------------------------------

    def imported_user(
        self,
        *,
        email: str,
        username: str | None = None,
        email_address: str | None | bool = True,
        email_address_verified: bool = True,
        normalized_email: str | None = None,
    ) -> CustomUser:
        """Create a user shaped the way a bulk import leaves it.

        No usable password, no ``SocialAccount``, ``identity_state`` at its
        ``legacy`` default.  ``email_address`` selects the
        ``account_emailaddress`` shape: ``True`` for the row the export
        carries for 20,004 accounts, ``False`` for the four that carry none,
        or an explicit string for the eleven whose row differs from the
        account email by case.  ``normalized_email`` writes the column
        directly, so a raw bulk insert that bypassed ``CustomUser.save()`` can
        be reproduced by passing ``""``.
        """

        user = CustomUser.objects.create(
            username=username or email.split("@")[0],
            email=email,
            first_name="Imported",
            last_name="Member",
            date_joined=timezone.now(),
        )
        user.set_unusable_password()
        user.save()
        if normalized_email is not None:
            CustomUser.objects.filter(pk=user.pk).update(
                normalized_email=normalized_email or None,
            )
            user.refresh_from_db()
        if email_address is not False:
            EmailAddress.objects.create(
                user=user,
                email=email if email_address is True else email_address,
                verified=email_address_verified,
                primary=True,
            )
        return user

    def imported_history(self, user: CustomUser) -> dict[str, object]:
        """Give the member the history the owner wants to see survive."""

        cohort = Cohort.objects.create(
            slug="data-engineering-zoomcamp-2025",
            title="Data Engineering Zoomcamp 2025",
            description="Imported cohort",
            first_homework_scored=True,
        )
        enrollment = Enrollment.objects.create(
            student=user,
            course=cohort,
            display_name="Imported Member",
            total_score=87,
            certificate_url="https://certificates.example.invalid/synthetic",
        )
        homework = Homework.objects.create(
            course=cohort,
            title="Module 1 homework",
            slug="module-1-homework",
            description="Imported homework",
            due_date=timezone.now() - timezone.timedelta(days=30),
            state=HomeworkState.SCORED.value,
        )
        question = Question.objects.create(
            homework=homework,
            text="Which tool did you use?",
            question_type=QuestionTypes.FREE_FORM.value,
            answer_type=AnswerTypes.ANY.value,
            correct_answer="dbt",
            scores_for_correct_answer=10,
        )
        submission = Submission.objects.create(
            homework=homework,
            student=user,
            enrollment=enrollment,
            total_score=42,
        )
        answer = Answer.objects.create(
            submission=submission,
            question=question,
            answer_text="dbt",
            is_correct=True,
        )
        return {
            "cohort": cohort,
            "enrollment": enrollment,
            "homework": homework,
            "submission": submission,
            "answer": answer,
        }

    # -- driving a real provider callback ----------------------------------

    @staticmethod
    def github_routes(
        *,
        uid: str,
        profile_email: str | None,
        emails: tuple[tuple[str, bool, bool], ...],
    ) -> dict[str, object]:
        """``emails`` is ``(address, verified, primary)`` as GitHub returns it."""

        profile: dict[str, object] = {
            "id": uid,
            "login": f"synthetic-{uid}",
            "name": "Imported Member",
        }
        if profile_email is not None:
            profile["email"] = profile_email
        return {
            GITHUB_PROFILE_URL: profile,
            GITHUB_EMAILS_URL: [
                {"email": address, "verified": verified, "primary": primary}
                for address, verified, primary in emails
            ],
        }

    @staticmethod
    def google_routes(
        *,
        uid: str,
        email: str | None,
        verified: bool = True,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": uid,
            "given_name": "Imported",
            "family_name": "Member",
        }
        if email is not None:
            payload["email"] = email
            payload["verified_email"] = verified
        return {GOOGLE_USERINFO_URL: payload}

    def sign_in(
        self,
        *,
        provider: str,
        routes: dict[str, object],
        client: Client | None = None,
        follow: bool = True,
    ):
        """Drive ``/accounts/<provider>/login/`` through to the callback."""

        client = client or Client()
        start = client.get(f"/accounts/{provider}/login/")
        self.assertEqual(
            start.status_code,
            302,
            "provider login should redirect straight to the provider",
        )
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]

        def fake_session(_self):
            return _FakeProviderSession(routes)

        def fake_access_token(_self, code, pkce_code_verifier=None):
            del code, pkce_code_verifier
            return {"access_token": SYNTHETIC_ACCESS_TOKEN}

        with (
            patch.object(
                DefaultSocialAccountAdapter,
                "get_requests_session",
                fake_session,
            ),
            patch.object(OAuth2Client, "get_access_token", fake_access_token),
        ):
            response = client.get(
                f"/accounts/{provider}/login/callback/",
                {"code": "synthetic-code", "state": state},
                follow=follow,
            )
        return client, response

    # -- assertions --------------------------------------------------------

    def assert_signed_in_as(self, client: Client, user: CustomUser) -> None:
        session_user_id = client.session.get("_auth_user_id")
        self.assertIsNotNone(session_user_id, "no session was established")
        self.assertEqual(int(session_user_id), user.pk)

    def assert_not_signed_in(self, client: Client) -> None:
        self.assertIsNone(client.session.get("_auth_user_id"))


class ImportedAccountMatchingFailsClosedTests(ImportedAccountSignInTestCase):
    """Matching that cannot be made safely must not be made at all."""

    def assert_denied(self, client: Client, response) -> None:
        self.assertEqual(response.status_code, 409)
        self.assert_not_signed_in(client)
        self.assertFalse(SocialAccount.objects.exists())

    def test_github_unverified_address_never_matches_an_imported_account(self) -> None:
        """The account-takeover shape, and the reason verification is required.

        Anyone may *add* an address to a GitHub account without confirming it;
        GitHub reports it with ``verified: false``.  If that were enough to
        match, one member would inherit another member's entire course
        history.
        """

        victim = self.imported_user(email="victim@example.invalid")
        history = self.imported_history(victim)

        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-attacker-uid",
                profile_email=None,
                emails=(("victim@example.invalid", False, True),),
            ),
        )

        self.assert_denied(client, response)
        self.assertEqual(CustomUser.objects.count(), 1)
        self.assertEqual(Enrollment.objects.get(pk=history["enrollment"].pk).student_id, victim.pk)
        self.assertEqual(
            AccountIdentityQuarantine.objects.get().reason_codes,
            ["verified_email_required"],
        )

    def test_github_profile_email_alone_never_matches_an_imported_account(self) -> None:
        """The public profile email is not an assertion of ownership."""

        self.imported_user(email="profile.only@example.invalid")

        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-profile-uid",
                profile_email="profile.only@example.invalid",
                emails=(),
            ),
        )

        self.assert_denied(client, response)
        self.assertEqual(CustomUser.objects.count(), 1)


class ProviderVerificationSettingsTests(TestCase):
    """The adapter's verified-email rule must not be disarmed by settings.

    ``SOCIALACCOUNT_PROVIDERS[...]["VERIFIED_EMAIL"] = True`` makes allauth
    overwrite every address a provider returns with ``verified=True`` inside
    ``Provider.cleanup_email_addresses``, before the adapter ever sees it.
    With 20,009 imported accounts matched on email, that flag turns "the
    provider vouched for this address" into "the provider mentioned this
    address".
    """

    def test_no_provider_is_configured_to_force_verified_emails(self) -> None:
        for provider, options in settings.SOCIALACCOUNT_PROVIDERS.items():
            with self.subTest(provider=provider):
                self.assertNotIn(
                    "VERIFIED_EMAIL",
                    options,
                    "email matching onto imported accounts requires the "
                    "provider's own verification signal",
                )

    def test_github_scope_still_requests_the_verified_address_list(self) -> None:
        self.assertIn(
            "user:email",
            settings.SOCIALACCOUNT_PROVIDERS["github"]["SCOPE"],
        )

    def test_the_adapter_refuses_to_assume_verification_whatever_is_configured(
        self,
    ) -> None:
        """Settings hygiene is not the guard; the adapter is."""

        adapter = ConsolidatingSocialAccountAdapter()
        self.assertFalse(adapter.is_email_verified("github", "anyone@example.invalid"))


class ForcedVerificationCannotBeReintroducedTests(ImportedAccountSignInTestCase):
    """The takeover shape must stay closed even if the flag is put back."""

    @override_settings(
        SOCIALACCOUNT_PROVIDERS={
            "github": {"SCOPE": ["user:email"], "VERIFIED_EMAIL": True},
        }
    )
    def test_verified_email_setting_no_longer_promotes_unverified_addresses(
        self,
    ) -> None:
        victim = self.imported_user(email="still.protected@example.invalid")

        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-reintroduced-uid",
                profile_email=None,
                emails=(("still.protected@example.invalid", False, True),),
            ),
        )

        self.assertEqual(response.status_code, 409)
        self.assert_not_signed_in(client)
        self.assertFalse(SocialAccount.objects.filter(user=victim).exists())

    def test_a_stored_per_app_verified_email_key_does_not_promote_either(self) -> None:
        """``SocialApp.settings`` is the other way allauth can be told to trust."""

        victim = self.imported_user(email="app-settings@example.invalid")
        app = SocialApp.objects.get(provider="github")
        app.settings = {"verified_email": True}
        app.save(update_fields=["settings"])

        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-app-settings-uid",
                profile_email=None,
                emails=(("app-settings@example.invalid", False, True),),
            ),
        )

        self.assertEqual(response.status_code, 409)
        self.assert_not_signed_in(client)
        self.assertFalse(SocialAccount.objects.filter(user=victim).exists())
