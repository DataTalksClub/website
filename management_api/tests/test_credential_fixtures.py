from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import AuditEvent, IdempotencyRecord, Operation, StaffSession
from core.operations import start_operation
from management_api.concurrency import revision_etag
from management_api.operations import create_principal_operation
from management_api.test_fixtures.policy import fixture_policy
from management_auth.models import (
    APICredential,
    APIPrincipal,
    ManagementIdempotencyRecord,
)
from management_auth.services import (
    create_principal,
    issue_credential_once,
    replace_principal_permissions,
)


@override_settings(ROOT_URLCONF="management_api.tests.fixture_urlconf")
class CredentialLifecycleFixtureTests(TestCase):
    def setUp(self) -> None:
        access = Permission.objects.get(
            content_type__app_label="core",
            codename="access_studio",
        )
        high_risk = Permission.objects.get(
            content_type__app_label="core",
            codename="execute_high_risk_fixture",
        )
        self.user = get_user_model().objects.create_user(
            username="fixture-api-human",
            is_staff=True,
        )
        self.user.user_permissions.add(high_risk)
        self.actor = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="fixture API human",
            identity_snapshot="human:fixture-api",
            user=self.user,
            permissions=(high_risk,),
        )
        StaffSession.objects.create(
            user=self.user,
            authenticated_at=timezone.now(),
        )
        actor_credential = issue_credential_once(
            actor_principal=self.actor,
            target_principal_id=self.actor.id,
            name="fixture API adapter",
            scopes=(
                "management.credentials.create.fixture",
                "management.credentials.rotate.fixture",
                "management.credentials.revoke.fixture",
                "management.bulk.fixture",
                "management.operations.detail.fixture",
                "management.operations.cancel.fixture",
            ),
            idempotency_key="seed-fixture-actor",
            actor_permission="core.execute_high_risk_fixture",
            created_by=self.user,
        )
        self.actor_token = actor_credential.response["token"]
        self.target = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="fixture target",
            identity_snapshot="service:fixture-target",
            permissions=(access,),
        )

    def post(self, path: str, payload: dict, *, key: str, **extra):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json; charset=utf-8",
            HTTP_AUTHORIZATION=f"Bearer {self.actor_token}",
            HTTP_IDEMPOTENCY_KEY=key,
            **extra,
        )

    def create_payload(self) -> dict:
        return {
            "target_principal_id": str(self.target.id),
            "name": "fixture issued credential",
            "scopes": ["studio.home.read"],
            "confirmed": True,
        }

    def test_policy_modes_fail_before_credential_generation(self) -> None:
        baseline = APICredential.objects.count()
        for index, mode in enumerate(
            ("absent", "unresolved", "error", "stale", "mismatch", "cancelled")
        ):
            with self.subTest(mode=mode), fixture_policy(mode):
                response = self.post(
                    "/api/v1/admin/_fixtures/credentials",
                    self.create_payload(),
                    key=f"denied-{index}",
                )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "high_risk_denied")
            self.assertEqual(APICredential.objects.count(), baseline)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="management.credential.create.fixture",
                outcome=AuditEvent.Outcome.DENIED,
            ).count(),
            6,
        )

    def test_create_replay_rotate_revoke_and_secret_canary(self) -> None:
        with fixture_policy("allowed"):
            created = self.post(
                "/api/v1/admin/_fixtures/credentials",
                self.create_payload(),
                key="allowed-create",
            )
        self.assertEqual(created.status_code, 201)
        raw = created.json()["token"]
        credential = APICredential.objects.get(pk=created.json()["credential_id"])
        self.assertEqual(credential.principal, self.target)

        with fixture_policy("allowed"):
            replay = self.post(
                "/api/v1/admin/_fixtures/credentials",
                self.create_payload(),
                key="allowed-create",
            )
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()["error"]["code"], "secret_unavailable_on_replay")
        self.assertNotIn("token", replay.json())

        with fixture_policy("allowed"):
            rotated = self.post(
                f"/api/v1/admin/_fixtures/credentials/{credential.id}/rotate",
                {
                    "expected_revision": credential.revision,
                    "overlap_seconds": 0,
                    "confirmed": True,
                },
                key="allowed-rotate",
            )
        self.assertEqual(rotated.status_code, 201)
        successor = APICredential.objects.get(pk=rotated.json()["credential_id"])
        with fixture_policy("allowed"):
            revoked = self.post(
                f"/api/v1/admin/_fixtures/credentials/{successor.id}/revoke",
                {"expected_revision": successor.revision, "confirmed": True},
                key="allowed-revoke",
            )
        self.assertEqual(revoked.status_code, 200)
        with fixture_policy("allowed"):
            revoked_replay = self.post(
                f"/api/v1/admin/_fixtures/credentials/{successor.id}/revoke",
                {"expected_revision": successor.revision, "confirmed": True},
                key="allowed-revoke",
            )
        self.assertEqual(revoked_replay.status_code, 200)
        self.assertEqual(revoked_replay.json(), revoked.json())
        successor.refresh_from_db()
        self.assertIsNotNone(successor.revoked_at)

        persisted = json.dumps(
            {
                "credentials": list(APICredential.objects.values()),
                "audits": list(AuditEvent.objects.values()),
                "idempotency": list(IdempotencyRecord.objects.values()),
                "management_idempotency": list(ManagementIdempotencyRecord.objects.values()),
                "operations": list(Operation.objects.values()),
            },
            default=str,
        )
        self.assertNotIn(raw, persisted)
        for event in AuditEvent.objects.filter(
            action__in=(
                "management.credential.created",
                "management.credential.rotated",
                "management.credential.revoked",
            )
        ):
            self.assertEqual(event.api_principal_id, self.actor.id)
            self.assertNotIn(raw, json.dumps(event.metadata))

    def test_resource_scope_collapses_missing_malformed_and_human_targets(self) -> None:
        human_target_user = get_user_model().objects.create_user(username="fixture-human-target")
        human_target_user.user_permissions.add(
            Permission.objects.get(content_type__app_label="core", codename="access_studio")
        )
        human_target = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="out of scope human",
            identity_snapshot="human:out-of-scope",
            user=human_target_user,
            permissions=human_target_user.user_permissions.all(),
        )
        payloads = (
            {**self.create_payload(), "target_principal_id": "not-a-uuid"},
            {
                **self.create_payload(),
                "target_principal_id": "00000000-0000-4000-8000-000000000001",
            },
            {**self.create_payload(), "target_principal_id": str(human_target.id)},
        )
        responses = [
            self.post("/api/v1/admin/_fixtures/credentials", item, key=f"scope-{index}")
            for index, item in enumerate(payloads)
        ]
        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "not_found")
            self.assertEqual(
                response.json()["error"]["message"],
                "The requested management resource was not found.",
            )

    def test_unknown_fields_fail_before_policy_or_database_effect(self) -> None:
        baseline = {
            "credentials": APICredential.objects.count(),
            "idempotency": ManagementIdempotencyRecord.objects.count(),
            "audits": AuditEvent.objects.count(),
        }
        response = self.post(
            "/api/v1/admin/_fixtures/credentials",
            {**self.create_payload(), "server_owned": "forbidden"},
            key="mass-assignment",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_fields")
        self.assertEqual(APICredential.objects.count(), baseline["credentials"])
        self.assertEqual(ManagementIdempotencyRecord.objects.count(), baseline["idempotency"])
        self.assertEqual(AuditEvent.objects.count(), baseline["audits"])

    def test_unauthorized_resource_variants_are_identical_403_before_parsing(self) -> None:
        replace_principal_permissions(
            principal_id=self.actor.id,
            permissions=(),
            expected_revision=self.actor.revision,
        )
        payloads = (
            {**self.create_payload(), "target_principal_id": "not-a-uuid"},
            {
                **self.create_payload(),
                "target_principal_id": "00000000-0000-4000-8000-000000000001",
            },
            self.create_payload(),
        )
        responses = [
            self.post("/api/v1/admin/_fixtures/credentials", payload, key=f"denied-{index}")
            for index, payload in enumerate(payloads)
        ]
        for response in responses:
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "permission_denied")
            self.assertEqual(response.json()["error"]["message"], "Permission is denied.")

    def test_bulk_fixture_bounds_errors_and_safe_replay(self) -> None:
        payload = {
            "items": [
                {"name": "accepted", "valid": True},
                {"name": "rejected", "valid": False},
            ],
            "confirmed": True,
        }
        with fixture_policy("allowed"):
            response = self.post("/api/v1/admin/_fixtures/bulk", payload, key="bulk-fixture")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(response.json()["result"], {"accepted": 1})
        self.assertEqual(len(response.json()["errors"]), 1)
        with fixture_policy("allowed"):
            replay = self.post("/api/v1/admin/_fixtures/bulk", payload, key="bulk-fixture")
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(replay.json(), response.json())

        too_many = {
            "items": [{"name": str(index), "valid": True} for index in range(101)],
            "confirmed": True,
        }
        rejected = self.post("/api/v1/admin/_fixtures/bulk", too_many, key="bulk-too-many")
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()["error"]["code"], "invalid_bulk")

    def test_operation_detail_and_cancel_are_principal_scoped_and_replayable(self) -> None:
        operation = create_principal_operation(
            principal=self.actor,
            kind="fixture.lifecycle",
            cancellable=True,
            progress_total=1,
        )
        operation = start_operation(
            operation_id=operation.id,
            expected_revision=operation.revision,
        )
        detail = self.client.get(
            f"/api/v1/admin/_fixtures/operations/{operation.id}",
            HTTP_AUTHORIZATION=f"Bearer {self.actor_token}",
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["status"], "running")
        self.assertEqual(detail.json()["etag"], revision_etag(operation.revision))

        with fixture_policy("allowed"):
            cancelled = self.post(
                f"/api/v1/admin/_fixtures/operations/{operation.id}/cancel",
                {"confirmed": True},
                key="operation-cancel",
                HTTP_IF_MATCH=revision_etag(operation.revision),
            )
        self.assertEqual(cancelled.status_code, 200)
        self.assertTrue(cancelled.json()["cancellation_requested"])
        with fixture_policy("allowed"):
            replay = self.post(
                f"/api/v1/admin/_fixtures/operations/{operation.id}/cancel",
                {"confirmed": True},
                key="operation-cancel",
                HTTP_IF_MATCH=revision_etag(operation.revision),
            )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), cancelled.json())

        missing = self.client.get(
            "/api/v1/admin/_fixtures/operations/00000000-0000-4000-8000-000000000001",
            HTTP_AUTHORIZATION=f"Bearer {self.actor_token}",
        )
        malformed = self.client.get(
            "/api/v1/admin/_fixtures/operations/not-a-uuid",
            HTTP_AUTHORIZATION=f"Bearer {self.actor_token}",
        )
        self.assertEqual((missing.status_code, malformed.status_code), (404, 404))
        self.assertEqual(missing.json()["error"]["code"], malformed.json()["error"]["code"])
