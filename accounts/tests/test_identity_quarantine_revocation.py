"""Quarantine revokes existing access, not only new logins (audit BE-08).

Setting ``identity_state`` to ``quarantined`` on an account that already holds
a browser session, a legacy API token, or a human management credential must
deny that path on its next use — with the same generic denial each path
already uses, and no identifying fields in any denial. Alias continuity still
works for an eligible survivor and still fails for a quarantined one.
"""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.auth import token_required
from accounts.identity_resolution import resolve_durable_user
from accounts.models import AccountIdentityAlias, CustomUser, Token
from accounts.studio_authorization import (
    StudioAuthorizationDenied,
    authorize_studio_request,
)
from accounts.studio_sessions import create_staff_session
from accounts.studio_test_support import make_studio_user
from management_api.authentication import authenticate as authenticate_api
from management_api.errors import APIError
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal
from management_auth.tokens import encode_secret, generate_token

QUARANTINED = CustomUser.IdentityState.QUARANTINED


def quarantine(user: CustomUser) -> None:
    CustomUser.objects.filter(pk=user.pk).update(identity_state=QUARANTINED)
    user.refresh_from_db()


class BrowserSessionRevocationTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            username="session-owner",
            email="session-owner@example.invalid",
            password="tested-password-1",
        )

    def login_and_home(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("course_list"))

    def test_a_quarantined_session_is_flushed_on_its_next_request(self) -> None:
        response = self.login_and_home()
        self.assertTrue(response.wsgi_request.user.is_authenticated)

        quarantine(self.user)
        response = self.client.get(reverse("course_list"))

        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_an_eligible_session_is_untouched(self) -> None:
        response = self.login_and_home()

        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertIn(SESSION_KEY, self.client.session)

    def test_a_quarantined_account_cannot_log_in(self) -> None:
        quarantine(self.user)

        logged_in = self.client.login(
            username="session-owner",
            password="tested-password-1",
        )

        self.assertFalse(logged_in)


class AliasContinuityTests(TestCase):
    def make_pair(self, *, survivor_state: str) -> tuple[CustomUser, CustomUser]:
        source = CustomUser.objects.create_user(
            username="absorbed-source",
            email="source@example.invalid",
        )
        source.identity_state = CustomUser.IdentityState.ABSORBED
        source.save(update_fields=["identity_state"])
        survivor = CustomUser.objects.create_user(
            username="alias-survivor",
            email="survivor@example.invalid",
        )
        survivor.identity_state = survivor_state
        survivor.save(update_fields=["identity_state"])
        AccountIdentityAlias.objects.create(source_user_id=source.pk, survivor=survivor)
        return source, survivor

    def test_an_absorbed_identity_still_resolves_to_an_active_survivor(self) -> None:
        source, survivor = self.make_pair(survivor_state=CustomUser.IdentityState.ACTIVE)

        self.assertEqual(resolve_durable_user(source), survivor)

    def test_an_absorbed_identity_never_resolves_to_a_quarantined_survivor(
        self,
    ) -> None:
        source, _survivor = self.make_pair(survivor_state=QUARANTINED)

        self.assertIsNone(resolve_durable_user(source))


class LegacyTokenRevocationTests(TestCase):
    def setUp(self) -> None:
        self.user = CustomUser.objects.create_user(
            username="token-owner",
            email="token-owner@example.invalid",
        )
        self.token = Token.objects.create(user=self.user, key="q" * 40)
        self.factory = RequestFactory()

        @token_required
        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse("through")

        self.view = view

    def request(self) -> HttpRequest:
        return self.factory.post("/private/", HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_a_live_token_still_authenticates(self) -> None:
        self.assertEqual(self.view(self.request()).status_code, 200)

    def test_a_quarantined_token_owner_is_denied_with_the_generic_response(
        self,
    ) -> None:
        quarantine(self.user)

        response = self.view(self.request())

        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)
        # No identifying fields reach the denial.
        self.assertEqual(response.content.decode(), '{"error": "Invalid token"}')


class ManagementCredentialRevocationTests(TestCase):
    def setUp(self) -> None:
        self.user = CustomUser.objects.create_user(
            username="credential-owner",
            email="credential-owner@example.invalid",
        )
        self.principal = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="quarantine-revocation-fixture",
            identity_snapshot="quarantine-revocation-fixture",
            user=self.user,
        )
        generated = generate_token()
        self.raw_token = generated.raw
        self.credential = APICredential.objects.create(
            principal=self.principal,
            name="quarantine-revocation-fixture",
            prefix=generated.prefix,
            secret_digest=encode_secret(generated.secret),
            digest_algorithm="pbkdf2_sha256",
            digest_version=1,
            scopes=["studio.audit.read"],
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.factory = RequestFactory()

    def request(self) -> HttpRequest:
        return self.factory.get("/api/v1/admin/", HTTP_AUTHORIZATION=f"Bearer {self.raw_token}")

    def test_a_live_human_credential_authenticates(self) -> None:
        identity = authenticate_api(self.request())

        self.assertEqual(identity.principal.pk, self.principal.pk)

    def test_a_quarantined_owner_cannot_authenticate_a_human_credential(
        self,
    ) -> None:
        quarantine(self.user)

        with self.assertRaises(APIError):
            authenticate_api(self.request())


class StudioAuthorityRevocationTests(TestCase):
    def test_a_quarantined_staff_account_loses_studio_authority(self) -> None:
        from management_registry import CAPABILITY_REGISTRY

        user = make_studio_user(username="quarantined-staffer", roles=("site_admin",))
        store = SessionStore()
        store.create()
        # create_staff_session only reads request.session; a bare factory
        # request carries it without involving session middleware.
        request = RequestFactory().get("/")
        request.session = store
        session = create_staff_session(user=user, request=request)
        capability = CAPABILITY_REGISTRY.require("studio.home.read")

        # Authorized before quarantine.
        authorize_studio_request(
            request_user=user,
            session_reference=session.id,
            capability=capability,
        )

        quarantine(user)
        with self.assertRaises(StudioAuthorizationDenied):
            authorize_studio_request(
                request_user=user,
                session_reference=session.id,
                capability=capability,
            )
