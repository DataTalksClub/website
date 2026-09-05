from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.models import Session
from django.db import IntegrityError, transaction
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
)
from django.utils import timezone

from accounts.auth import ConsolidatingSocialAccountAdapter
from accounts.identity_inventory import account_inventory
from accounts.identity_resolution import resolve_durable_user_id
from accounts.models import (
    AccountIdentityAlias,
    AccountIdentityQuarantine,
    CustomUser,
)
from accounts.navigation import SAFE_ACCOUNT_DESTINATION, safe_next_path
from accounts.studio_roles import synchronize_studio_roles
from core.models import AuditEvent
from management_api.authentication import authenticate as authenticate_management
from management_auth.constants import DIGEST_ALGORITHM, DIGEST_VERSION
from management_auth.models import APICredential, APIPrincipal
from management_auth.tokens import encode_secret, generate_token
from review_import.manifest import is_sensitive_table

TRANSITION_BYPASS_NEXT_VALUES = (
    "/courses/../accounts/continue/",
    "/courses/%2e%2e/accounts/login/",
    "/courses/%2E%2e/accounts/logout/",
    "/courses/.%2E/accounts/github/login/callback/",
    "/courses/%2e./accounts/github/login/",
    "/courses/%252e%252e/accounts/continue/",
    "%2Fcourses%2F%252E%252e%2Faccounts%2Flogin%2F",
    "/courses\\..\\accounts\\logout\\",
    "/courses/%5c..%5caccounts%5ccontinue/",
    "/courses/%255C..%255caccounts%255clogin/",
    "/courses//../accounts//logout/",
    "../../accounts/login/",
    "//testserver/accounts/login/",
    "\\\\testserver\\accounts\\login\\",
    "/%2ftestserver/accounts/logout/",
    "https://attacker.invalid/accounts/login/",
    "https%3A%2F%2Fattacker.invalid/accounts/login/",
    "/courses/%0d%0a/accounts/login/",
    "/courses/%ZZ/accounts/login/",
)


def create_verified_user(
    *,
    username: str,
    email: str,
    verified_email: str | None = None,
    password: str = "synthetic-password",
    **fields,
) -> CustomUser:
    user = CustomUser.objects.create_user(
        username=username,
        email=email,
        password=password,
        **fields,
    )
    EmailAddress.objects.create(
        user=user,
        email=verified_email or email,
        verified=True,
        primary=True,
    )
    return user


class SingleIdentityModelTests(TestCase):
    def test_adopted_user_and_table_identity_remain_authoritative(self) -> None:
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.CustomUser")
        self.assertEqual(CustomUser._meta.db_table, "accounts_customuser")
        self.assertEqual(
            settings.AUTHENTICATION_BACKENDS, ["accounts.backends.DurableAccountBackend"]
        )

    def test_email_normalization_is_expand_only_and_active_identity_is_unique(self) -> None:
        first = CustomUser.objects.create_user(
            username="first",
            email="  Learner@Example.Invalid ",
        )
        second = CustomUser.objects.create_user(
            username="second",
            email="learner@example.invalid",
        )
        self.assertEqual(first.normalized_email, "learner@example.invalid")
        self.assertEqual(second.normalized_email, "learner@example.invalid")
        self.assertEqual(first.identity_state, CustomUser.IdentityState.LEGACY)

        first.identity_state = CustomUser.IdentityState.ACTIVE
        first.save(update_fields=("identity_state",))
        second.identity_state = CustomUser.IdentityState.ACTIVE
        with self.assertRaises(IntegrityError), transaction.atomic():
            second.save(update_fields=("identity_state",))

    def test_alias_resolves_old_id_without_replacing_source_row(self) -> None:
        source = CustomUser.objects.create_user(username="source")
        survivor = CustomUser.objects.create_user(username="survivor")
        AccountIdentityAlias.objects.create(
            source_user_id=source.pk,
            survivor=survivor,
            source_snapshot_id="a" * 64,
            mapping_checksum="b" * 64,
            review_reference="synthetic-review-100",
        )

        self.assertEqual(resolve_durable_user_id(source.pk), survivor.pk)
        self.assertTrue(CustomUser.objects.filter(pk=source.pk).exists())


class SafeNextCanonicalizationTests(SimpleTestCase):
    factory = RequestFactory()

    def safe_next(
        self,
        candidate: str,
        *,
        path: str = "/accounts/continue/",
    ) -> str:
        query = urlencode({"next": candidate})
        request = self.factory.get(f"{path}?{query}")
        return safe_next_path(request)

    def test_browser_equivalent_transition_bypasses_fall_back(self) -> None:
        for candidate in TRANSITION_BYPASS_NEXT_VALUES:
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    self.safe_next(candidate),
                    SAFE_ACCOUNT_DESTINATION,
                )

    def test_legitimate_local_paths_keep_query_and_fragment(self) -> None:
        cases = (
            (
                "/courses/ai-dev-tools/?tab=overview&view=full#module-1",
                "/courses/ai-dev-tools/?tab=overview&view=full#module-1",
            ),
            (
                "/courses/guides/../ai-dev-tools//?tab=overview#module-1",
                "/courses/ai-dev-tools/?tab=overview#module-1",
            ),
            (
                "../../courses/?q=one%20two#catalog",
                "/courses/?q=one%20two#catalog",
            ),
            (
                "/blog/post.html?utm_source=account#faq",
                "/blog/post.html?utm_source=account#faq",
            ),
        )
        for candidate, expected in cases:
            with self.subTest(candidate=candidate):
                self.assertEqual(self.safe_next(candidate), expected)

    def test_query_only_reference_is_allowed_off_transition_pages(self) -> None:
        self.assertEqual(
            self.safe_next("?tab=overview#module", path="/courses/"),
            "/courses/?tab=overview#module",
        )

    def test_absent_or_self_reference_on_transition_page_falls_back(self) -> None:
        request = self.factory.get("/accounts/continue/")
        self.assertEqual(
            safe_next_path(request),
            SAFE_ACCOUNT_DESTINATION,
        )
        self.assertEqual(
            self.safe_next("?tab=still-self"),
            SAFE_ACCOUNT_DESTINATION,
        )


class DurableAuthenticationTests(TestCase):
    def test_legacy_username_login_remains_compatible(self) -> None:
        user = CustomUser.objects.create_user(
            username="legacy-login",
            email="legacy@example.invalid",
            password="synthetic-password",
        )

        authenticated = authenticate(
            username="legacy-login",
            password="synthetic-password",
        )

        self.assertEqual(authenticated.pk, user.pk)

    def test_duplicate_normalized_email_fails_closed_even_when_one_password_matches(self) -> None:
        CustomUser.objects.create_user(
            username="first",
            email="Duplicate@Example.Invalid",
            password="matching-password",
        )
        CustomUser.objects.create_user(
            username="second",
            email="duplicate@example.invalid",
            password="different-password",
        )

        authenticated = authenticate(
            email="duplicate@example.invalid",
            password="matching-password",
        )

        self.assertIsNone(authenticated)

    def test_email_collision_cannot_fall_through_to_matching_username(self) -> None:
        CustomUser.objects.create_user(
            username="collision@example.invalid",
            email="first@example.invalid",
            password="known-pass",
        )
        CustomUser.objects.create_user(
            username="other",
            email="collision@example.invalid",
            password="other-pass",
        )

        self.assertIsNone(
            authenticate(
                username="collision@example.invalid",
                password="known-pass",
            )
        )

    def test_quarantined_account_cannot_authenticate(self) -> None:
        user = CustomUser.objects.create_user(
            username="quarantined",
            email="quarantined@example.invalid",
            password="synthetic-password",
        )
        user.identity_state = CustomUser.IdentityState.QUARANTINED
        user.save(update_fields=("identity_state",))

        self.assertIsNone(
            authenticate(
                username="quarantined",
                password="synthetic-password",
            )
        )


class SocialLinkingTests(TestCase):
    @staticmethod
    def anonymous_request(path: str):
        request = RequestFactory().get(path)
        request.user = AnonymousUser()
        return request

    def social_login(
        self,
        *,
        email: str,
        verified: bool,
        provider: str = "github",
        uid: str = "synthetic-social-uid",
        extra_data: dict | None = None,
    ):
        account = SimpleNamespace(
            provider=provider,
            uid=uid,
            extra_data=extra_data or {},
        )
        return SimpleNamespace(
            account=account,
            user=None,
            is_existing=False,
            email_addresses=[SimpleNamespace(email=email, verified=verified)],
            connect=Mock(),
        )

    def test_verified_social_claim_connects_existing_account_only(self) -> None:
        user = create_verified_user(
            username="returning",
            email="returning@example.invalid",
        )
        sociallogin = self.social_login(
            email="returning@example.invalid",
            verified=True,
            extra_data={"access_token": "not-logged"},
        )
        before_users = CustomUser.objects.count()

        ConsolidatingSocialAccountAdapter().pre_social_login(
            self.anonymous_request("/accounts/github/login/callback/"),
            sociallogin,
        )

        sociallogin.connect.assert_called_once()
        connected_user = sociallogin.connect.call_args.args[1]
        self.assertEqual(connected_user.pk, user.pk)
        self.assertEqual(CustomUser.objects.count(), before_users)
        self.assertFalse(AccountIdentityQuarantine.objects.exists())
        audit = AuditEvent.objects.get(action="accounts.identity.link_succeeded")
        rendered = json.dumps(audit.metadata, sort_keys=True)
        self.assertNotIn("returning@example.invalid", rendered)
        self.assertNotIn("not-logged", rendered)

    def test_stale_identity_state_fails_closed_before_social_linking(self) -> None:
        user = create_verified_user(
            username="stale-returning",
            email="stale-returning@example.invalid",
        )
        sociallogin = self.social_login(
            email="stale-returning@example.invalid",
            verified=True,
        )

        def change_identity_after_snapshot(*, email, user_id):
            del email
            CustomUser.objects.filter(pk=user_id).update(
                identity_state=CustomUser.IdentityState.QUARANTINED,
            )
            return False

        with (
            patch(
                "accounts.auth._has_unresolved_email_collision",
                side_effect=change_identity_after_snapshot,
            ),
            self.assertRaises(ImmediateHttpResponse) as raised,
        ):
            ConsolidatingSocialAccountAdapter().pre_social_login(
                self.anonymous_request("/accounts/github/login/callback/"),
                sociallogin,
            )

        self.assertEqual(raised.exception.response.status_code, 409)
        sociallogin.connect.assert_not_called()
        user.refresh_from_db()
        self.assertEqual(user.identity_state, CustomUser.IdentityState.LEGACY)
        quarantine = AccountIdentityQuarantine.objects.get()
        self.assertEqual(quarantine.reason_codes, ["normalized_email_conflict"])

    def test_each_supported_provider_connects_the_existing_durable_account(self) -> None:
        for provider in ("github", "google", "slack"):
            with self.subTest(provider=provider):
                email = f"{provider}@example.invalid"
                user = create_verified_user(
                    username=f"{provider}-returning",
                    email=email,
                )
                sociallogin = self.social_login(
                    email=email,
                    verified=True,
                    provider=provider,
                    uid=f"synthetic-{provider}-uid",
                )

                ConsolidatingSocialAccountAdapter().pre_social_login(
                    self.anonymous_request(f"/accounts/{provider}/login/callback/"),
                    sociallogin,
                )

                self.assertEqual(sociallogin.connect.call_args.args[1].pk, user.pk)

    def test_unverified_claim_is_denied_without_account_creation(self) -> None:
        sociallogin = self.social_login(
            email="unverified@example.invalid",
            verified=False,
            extra_data={"token": "must-not-leak"},
        )

        with self.assertRaises(ImmediateHttpResponse) as raised:
            ConsolidatingSocialAccountAdapter().pre_social_login(
                self.anonymous_request("/accounts/github/login/callback/"),
                sociallogin,
            )

        self.assertEqual(raised.exception.response.status_code, 409)
        self.assertEqual(CustomUser.objects.count(), 0)
        sociallogin.connect.assert_not_called()
        quarantine = AccountIdentityQuarantine.objects.get()
        rendered = json.dumps(
            {
                "reason_codes": quarantine.reason_codes,
                "source_user_ids": quarantine.source_user_ids,
                "fingerprint": quarantine.fingerprint,
            },
            sort_keys=True,
        )
        self.assertNotIn("unverified@example.invalid", rendered)
        self.assertNotIn("must-not-leak", rendered)

    def test_ambiguous_verified_case_variant_never_uses_last_login(self) -> None:
        first = create_verified_user(
            username="first",
            email="Case@Example.Invalid",
            verified_email="Case@Example.Invalid",
        )
        second = create_verified_user(
            username="second",
            email="case@example.invalid",
            verified_email="case@example.invalid",
        )
        sociallogin = self.social_login(
            email="case@example.invalid",
            verified=True,
        )

        with self.assertRaises(ImmediateHttpResponse):
            ConsolidatingSocialAccountAdapter().pre_social_login(
                self.anonymous_request("/accounts/github/login/callback/"),
                sociallogin,
            )

        sociallogin.connect.assert_not_called()
        self.assertEqual(
            AccountIdentityQuarantine.objects.get().source_user_ids,
            [first.pk, second.pk],
        )


class SharedAccountSurfaceTests(TestCase):
    def test_login_heading_matches_deployed_smoke_contract(self) -> None:
        """The one h1 the deployed smoke and every browser flow look for.

        The page moved to the design system (issue #179), so the heading no longer
        carries the adopted shell's utility classes or its icon.  What the
        smoke contract actually depends on is unchanged and is what this
        asserts: a single level-one heading whose whole accessible name is
        ``Sign In``.
        """

        response = self.client.get("/accounts/login/")
        body = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(body, r"<h1[^>]*>\s*Sign In\s*</h1>")
        self.assertEqual(body.count("<h1"), 1)

    def test_signed_out_shell_uses_one_same_host_login_and_no_account_rows(self) -> None:
        identity_counts = {
            "users": CustomUser.objects.count(),
            "emails": EmailAddress.objects.count(),
            "social": SocialAccount.objects.count(),
            "sessions": Session.objects.count(),
            "aliases": AccountIdentityAlias.objects.count(),
        }

        response = self.client.get("/")

        self.assertContains(response, 'href="/accounts/login/?next=%2F"')
        self.assertNotContains(response, "courses.datatalks.club/accounts")
        self.assertEqual(
            identity_counts,
            {
                "users": CustomUser.objects.count(),
                "emails": EmailAddress.objects.count(),
                "social": SocialAccount.objects.count(),
                "sessions": Session.objects.count(),
                "aliases": AccountIdentityAlias.objects.count(),
            },
        )

    def test_signed_in_public_course_settings_and_api_share_account_id(self) -> None:
        user = create_verified_user(
            username="shared-account",
            email="shared@example.invalid",
        )
        self.client.force_login(user)

        for path in ("/", "/courses", "/accounts/settings/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'data-account-id="{user.pk}"')
                self.assertContains(response, "Account settings")
                # One account destination: sign-in methods are a section of
                # account settings, not a second menu entry to their own page.
                self.assertNotContains(response, "Login connections")
                self.assertIn("private", response["Cache-Control"])
                self.assertIn("no-store", response["Cache-Control"])
        identity = self.client.get("/api/v1/account/identity/")
        self.assertEqual(identity.json()["account_id"], user.pk)
        self.assertEqual(identity.json()["auth_user_model"], "accounts.CustomUser")

    def test_identity_apis_deny_generically_without_cross_account_data(self) -> None:
        session_response = self.client.get("/api/v1/account/identity/")
        token_response = self.client.get("/api/account/identity/")

        self.assertEqual(session_response.status_code, 401)
        self.assertEqual(
            session_response.json(),
            {"error": "Authentication required"},
        )
        self.assertEqual(token_response.status_code, 401)
        self.assertEqual(token_response.json(), {"error": "Authentication token required"})
        rendered = json.dumps(
            [session_response.json(), token_response.json()],
            sort_keys=True,
        )
        self.assertNotIn("account_id", rendered)

    def test_studio_navigation_uses_capability_policy_not_is_staff_alone(self) -> None:
        staff = create_verified_user(
            username="synthetic-staff",
            email="staff@example.invalid",
            is_staff=True,
        )
        self.client.force_login(staff)
        without_capability = self.client.get("/")
        self.assertNotContains(without_capability, 'href="/studio/"')

        roles = {group.name: group for group in synchronize_studio_roles()}
        staff.groups.add(roles["site_admin"])
        with_capability = self.client.get("/")
        self.assertContains(with_capability, 'href="/studio/"')

    def test_explicit_reauthentication_has_safe_next_and_no_credential(self) -> None:
        first = self.client.get("/accounts/continue/?next=/courses/durable-identity/")
        replay = self.client.get("/accounts/continue/?next=/courses/durable-identity/")

        expected = "http://testserver/accounts/login/?next=%2Fcourses%2Fdurable-identity%2F"
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first["Location"], expected)
        self.assertEqual(replay["Location"], expected)
        self.assertEqual(first["Referrer-Policy"], "same-origin")
        for forbidden in ("token=", "code=", "credential=", "session="):
            self.assertNotIn(forbidden, first["Location"].casefold())

        for path in (
            "/accounts/continue/",
            "/accounts/continue/?next=/accounts/continue/",
            "/accounts/continue/?next=%2Faccounts%2Fcontinue%2F",
            "/accounts/continue/?next=%252Faccounts%252Fcontinue%252F",
            "/accounts/continue/?next=/accounts/login/",
            "/accounts/continue/?next=/accounts/logout/",
            "/accounts/continue/?next=/accounts/github/login/",
            "/accounts/continue/?next=/accounts/github/login/callback/",
            "/accounts/continue/?next=https://attacker.invalid/collect",
        ):
            with self.subTest(path=path):
                unsafe = self.client.get(path)
                self.assertEqual(
                    unsafe["Location"],
                    "http://testserver/accounts/login/?next=%2F",
                )

        for candidate in TRANSITION_BYPASS_NEXT_VALUES:
            with self.subTest(candidate=candidate):
                unsafe = self.client.get(
                    "/accounts/continue/",
                    {"next": candidate},
                )
                self.assertEqual(
                    unsafe["Location"],
                    "http://testserver/accounts/login/?next=%2F",
                )

        legitimate = self.client.get(
            "/accounts/continue/",
            {"next": ("/courses/guides/../durable-identity//?tab=overview#module-1")},
        )
        login_query = parse_qs(urlsplit(legitimate["Location"]).query)
        self.assertEqual(
            login_query["next"],
            ["/courses/durable-identity/?tab=overview#module-1"],
        )

    def test_authenticated_continuity_returns_directly_with_same_account(self) -> None:
        user = create_verified_user(
            username="continuity-account",
            email="continuity-account@example.invalid",
        )
        self.client.force_login(user)

        intended = self.client.get("/accounts/continue/?next=/courses/durable-identity/")
        fallback = self.client.get("/accounts/continue/?next=/accounts/login/")

        self.assertEqual(
            intended["Location"],
            "/courses/durable-identity/",
        )
        self.assertEqual(fallback["Location"], "/")
        for location in (intended["Location"], fallback["Location"]):
            self.assertNotIn("/accounts/login/", location)
            self.assertNotIn("/accounts/continue/", location)

        for candidate in TRANSITION_BYPASS_NEXT_VALUES:
            with self.subTest(candidate=candidate):
                bypass = self.client.get(
                    "/accounts/continue/",
                    {"next": candidate},
                )
                self.assertEqual(bypass["Location"], "/")

        legitimate = self.client.get(
            "/accounts/continue/",
            {"next": ("/courses/guides/../durable-identity//?tab=overview#module-1")},
        )
        self.assertEqual(
            legitimate["Location"],
            "/courses/durable-identity/?tab=overview#module-1",
        )
        identity = self.client.get("/api/v1/account/identity/")
        self.assertEqual(identity.json()["account_id"], user.pk)

    def test_content_only_review_tables_classify_new_identity_rows_as_sensitive(self) -> None:
        for table in (
            "accounts_accountidentityalias",
            "accounts_accountidentityquarantine",
            "accounts_accountreconciliationrun",
        ):
            with self.subTest(table=table):
                self.assertTrue(is_sensitive_table(table))

    def test_inventory_covers_fields_relations_routes_and_session_boundary(self) -> None:
        inventory = account_inventory()

        self.assertEqual(inventory["auth_user_model"], "accounts.CustomUser")
        self.assertEqual(inventory["user_table"], "accounts_customuser")
        self.assertEqual(len(inventory["dependent_relations"]), 20)
        self.assertEqual(len(inventory["many_to_many_relations"]), 3)
        relation_keys = {
            f"{item['model_label']}.{item['field_name']}"
            for item in inventory["dependent_relations"]
        }
        self.assertIn("courses.Enrollment.student", relation_keys)
        self.assertIn("core.AuditEvent.actor", relation_keys)
        self.assertIn("management_auth.APIPrincipal.user", relation_keys)
        self.assertIn("socialaccount.SocialAccount.user", relation_keys)
        self.assertIsNone(inventory["session"]["cookie_domain"])
        self.assertEqual(
            inventory["session"]["cross_host_policy"],
            "explicit_reauthentication",
        )
        self.assertFalse(inventory["session"]["save_every_request"])
        authentication_paths = {item["path"] for item in inventory["authentication_routes"]}
        self.assertIn("/accounts/github/login/callback/", authentication_paths)
        self.assertIn("/accounts/google/login/callback/", authentication_paths)
        self.assertIn("/accounts/slack/login/callback/", authentication_paths)
        self.assertFalse(inventory["content_projection_account_creation"])
        self.assertEqual(len(inventory["inventory_checksum"]), 64)


class SessionLifecycleTests(TestCase):
    def test_ordinary_identity_release_preserves_the_existing_session(self) -> None:
        user = create_verified_user(
            username="session-continuity",
            email="session-continuity@example.invalid",
        )
        self.client.force_login(user)
        original_session_key = self.client.session.session_key
        user.preferred_timezone = "Europe/Berlin"
        user.save(update_fields=("preferred_timezone",))

        response = self.client.get("/api/v1/account/identity/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_id"], user.pk)
        self.assertEqual(self.client.session.session_key, original_session_key)

    def test_login_cycles_anonymous_session_key_and_logout_is_scoped(self) -> None:
        user = CustomUser.objects.create_user(
            username="fixation-check",
            email="fixation-check@example.invalid",
            password="synthetic-password",
        )
        anonymous_session = self.client.session
        anonymous_session["synthetic_pre_login_value"] = True
        anonymous_session.save()
        anonymous_key = anonymous_session.session_key
        other_client = Client()
        other_client.force_login(user)
        other_session_key = other_client.session.session_key

        self.assertTrue(
            self.client.login(
                username="fixation-check",
                password="synthetic-password",
            )
        )
        authenticated_key = self.client.session.session_key
        self.assertNotEqual(authenticated_key, anonymous_key)

        self.client.post("/accounts/logout/")

        self.assertFalse(Session.objects.filter(session_key=authenticated_key).exists())
        self.assertTrue(Session.objects.filter(session_key=other_session_key).exists())

    def test_password_change_disablement_and_expiry_fail_closed(self) -> None:
        password_user = CustomUser.objects.create_user(
            username="password-session",
            email="password-session@example.invalid",
            password="before-change",
        )
        password_client = Client()
        password_client.force_login(password_user)
        password_user.set_password("after-change")
        password_user.save(update_fields=("password",))
        self.assertEqual(
            password_client.get("/api/v1/account/identity/").status_code,
            401,
        )

        disabled_user = create_verified_user(
            username="disabled-session",
            email="disabled-session@example.invalid",
            is_staff=True,
        )
        disabled_client = Client()
        disabled_client.force_login(disabled_user)
        disabled_user.is_active = False
        disabled_user.save(update_fields=("is_active",))
        self.assertEqual(
            disabled_client.get("/api/v1/account/identity/").status_code,
            401,
        )

        expiring_user = create_verified_user(
            username="expired-session",
            email="expired-session@example.invalid",
        )
        expiring_client = Client()
        expiring_client.force_login(expiring_user)
        expiring_session = expiring_client.session
        expiring_session.set_expiry(-1)
        expiring_session.save()
        self.assertEqual(
            expiring_client.get("/api/v1/account/identity/").status_code,
            401,
        )


class ManagementIdentityParityTests(TestCase):
    def test_human_management_principal_uses_the_same_durable_account(self) -> None:
        user = create_verified_user(
            username="management-human",
            email="management-human@example.invalid",
            is_staff=True,
        )
        principal = APIPrincipal.objects.create(
            kind=APIPrincipal.Kind.HUMAN,
            name="Synthetic management human",
            identity_snapshot="synthetic-human-identity",
            user=user,
        )
        generated = generate_token()
        APICredential.objects.create(
            principal=principal,
            name="Synthetic management credential",
            prefix=generated.prefix,
            secret_digest=encode_secret(generated.secret),
            digest_algorithm=DIGEST_ALGORITHM,
            digest_version=DIGEST_VERSION,
            scopes=["studio.home.read"],
            expires_at=timezone.now() + timedelta(hours=1),
        )
        request = RequestFactory().get(
            "/api/v1/admin/health",
            HTTP_AUTHORIZATION=f"Bearer {generated.raw}",
        )

        identity = authenticate_management(request)

        self.assertEqual(identity.principal.user_id, user.pk)
        self.client.force_login(user)
        session_identity = self.client.get("/api/v1/account/identity/").json()
        self.assertEqual(session_identity["account_id"], user.pk)


class IdentityTemplateReadabilityTests(SimpleTestCase):
    structural_tags = (
        r"(?:article|aside|div|footer|form|h[1-6]|header|li|main|nav|ol|p|"
        r"section|table|tbody|td|th|thead|tr|ul)"
    )
    compressed_patterns = (
        re.compile(rf"</{structural_tags}>\s*<{structural_tags}\b"),
        re.compile(r"{%\s*(?:for|if|elif|else|empty|endif|endfor)\b[^%]*%}\s*<"),
        re.compile(
            rf"</{structural_tags}>\s*"
            r"{%\s*(?:endfor|endif|else|elif|empty)\b"
        ),
    )

    def test_identity_templates_are_line_broken_not_minified(self) -> None:
        root = Path(settings.BASE_DIR)
        template_paths = (
            root / "course_platform_templates/base.html",
            root / "accounts/templates/accounts/login.html",
            root / "course_platform_templates/socialaccount/identity_conflict.html",
            root / "course_platform_templates/accounts/account_settings.html",
        )
        failures = []
        for path in template_paths:
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if any(pattern.search(line) for pattern in self.compressed_patterns):
                    failures.append(f"{path.relative_to(root)}:{line_number}")
        self.assertEqual(
            failures,
            [],
            "Keep structural HTML and Django controls on separate source lines",
        )
