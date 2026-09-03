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
from django.db.models.signals import post_init
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.auth import (
    ConsolidatingSocialAccountAdapter,
    _has_unresolved_email_collision,
)
from accounts.models import (
    AccountIdentityAlias,
    AccountIdentityQuarantine,
    CustomUser,
)
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
SLACK_USERINFO_URL = "https://slack.com/api/openid.connect.userInfo"

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
    def slack_routes(
        *,
        uid: str,
        email: str | None,
        verified: bool = True,
    ) -> dict[str, object]:
        """Slack signs in through OpenID Connect, so the flag is top level."""

        payload: dict[str, object] = {
            "ok": True,
            "https://slack.com/team_id": "T-SYNTHETIC",
            "https://slack.com/user_id": uid,
        }
        if email is not None:
            payload["email"] = email
            payload["email_verified"] = verified
        return {SLACK_USERINFO_URL: payload}

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


class ImportedAccountMatchesOnVerifiedEmailTests(ImportedAccountSignInTestCase):
    """The launch-day journey, end to end, for each imported shape."""

    def assert_history_is_visible(
        self,
        client: Client,
        history: dict[str, object],
    ) -> None:
        """Assert through the pages a returning member would actually open."""

        cohort = history["cohort"]
        course_page = client.get(
            reverse(
                "course",
                kwargs={
                    "course_slug": cohort.course.slug,
                    "cohort_year": cohort.year,
                },
            )
        )
        self.assertEqual(course_page.status_code, 200)
        self.assertContains(course_page, "Your work in this course")
        self.assertContains(course_page, "enrolled")
        self.assertContains(course_page, "Total score")
        self.assertContains(course_page, "87")
        self.assertContains(course_page, "Download Certificate")
        self.assertContains(course_page, history["enrollment"].certificate_url)

        homework_page = client.get(
            reverse(
                "homework",
                kwargs={
                    "course_slug": cohort.course.slug,
                    "cohort_year": cohort.year,
                    "homework_slug": history["homework"].slug,
                },
            )
        )
        self.assertEqual(homework_page.status_code, 200)
        self.assertContains(homework_page, "Your submission has been graded")
        self.assertContains(homework_page, "42")

    def test_google_reunites_the_imported_account_with_its_history(self) -> None:
        user = self.imported_user(email="returning.member@example.invalid")
        history = self.imported_history(user)
        before = CustomUser.objects.count()

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-uid-1",
                email="returning.member@example.invalid",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assertEqual(CustomUser.objects.count(), before)
        self.assertEqual(
            SocialAccount.objects.get(provider="google").user_id,
            user.pk,
        )
        self.assertEqual(Enrollment.objects.get(student=user).pk, history["enrollment"].pk)
        self.assertEqual(Submission.objects.get(student=user).pk, history["submission"].pk)
        self.assert_history_is_visible(client, history)

    def test_github_reunites_the_imported_account_with_its_history(self) -> None:
        user = self.imported_user(email="returning.dev@example.invalid")
        history = self.imported_history(user)
        before = CustomUser.objects.count()

        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-uid-1",
                profile_email="returning.dev@example.invalid",
                emails=(("returning.dev@example.invalid", True, True),),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assertEqual(CustomUser.objects.count(), before)
        self.assert_history_is_visible(client, history)

    def test_account_without_any_email_address_row_still_matches(self) -> None:
        """The four exported accounts that carry no ``emailaddress`` row."""

        user = self.imported_user(
            email="no.emailaddress.row@example.invalid",
            email_address=False,
        )
        history = self.imported_history(user)

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-uid-2",
                email="no.emailaddress.row@example.invalid",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assertEqual(CustomUser.objects.count(), 1)
        self.assert_history_is_visible(client, history)

    def test_account_with_an_unverified_email_address_row_still_matches(self) -> None:
        """The one exported account whose ``emailaddress`` row is unverified.

        Our own row says nothing: ``ACCOUNT_EMAIL_VERIFICATION`` is ``none``,
        so the site never verified it either way.  Google's assertion is the
        signal, and it is present.
        """

        user = self.imported_user(
            email="unverified.row@example.invalid",
            email_address_verified=False,
        )
        history = self.imported_history(user)

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-uid-3",
                email="unverified.row@example.invalid",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assertEqual(CustomUser.objects.count(), 1)
        self.assert_history_is_visible(client, history)

    def test_a_row_less_account_also_matches_a_second_provider_later(self) -> None:
        """``connect()`` writes no ``EmailAddress`` row, so the next provider
        has to match on the account column again."""

        user = self.imported_user(
            email="row.less.two.providers@example.invalid",
            email_address=False,
        )
        history = self.imported_history(user)

        _, google_response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-row-less-uid",
                email="row.less.two.providers@example.invalid",
            ),
        )
        self.assertEqual(google_response.status_code, 200)
        self.assertFalse(EmailAddress.objects.filter(user=user).exists())

        client, github_response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-row-less-uid",
                profile_email=None,
                emails=(("row.less.two.providers@example.invalid", True, True),),
            ),
        )

        self.assertEqual(github_response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assertEqual(CustomUser.objects.count(), 1)
        self.assertEqual(SocialAccount.objects.filter(user=user).count(), 2)
        self.assert_history_is_visible(client, history)

    def test_bulk_insert_that_left_normalized_email_empty_still_matches(self) -> None:
        """A raw ``COPY``/``bulk_create`` never runs ``CustomUser.save()``."""

        user = self.imported_user(
            email="raw.insert@example.invalid",
            normalized_email="",
        )
        self.assertIsNone(user.normalized_email)
        history = self.imported_history(user)

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-uid-4",
                email="raw.insert@example.invalid",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assert_history_is_visible(client, history)

    def test_provider_address_case_does_not_split_the_account(self) -> None:
        """Eleven exported rows differ from the account email only by case."""

        user = self.imported_user(
            email="person@example.invalid",
            email_address="Person@Example.Invalid",
        )
        history = self.imported_history(user)

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-uid-5",
                email="Person@Example.Invalid",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assertEqual(CustomUser.objects.count(), 1)
        self.assert_history_is_visible(client, history)

    def test_plus_tagged_address_matches_only_its_own_account(self) -> None:
        """Eighteen exported addresses carry a plus tag; it is part of the address."""

        tagged = self.imported_user(
            email="member+zoomcamp@example.invalid",
            username="tagged",
        )
        untagged = self.imported_user(
            email="member@example.invalid",
            username="untagged",
        )
        history = self.imported_history(tagged)

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-uid-6",
                email="member+zoomcamp@example.invalid",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, tagged)
        self.assertFalse(SocialAccount.objects.filter(user=untagged).exists())
        self.assert_history_is_visible(client, history)

    def test_slack_reunites_the_imported_account_with_its_history(self) -> None:
        """1,046 of the exported accounts have Slack as their only provider."""

        user = self.imported_user(email="slack.only@example.invalid")
        history = self.imported_history(user)

        client, response = self.sign_in(
            provider="slack",
            routes=self.slack_routes(
                uid="slack-uid-1",
                email="slack.only@example.invalid",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assertEqual(CustomUser.objects.count(), 1)
        self.assert_history_is_visible(client, history)

    def test_second_provider_links_to_the_same_imported_account(self) -> None:
        user = self.imported_user(email="two.providers@example.invalid")
        history = self.imported_history(user)

        google_client, google_response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-uid-7",
                email="two.providers@example.invalid",
            ),
        )
        self.assertEqual(google_response.status_code, 200)
        self.assert_signed_in_as(google_client, user)

        github_client, github_response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-uid-7",
                profile_email="two.providers@example.invalid",
                emails=(("two.providers@example.invalid", True, True),),
            ),
        )

        self.assertEqual(github_response.status_code, 200)
        self.assert_signed_in_as(github_client, user)
        self.assertEqual(CustomUser.objects.count(), 1)
        self.assertEqual(
            sorted(
                SocialAccount.objects.filter(user=user).values_list(
                    "provider",
                    flat=True,
                )
            ),
            ["github", "google"],
        )
        self.assert_history_is_visible(github_client, history)

    def test_returning_after_the_first_link_reuses_the_stored_connection(self) -> None:
        user = self.imported_user(email="second.visit@example.invalid")
        history = self.imported_history(user)
        routes = self.google_routes(
            uid="google-uid-8",
            email="second.visit@example.invalid",
        )

        self.sign_in(provider="google", routes=routes)
        client, response = self.sign_in(provider="google", routes=routes)

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assertEqual(CustomUser.objects.count(), 1)
        self.assertEqual(SocialAccount.objects.count(), 1)
        self.assert_history_is_visible(client, history)

    def test_github_second_verified_address_does_not_block_the_match(self) -> None:
        """A work plus a personal verified address is an ordinary GitHub account.

        Only one of them belongs to an account here, so there is nothing
        ambiguous to fail closed on.
        """

        user = self.imported_user(email="work.address@example.invalid")
        history = self.imported_history(user)

        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-uid-9",
                profile_email=None,
                emails=(
                    ("aaa.personal@example.invalid", True, True),
                    ("work.address@example.invalid", True, False),
                ),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        self.assertEqual(CustomUser.objects.count(), 1)
        self.assert_history_is_visible(client, history)

    def test_the_matched_address_is_the_one_written_onto_the_account(self) -> None:
        """The account keeps its own address, not whichever sorts first."""

        user = self.imported_user(email="zoe.member@example.invalid")

        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-uid-10",
                profile_email=None,
                emails=(
                    ("aaa.other@example.invalid", True, True),
                    ("zoe.member@example.invalid", True, False),
                ),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assert_signed_in_as(client, user)
        user.refresh_from_db()
        self.assertEqual(user.email, "zoe.member@example.invalid")
        self.assertEqual(user.normalized_email, "zoe.member@example.invalid")
        self.assertEqual(user.identity_state, CustomUser.IdentityState.ACTIVE)


class ImportedAccountMatchingCostTests(ImportedAccountSignInTestCase):
    """Launch day is ~20,000 first-time links, all in the same few hours."""

    class _AccountRowCounter:
        """Count the ``CustomUser`` rows a block actually reads."""

        def __init__(self) -> None:
            self.rows = 0

        def _observe(self, **kwargs: object) -> None:
            self.rows += 1

        def __enter__(self) -> ImportedAccountMatchingCostTests._AccountRowCounter:
            post_init.connect(self._observe, sender=CustomUser)
            return self

        def __exit__(self, *exc_info: object) -> bool:
            post_init.disconnect(self._observe, sender=CustomUser)
            return False

    def test_the_collision_check_does_not_read_every_account(self) -> None:
        """It used to load all ~20,000 rows and compare them in Python.

        One query, but every row of the account table, on every first
        sign-in — and launch day is roughly 20,000 of them.
        """

        member = self.imported_user(email="in.a.crowd@example.invalid", username="crowded")
        for index in range(25):
            self.imported_user(
                email=f"crowd-{index}@example.invalid",
                username=f"crowd-{index}",
            )

        with self._AccountRowCounter() as counter:
            collided = _has_unresolved_email_collision(
                email="in.a.crowd@example.invalid",
                user_id=member.pk,
            )

        self.assertFalse(collided)
        self.assertEqual(counter.rows, 0)

    def test_rows_a_bulk_insert_left_unnormalized_are_still_compared(self) -> None:
        """The exact answer is preserved for rows that bypassed the model."""

        member = self.imported_user(email="normalized@example.invalid", username="normalized")
        self.imported_user(
            email="Normalized@Example.Invalid",
            username="raw-duplicate",
            normalized_email="",
        )

        self.assertTrue(
            _has_unresolved_email_collision(
                email="normalized@example.invalid",
                user_id=member.pk,
            )
        )


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

    def test_provider_that_returns_no_email_is_denied_without_creating_an_account(
        self,
    ) -> None:
        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-no-email-uid",
                profile_email=None,
                emails=(),
            ),
        )

        self.assert_denied(client, response)
        self.assertEqual(CustomUser.objects.count(), 0)
        self.assertEqual(
            AccountIdentityQuarantine.objects.get().reason_codes,
            ["verified_email_required"],
        )

    def test_google_unverified_address_is_denied(self) -> None:
        self.imported_user(email="google.unverified@example.invalid")

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-unverified-uid",
                email="google.unverified@example.invalid",
                verified=False,
            ),
        )

        self.assert_denied(client, response)
        self.assertEqual(CustomUser.objects.count(), 1)

    def test_slack_without_email_verified_is_denied(self) -> None:
        """Slack's OIDC ``email_verified`` is the whole signal for 1,046 accounts.

        If Slack ever stops asserting it, this is what those members meet —
        a 409, not a silent match.
        """

        self.imported_user(email="slack.unverified@example.invalid")

        client, response = self.sign_in(
            provider="slack",
            routes=self.slack_routes(
                uid="slack-unverified-uid",
                email="slack.unverified@example.invalid",
                verified=False,
            ),
        )

        self.assert_denied(client, response)
        self.assertEqual(CustomUser.objects.count(), 1)

    def test_unknown_verified_address_is_denied_rather_than_given_a_new_account(
        self,
    ) -> None:
        """Signup is closed, so an unmatched address must not silently enrol."""

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-stranger-uid",
                email="stranger@example.invalid",
            ),
        )

        self.assert_denied(client, response)
        self.assertEqual(CustomUser.objects.count(), 0)
        self.assertEqual(
            AccountIdentityQuarantine.objects.get().reason_codes,
            ["verified_owner_missing"],
        )

    def test_two_imported_accounts_sharing_an_address_are_denied(self) -> None:
        """The export carries exactly one such pair; a human must split it."""

        first = self.imported_user(
            email="shared@example.invalid",
            username="shared-one",
            email_address=False,
        )
        second = self.imported_user(
            email="shared@example.invalid",
            username="shared-two",
        )

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-shared-uid",
                email="shared@example.invalid",
            ),
        )

        self.assert_denied(client, response)
        quarantine = AccountIdentityQuarantine.objects.get()
        self.assertEqual(quarantine.reason_codes, ["verified_owner_ambiguous"])
        self.assertEqual(
            sorted(quarantine.source_user_ids),
            sorted([first.pk, second.pk]),
        )

    def test_verified_addresses_pointing_at_two_accounts_are_denied(self) -> None:
        first = self.imported_user(email="one.side@example.invalid", username="one-side")
        second = self.imported_user(email="other.side@example.invalid", username="other-side")

        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-two-owners-uid",
                profile_email=None,
                emails=(
                    ("one.side@example.invalid", True, True),
                    ("other.side@example.invalid", True, False),
                ),
            ),
        )

        self.assert_denied(client, response)
        quarantine = AccountIdentityQuarantine.objects.get()
        self.assertEqual(quarantine.reason_codes, ["verified_owner_ambiguous"])
        self.assertEqual(
            sorted(quarantine.source_user_ids),
            sorted([first.pk, second.pk]),
        )

    def test_an_absorbed_accounts_address_does_not_move_the_survivors_identity(
        self,
    ) -> None:
        """Reconciliation may leave an absorbed row holding the old address.

        Following the alias would sign the member in, but writing the absorbed
        account's address onto the survivor would rewrite the survivor's
        identity.  Fail closed instead.
        """

        survivor = self.imported_user(
            email="survivor@example.invalid",
            username="survivor",
        )
        absorbed = self.imported_user(
            email="absorbed@example.invalid",
            username="absorbed",
        )
        CustomUser.objects.filter(pk=absorbed.pk).update(
            identity_state=CustomUser.IdentityState.ABSORBED,
        )
        AccountIdentityAlias.objects.create(
            source_user_id=absorbed.pk,
            survivor=survivor,
            source_snapshot_id="a" * 64,
            mapping_checksum="b" * 64,
            review_reference="synthetic-review",
        )

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-absorbed-uid",
                email="absorbed@example.invalid",
            ),
        )

        self.assert_denied(client, response)
        survivor.refresh_from_db()
        self.assertEqual(survivor.email, "survivor@example.invalid")
        self.assertEqual(
            AccountIdentityQuarantine.objects.get().reason_codes,
            ["verified_owner_claim_mismatch"],
        )

    def test_deactivated_imported_account_is_not_handed_to_the_provider(self) -> None:
        user = self.imported_user(email="deactivated@example.invalid")
        CustomUser.objects.filter(pk=user.pk).update(is_active=False)

        client, response = self.sign_in(
            provider="google",
            routes=self.google_routes(
                uid="google-deactivated-uid",
                email="deactivated@example.invalid",
            ),
        )

        self.assert_denied(client, response)

    def test_denial_page_tells_the_member_what_to_do_next(self) -> None:
        client, response = self.sign_in(
            provider="github",
            routes=self.github_routes(
                uid="github-support-uid",
                profile_email=None,
                emails=(),
            ),
        )

        self.assertEqual(response.status_code, 409)
        self.assert_not_signed_in(client)
        body = response.content.decode()
        self.assertIn("support", body.lower())
        self.assertNotIn("verified_email_required", body)


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
