from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any, cast

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.development_owner import bootstrap_development_owner
from accounts.models import CustomUser
from accounts.studio_roles import MANAGE_API_CREDENTIALS
from core.bootstrap import RuntimeEnvironment
from core.models import AuditEvent
from management_api.concurrency import revision_etag
from management_api.openapi import generate_document, render_document
from management_auth.idempotency import hash_management_idempotency_key
from management_auth.models import (
    APICredential,
    APIPrincipal,
    APIRateAdmission,
    ManagementIdempotencyRecord,
)
from management_auth.services import issue_credential_once
from management_auth.tokens import parse_token, verify_secret

OWNER_EMAIL = "runtime-owner@example.test"
OWNER_PASSWORD = "runtime-owner-password-107"
TOKEN_PATTERN = re.compile(r"dtca_v1_[A-Za-z0-9_-]{16}_[A-Za-z0-9_-]{43}")


@override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST)
class RuntimeCredentialAPIBase(TestCase):
    def setUp(self) -> None:
        bootstrap_development_owner(
            email=OWNER_EMAIL,
            password=OWNER_PASSWORD,
            reset_password=False,
            allow_test=True,
        )
        self.human = APIPrincipal.objects.get(kind=APIPrincipal.Kind.HUMAN)
        self.service = APIPrincipal.objects.get(kind=APIPrincipal.Kind.SERVICE)
        actor_credential = issue_credential_once(
            actor_principal=self.human,
            target_principal_id=self.human.id,
            name="Runtime management actor",
            scopes=(
                "management.credentials.list",
                "management.credentials.create",
                "management.credentials.rotate",
                "management.credentials.revoke",
            ),
            idempotency_key="runtime-management-actor",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        self.actor_token = str(actor_credential.response["token"])

    def api_get(self, path: str, *, token: str | None = None, **extra):
        return self.client.get(
            path,
            HTTP_AUTHORIZATION=f"Bearer {token or self.actor_token}",
            **extra,
        )

    def api_post(
        self,
        path: str,
        payload: dict,
        *,
        key: str,
        token: str | None = None,
        revision: int | None = None,
    ):
        extra: dict[str, Any] = {
            "HTTP_AUTHORIZATION": f"Bearer {token or self.actor_token}",
            "HTTP_IDEMPOTENCY_KEY": key,
        }
        if revision is not None:
            extra["HTTP_IF_MATCH"] = revision_etag(revision)
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json; charset=utf-8",
            **extra,
        )

    def create_payload(self, **overrides) -> dict:
        payload = {
            "target_principal_id": str(self.service.id),
            "name": "Development health automation",
            "scopes": ["studio.home.read"],
            "confirmed": True,
        }
        payload.update(overrides)
        return payload


class RuntimeCredentialAPITests(RuntimeCredentialAPIBase):
    def test_bearer_mutation_is_not_subject_to_browser_csrf(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            "/api/v1/admin/credentials",
            data=json.dumps(self.create_payload()),
            content_type="application/json; charset=utf-8",
            HTTP_AUTHORIZATION=f"Bearer {self.actor_token}",
            HTTP_IDEMPOTENCY_KEY="runtime-csrf-independent",
        )

        self.assertEqual(response.status_code, 201)

    def test_list_create_rotate_revoke_and_health_are_one_time_and_safe(self) -> None:
        empty = self.api_get("/api/v1/admin/credentials")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["items"], [])

        created = self.api_post(
            "/api/v1/admin/credentials",
            self.create_payload(),
            key="runtime-create",
        )
        self.assertEqual(created.status_code, 201)
        service_token = created.json()["token"]
        credential = APICredential.objects.get(id=created.json()["credential_id"])
        parsed = parse_token(service_token)
        self.assertIsNotNone(parsed)
        self.assertTrue(verify_secret(parsed.secret, credential.secret_digest))  # type: ignore[union-attr]
        self.assertNotEqual(credential.secret_digest, parsed.secret)  # type: ignore[union-attr]
        self.assertEqual(credential.scopes, ["studio.home.read"])
        self.assertLessEqual(
            credential.expires_at,
            credential.created_at + timedelta(days=30, seconds=1),
        )
        created_audit = AuditEvent.objects.get(
            action="management.credential.created",
            target_id=credential.id,
            outcome=AuditEvent.Outcome.SUCCEEDED,
        )
        self.assertEqual(
            created_audit.idempotency_key_hash,
            hash_management_idempotency_key(
                self.human.id,
                "management.credential.create",
                "runtime-create",
            ),
        )
        self.assertEqual(created_audit.metadata["state"], "active")
        self.assertEqual(created_audit.metadata["scopes"], ["studio.home.read"])
        self.assertEqual(created_audit.metadata["expires_at"], credential.expires_at.isoformat())
        self.assertEqual(
            self.api_get("/api/v1/admin/health", token=service_token).status_code,
            200,
        )
        denied_management = self.api_get(
            "/api/v1/admin/credentials",
            token=service_token,
        )
        self.assertEqual(denied_management.status_code, 403)

        listed = self.api_get("/api/v1/admin/credentials")
        self.assertEqual(listed.status_code, 200)
        item = listed.json()["items"][0]
        self.assertEqual(item["credential_id"], str(credential.id))
        self.assertEqual(item["state"], "active")
        for forbidden in ("token", "secret", "secret_digest", "authorization"):
            self.assertNotIn(forbidden, json.dumps(listed.json()).casefold())

        replay = self.api_post(
            "/api/v1/admin/credentials",
            self.create_payload(),
            key="runtime-create",
        )
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()["error"]["code"], "secret_unavailable_on_replay")
        self.assertEqual(replay.json()["result"]["credential_id"], str(credential.id))
        self.assertNotIn("token", replay.json())
        conflict = self.api_post(
            "/api/v1/admin/credentials",
            self.create_payload(name="Changed retry"),
            key="runtime-create",
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "idempotency_conflict")
        APIRateAdmission.objects.filter(
            principal=self.human,
            cost_class=APIRateAdmission.CostClass.WRITE,
        ).delete()

        rotated = self.api_post(
            f"/api/v1/admin/credentials/{credential.id}/rotate",
            {"confirmed": True, "overlap_seconds": 0},
            key="runtime-rotate",
            revision=credential.revision,
        )
        self.assertEqual(rotated.status_code, 201)
        successor_token = rotated.json()["token"]
        successor = APICredential.objects.get(id=rotated.json()["credential_id"])
        credential.refresh_from_db()
        self.assertEqual(credential.overlap_expires_at, credential.rotated_at)
        self.assertEqual(
            self.api_get("/api/v1/admin/health", token=service_token).status_code,
            401,
        )
        self.assertEqual(
            self.api_get("/api/v1/admin/health", token=successor_token).status_code,
            200,
        )

        rotate_replay = self.api_post(
            f"/api/v1/admin/credentials/{credential.id}/rotate",
            {"confirmed": True, "overlap_seconds": 0},
            key="runtime-rotate",
            revision=1,
        )
        self.assertEqual(rotate_replay.status_code, 409)
        self.assertEqual(
            rotate_replay.json()["error"]["code"],
            "secret_unavailable_on_replay",
        )
        self.assertNotIn("token", rotate_replay.json())
        APIRateAdmission.objects.filter(
            principal=self.human,
            cost_class=APIRateAdmission.CostClass.WRITE,
        ).delete()

        revoked = self.api_post(
            f"/api/v1/admin/credentials/{successor.id}/revoke",
            {"confirmed": True},
            key="runtime-revoke",
            revision=successor.revision,
        )
        self.assertEqual(revoked.status_code, 200)
        revoke_replay = self.api_post(
            f"/api/v1/admin/credentials/{successor.id}/revoke",
            {"confirmed": True},
            key="runtime-revoke",
            revision=successor.revision,
        )
        self.assertEqual(revoke_replay.status_code, 200)
        self.assertEqual(revoke_replay.json(), revoked.json())
        self.assertEqual(
            self.api_get("/api/v1/admin/health", token=successor_token).status_code,
            401,
        )

        persisted = json.dumps(
            {
                "credentials": list(APICredential.objects.values()),
                "audits": list(AuditEvent.objects.values()),
                "idempotency": list(ManagementIdempotencyRecord.objects.values()),
            },
            default=str,
        )
        self.assertNotIn(service_token, persisted)
        self.assertNotIn(successor_token, persisted)

    def test_fail_closed_validation_scope_revision_expiry_and_confirmation(self) -> None:
        human_target = self.human
        cases = (
            (
                self.create_payload(target_principal_id=str(human_target.id)),
                "human-target",
                404,
                "not_found",
            ),
            (
                self.create_payload(scopes=["management.credentials.list"]),
                "scope-escalation",
                403,
                "permission_denied",
            ),
            (
                self.create_payload(expires_at=(timezone.now() + timedelta(days=91)).isoformat()),
                "expiry-too-long",
                400,
                "invalid_request",
            ),
            (
                self.create_payload(confirmed=False),
                "not-confirmed",
                403,
                "high_risk_denied",
            ),
            (
                self.create_payload(server_owned="forbidden"),
                "mass-assignment",
                400,
                "invalid_fields",
            ),
        )
        for payload, key, status, code in cases:
            with self.subTest(key=key):
                response = self.api_post(
                    "/api/v1/admin/credentials",
                    payload,
                    key=key,
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], code)
        self.assertEqual(
            APICredential.objects.filter(principal=self.service).count(),
            0,
        )
        APIRateAdmission.objects.filter(
            principal=self.human,
            cost_class=APIRateAdmission.CostClass.WRITE,
        ).delete()

        created = self.api_post(
            "/api/v1/admin/credentials",
            self.create_payload(),
            key="stale-seed",
        )
        credential = APICredential.objects.get(id=created.json()["credential_id"])
        stale = self.api_post(
            f"/api/v1/admin/credentials/{credential.id}/rotate",
            {"confirmed": True},
            key="stale-rotate",
            revision=credential.revision + 1,
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "state_conflict")
        self.assertFalse(APICredential.objects.filter(predecessor=credential).exists())

        missing_revision = self.api_post(
            f"/api/v1/admin/credentials/{credential.id}/rotate",
            {"confirmed": True},
            key="missing-revision",
        )
        self.assertEqual(missing_revision.status_code, 428)
        self.assertEqual(
            missing_revision.json()["error"]["code"],
            "precondition_required",
        )
        self.assertGreaterEqual(
            AuditEvent.objects.filter(
                action="management.credential.created",
                outcome=AuditEvent.Outcome.DENIED,
            ).count(),
            4,
        )
        denied_audit = AuditEvent.objects.filter(
            action="management.credential.created",
            outcome=AuditEvent.Outcome.DENIED,
            idempotency_key_hash=hash_management_idempotency_key(
                self.human.id,
                "management.credential.create",
                "not-confirmed",
            ),
        ).get()
        self.assertEqual(denied_audit.metadata["state"], "denied")
        self.assertEqual(denied_audit.metadata["reason"], "high_risk_denied")

    def test_openapi_declares_only_real_runtime_lifecycle_contracts(self) -> None:
        document = generate_document()
        credentials = document["paths"]["/credentials"]
        self.assertEqual(set(credentials), {"get", "post"})
        self.assertEqual(
            document["paths"]["/credentials/{credential_id}/rotate"]["post"]["operationId"],
            "management.credentials.rotate",
        )
        self.assertEqual(
            document["paths"]["/credentials/{credential_id}/revoke"]["post"]["operationId"],
            "management.credentials.revoke",
        )
        rendered = render_document()
        self.assertEqual(
            document["components"]["schemas"]["APIError"]["properties"]["result"],
            {"$ref": "#/components/schemas/CredentialMetadata"},
        )
        self.assertNotIn("_fixtures/credentials", rendered)
        self.assertNotIn("example.test", rendered)
        self.assertNotIn("dtca_v1_", rendered)


@override_settings(
    RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST,
    DEVELOPMENT_OWNER_LOGIN_ENABLED=True,
)
class RuntimeCredentialStudioTests(TestCase):
    def setUp(self) -> None:
        bootstrap_development_owner(
            email=OWNER_EMAIL,
            password=OWNER_PASSWORD,
            reset_password=False,
            allow_test=True,
        )
        self.user = cast(
            CustomUser,
            APIPrincipal.objects.get(kind=APIPrincipal.Kind.HUMAN).user,
        )
        self.service = APIPrincipal.objects.get(kind=APIPrincipal.Kind.SERVICE)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def test_studio_csrf_create_replay_rotate_revoke_and_authority_recheck(self) -> None:
        path = reverse("studio:credential-list")
        page = self.client.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "No service credentials have been issued.")
        csrf = self.client.cookies["csrftoken"].value
        create_payload = {
            "csrfmiddlewaretoken": csrf,
            "target_principal_id": str(self.service.id),
            "name": "Studio health automation",
            "expires_at": (timezone.now() + timedelta(days=30)).isoformat(),
            "scopes": "studio.home.read",
            "confirmed": "true",
            "idempotency_key": "studio-create-107",
        }
        missing_csrf = Client(enforce_csrf_checks=True)
        missing_csrf.force_login(self.user)
        self.assertEqual(missing_csrf.post(path, create_payload).status_code, 403)

        created = self.client.post(path, create_payload)
        self.assertEqual(created.status_code, 201)
        matches = TOKEN_PATTERN.findall(created.content.decode())
        self.assertEqual(len(matches), 1)
        raw_token = matches[0]
        credential = APICredential.objects.get(principal=self.service)

        replay = self.client.post(path, create_payload)
        self.assertEqual(replay.status_code, 409)
        self.assertNotContains(replay, raw_token, status_code=409)
        self.assertContains(replay, "no longer available", status_code=409)

        rotate_payload = {
            "csrfmiddlewaretoken": csrf,
            "expected_revision": str(credential.revision),
            "overlap_seconds": "0",
            "confirmed": "true",
            "idempotency_key": "studio-rotate-107",
        }
        rotated = self.client.post(
            reverse("studio:credential-rotate", args=(credential.id,)),
            rotate_payload,
        )
        self.assertEqual(rotated.status_code, 201)
        successor_token = TOKEN_PATTERN.findall(rotated.content.decode())[0]
        successor = APICredential.objects.get(predecessor=credential)

        revoked = self.client.post(
            reverse("studio:credential-revoke", args=(successor.id,)),
            {
                "csrfmiddlewaretoken": csrf,
                "expected_revision": str(successor.revision),
                "confirmed": "true",
                "idempotency_key": "studio-revoke-107",
            },
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertNotContains(revoked, raw_token)
        self.assertNotContains(revoked, successor_token)
        successor.refresh_from_db()
        self.assertIsNotNone(successor.revoked_at)

        self.user.groups.clear()
        denied = self.client.get(path)
        self.assertEqual(denied.status_code, 403)
        for response in (page, created, replay, rotated, revoked, denied):
            self.assertIn("private", response.headers["Cache-Control"])
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
