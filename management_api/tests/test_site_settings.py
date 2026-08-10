from __future__ import annotations

import json
import uuid
from typing import Any
from unittest import mock

from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.development_owner import bootstrap_development_owner
from accounts.studio_roles import MANAGE_API_CREDENTIALS
from accounts.studio_sessions import SESSION_REFERENCE_KEY, revoke_staff_session
from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.bootstrap import RuntimeEnvironment
from core.models import AuditEvent, OperationalSetting, OperationalSettingRevision
from core.site_settings import ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY
from management_api.openapi import generate_document
from management_auth.models import APICredential, APIPrincipal, APIRateAdmission
from management_auth.services import issue_credential_once

OWNER_EMAIL = "settings-api-owner@example.test"
OWNER_PASSWORD = "settings-api-owner-password-114"


@override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST)
class AdminSiteSettingsTests(TestCase):
    def setUp(self) -> None:
        bootstrap_development_owner(
            email=OWNER_EMAIL,
            password=OWNER_PASSWORD,
            reset_password=False,
            allow_test=True,
        )
        self.human = APIPrincipal.objects.get(kind=APIPrincipal.Kind.HUMAN)
        issued = issue_credential_once(
            actor_principal=self.human,
            target_principal_id=self.human.id,
            name="Site settings actor",
            scopes=("site.settings.read", "site.settings.write"),
            idempotency_key="site-settings-actor",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        self.token = str(issued.response["token"])
        self.credential_id = uuid.UUID(str(issued.response["credential_id"]))
        self.url = "/api/v1/admin/settings"

    def get(self, *, token: str | None = None, **extra):
        return self.client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {token or self.token}",
            **extra,
        )

    def post(
        self,
        payload: object,
        *,
        key: str | None = None,
        token: str | None = None,
        client: Client | None = None,
        **extra,
    ):
        return (client or self.client).post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json; charset=utf-8",
            HTTP_AUTHORIZATION=f"Bearer {token or self.token}",
            HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()),
            **extra,
        )

    def assert_private_json(self, response, status: int) -> None:
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertTrue(response.headers["Content-Type"].startswith("application/json"))
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def clear_rate_admissions(self) -> None:
        APIRateAdmission.objects.all().delete()

    def test_get_and_post_exact_contract_replay_and_public_safe_evidence(self) -> None:
        defaults = self.get()
        self.assert_private_json(defaults, 200)
        settings = defaults.json()["settings"]
        self.assertEqual(
            [item["key"] for item in settings],
            [ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY],
        )
        self.assertEqual(settings[0]["value"], False)
        self.assertEqual(settings[0]["source"], "code_default")
        self.assertEqual(settings[0]["revision"], 0)
        self.assertEqual(settings[1]["value"], "")

        key = str(uuid.uuid4())
        payload = {
            "updates": [
                {
                    "key": ANNOUNCEMENT_MESSAGE_KEY,
                    "value": "  API announcement  ",
                    "expected_revision": 0,
                },
                {
                    "key": ANNOUNCEMENT_ENABLED_KEY,
                    "value": True,
                    "expected_revision": 0,
                },
            ]
        }
        changed = self.post(payload, key=key)
        self.assert_private_json(changed, 200)
        self.assertFalse(changed.json()["replayed"])
        self.assertEqual(
            [item["key"] for item in changed.json()["settings"]],
            [ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY],
        )
        self.assertEqual(changed.json()["settings"][1]["value"], "API announcement")
        self.assertEqual(changed.json()["settings"][1]["source"], "admin_api")
        self.assertTrue(all(item["changed"] for item in changed.json()["settings"]))
        self.assertEqual(OperationalSettingRevision.objects.count(), 2)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.operational_settings.batch_updated").count(),
            1,
        )

        self.clear_rate_admissions()
        replay = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": True,
                        "expected_revision": 0,
                    },
                    {
                        "key": ANNOUNCEMENT_MESSAGE_KEY,
                        "value": "API announcement",
                        "expected_revision": 0,
                    },
                ]
            },
            key=key,
        )
        self.assert_private_json(replay, 200)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["settings"], changed.json()["settings"])
        self.assertEqual(OperationalSettingRevision.objects.count(), 2)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.operational_settings.batch_updated").count(),
            1,
        )

    def test_strict_validation_stale_and_idempotency_conflicts_are_safe(self) -> None:
        invalid_cases: tuple[tuple[dict[str, Any], str], ...] = (
            ({}, "invalid_fields"),
            ({"updates": [], "source": "client"}, "invalid_fields"),
            (
                {
                    "updates": [
                        {
                            "key": "site.unknown",
                            "value": True,
                            "expected_revision": 0,
                        }
                    ]
                },
                "invalid_request",
            ),
            (
                {
                    "updates": [
                        {
                            "key": ANNOUNCEMENT_MESSAGE_KEY,
                            "value": "unsafe<markup>",
                            "expected_revision": 0,
                        }
                    ]
                },
                "invalid_request",
            ),
        )
        for payload, code in invalid_cases:
            with self.subTest(payload=payload):
                response = self.post(payload)
                self.assert_private_json(response, 400)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertNotIn("Traceback", response.content.decode())
                self.clear_rate_admissions()
        self.assertFalse(OperationalSetting.objects.exists())

        missing_key = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "updates": [
                        {
                            "key": ANNOUNCEMENT_ENABLED_KEY,
                            "value": True,
                            "expected_revision": 0,
                        }
                    ]
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assert_private_json(missing_key, 400)
        self.assertEqual(missing_key.json()["error"]["code"], "invalid_idempotency_key")
        self.clear_rate_admissions()

        shared_key = str(uuid.uuid4())
        first = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": True,
                        "expected_revision": 0,
                    }
                ]
            },
            key=shared_key,
        )
        self.assert_private_json(first, 200)
        self.clear_rate_admissions()
        conflict = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": False,
                        "expected_revision": 1,
                    }
                ]
            },
            key=shared_key,
        )
        self.assert_private_json(conflict, 409)
        self.assertEqual(conflict.json()["error"]["code"], "idempotency_conflict")
        self.clear_rate_admissions()

        stale = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": False,
                        "expected_revision": 0,
                    }
                ]
            }
        )
        self.assert_private_json(stale, 409)
        self.assertEqual(stale.json()["error"]["code"], "revision_conflict")
        self.assertEqual(
            stale.json()["result"],
            {"key": ANNOUNCEMENT_ENABLED_KEY, "revision": 1},
        )
        self.assertIs(
            OperationalSetting.objects.get(key=ANNOUNCEMENT_ENABLED_KEY).value,
            True,
        )

        self.clear_rate_admissions()
        read_permission = Permission.objects.get(
            content_type__app_label="core",
            codename="read_operational_settings",
        )
        self.human.permissions.remove(read_permission)
        denied_permission = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": False,
                        "expected_revision": 1,
                    }
                ]
            }
        )
        self.assert_private_json(denied_permission, 403)
        self.assertIs(
            OperationalSetting.objects.get(key=ANNOUNCEMENT_ENABLED_KEY).value,
            True,
        )

    def test_auth_csrf_cors_method_and_safe_failure_contracts(self) -> None:
        unauthenticated = self.client.get(self.url)
        self.assert_private_json(unauthenticated, 401)
        self.assertEqual(
            unauthenticated.json()["error"]["code"],
            "authentication_required",
        )

        read_only_issued = issue_credential_once(
            actor_principal=self.human,
            target_principal_id=self.human.id,
            name="Site settings reader",
            scopes=("site.settings.read",),
            idempotency_key="site-settings-reader",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        read_only_token = str(read_only_issued.response["token"])
        denied = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": True,
                        "expected_revision": 0,
                    }
                ]
            },
            token=read_only_token,
        )
        self.assert_private_json(denied, 403)
        self.assertFalse(OperationalSetting.objects.exists())

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_independent = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": True,
                        "expected_revision": 0,
                    }
                ]
            },
            client=csrf_client,
        )
        self.assert_private_json(csrf_independent, 200)
        self.clear_rate_admissions()

        method = self.client.delete(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assert_private_json(method, 405)
        preflight = self.client.options(
            self.url,
            HTTP_ORIGIN="https://untrusted.invalid",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        self.assert_private_json(preflight, 401)

        self.clear_rate_admissions()
        with mock.patch(
            "core.site_settings.OperationalSetting.objects.using",
            side_effect=DatabaseError("unavailable"),
        ):
            failed = self.get()
        self.assert_private_json(failed, 500)
        self.assertEqual(failed.json()["error"]["code"], "internal_error")
        self.assertNotIn("unavailable", failed.content.decode())

    def test_write_scope_requires_current_principal_read_permission(self) -> None:
        write_only = issue_credential_once(
            actor_principal=self.human,
            target_principal_id=self.human.id,
            name="Site settings write-only regression",
            scopes=("site.settings.write",),
            idempotency_key="site-settings-write-only",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        allowed_scope = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": True,
                        "expected_revision": 0,
                    }
                ]
            },
            token=str(write_only.response["token"]),
        )
        self.assert_private_json(allowed_scope, 200)
        self.assertIs(
            OperationalSetting.objects.get(key=ANNOUNCEMENT_ENABLED_KEY).value,
            True,
        )

    def test_stale_studio_and_api_commands_have_redacted_audit_parity(self) -> None:
        user = self.human.user
        assert user is not None
        studio_client = authenticated_studio_client(user)
        initial = studio_client.post(
            "/studio/settings",
            {
                "idempotency_key": str(uuid.uuid4()),
                "enabled_expected_revision": "0",
                "message_expected_revision": "0",
                "announcement_enabled": "true",
                "announcement_message": "Initial public message",
            },
        )
        self.assertEqual(initial.status_code, 302)

        stale_studio = studio_client.post(
            "/studio/settings",
            {
                "idempotency_key": str(uuid.uuid4()),
                "enabled_expected_revision": "0",
                "message_expected_revision": "0",
                "announcement_message": "Studio stale proposal",
            },
        )
        self.assertEqual(stale_studio.status_code, 409)
        self.assertEqual(stale_studio.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("private", stale_studio.headers["Cache-Control"])
        self.assertIn("no-store", stale_studio.headers["Cache-Control"])

        self.clear_rate_admissions()
        stale_api = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": False,
                        "expected_revision": 0,
                    },
                    {
                        "key": ANNOUNCEMENT_MESSAGE_KEY,
                        "value": "API stale proposal",
                        "expected_revision": 0,
                    },
                ]
            }
        )
        self.assert_private_json(stale_api, 409)

        events = tuple(
            AuditEvent.objects.filter(
                action="core.operational_settings.batch_updated",
            ).order_by("created_at", "id")
        )
        self.assertEqual([event.outcome for event in events], ["succeeded", "denied", "denied"])
        self.assertEqual(
            [event.metadata.get("reason") for event in events[1:]],
            ["revision_conflict", "revision_conflict"],
        )
        denial_evidence = json.dumps(
            [
                {
                    "changes": event.changes,
                    "metadata": event.metadata,
                }
                for event in events[1:]
            ],
            sort_keys=True,
        )
        self.assertNotIn("Studio stale proposal", denial_evidence)
        self.assertNotIn("API stale proposal", denial_evidence)
        setting = OperationalSetting.objects.get(key=ANNOUNCEMENT_MESSAGE_KEY)
        self.assertEqual(setting.value, "Initial public message")
        self.assertEqual(setting.revision, 1)
        self.assertEqual(OperationalSettingRevision.objects.count(), 2)

    def test_studio_and_api_permission_denials_have_audit_parity(self) -> None:
        auditor = make_studio_user(
            username="settings-parity-auditor",
            roles=("auditor",),
        )
        studio_client = authenticated_studio_client(auditor)
        denied_studio = studio_client.post(
            "/studio/settings",
            {
                "idempotency_key": str(uuid.uuid4()),
                "enabled_expected_revision": "0",
                "message_expected_revision": "0",
                "announcement_enabled": "true",
                "announcement_message": "Denied Studio proposal",
            },
        )
        self.assertEqual(denied_studio.status_code, 403)

        self.clear_rate_admissions()
        write_permission = Permission.objects.get(
            content_type__app_label="core",
            codename="change_operational_settings",
        )
        self.human.permissions.remove(write_permission)
        denied_api = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": True,
                        "expected_revision": 0,
                    }
                ]
            }
        )
        self.assert_private_json(denied_api, 403)

        events = tuple(
            AuditEvent.objects.filter(
                action="core.operational_settings.batch_updated",
                outcome=AuditEvent.Outcome.DENIED,
            ).order_by("created_at", "id")
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(
            [event.metadata.get("reason") for event in events],
            ["permission_denied", "permission_denied"],
        )
        evidence = json.dumps(
            [{"changes": event.changes, "metadata": event.metadata} for event in events],
            sort_keys=True,
        )
        self.assertNotIn("Denied Studio proposal", evidence)
        self.assertFalse(OperationalSetting.objects.exists())

    def test_revoked_studio_and_api_authentication_failures_do_not_audit(self) -> None:
        admin = make_studio_user(
            username="settings-revoked-session-admin",
            roles=("site_admin",),
        )
        studio_client = authenticated_studio_client(admin)
        session_id = uuid.UUID(studio_client.session[SESSION_REFERENCE_KEY])
        self.assertTrue(revoke_staff_session(session_id, user=admin))
        revoked_studio = studio_client.post(
            "/studio/settings",
            {
                "idempotency_key": str(uuid.uuid4()),
                "enabled_expected_revision": "0",
                "message_expected_revision": "0",
                "announcement_enabled": "true",
                "announcement_message": "Revoked Studio proposal",
            },
        )
        self.assertEqual(revoked_studio.status_code, 403)

        credential = APICredential.objects.get(pk=self.credential_id)
        credential.revoked_at = timezone.now()
        credential.revision += 1
        credential.save(
            update_fields=("revoked_at", "revision", "updated_at"),
        )
        revoked_api = self.post(
            {
                "updates": [
                    {
                        "key": ANNOUNCEMENT_ENABLED_KEY,
                        "value": True,
                        "expected_revision": 0,
                    }
                ]
            }
        )
        self.assert_private_json(revoked_api, 401)

        self.assertFalse(
            AuditEvent.objects.filter(
                action="core.operational_settings.batch_updated",
                outcome=AuditEvent.Outcome.DENIED,
            ).exists()
        )

    def test_openapi_declares_exact_settings_operations_and_bounds(self) -> None:
        document = generate_document()
        operations = document["paths"]["/settings"]
        self.assertEqual(set(operations), {"get", "post"})
        self.assertEqual(operations["get"]["operationId"], "site.settings.read")
        self.assertEqual(operations["post"]["operationId"], "site.settings.write")
        self.assertEqual(
            operations["post"]["security"],
            [{"BearerAuth": ["site.settings.write"]}],
        )
        header = operations["post"]["parameters"][0]
        self.assertEqual(
            (header["name"], header["required"], header["schema"]["maxLength"]),
            ("Idempotency-Key", True, 512),
        )
        request_schema = document["components"]["schemas"]["SiteSettingsBatchRequest"]
        updates = request_schema["properties"]["updates"]
        self.assertEqual((updates["minItems"], updates["maxItems"]), (1, 2))
        self.assertFalse(request_schema["additionalProperties"])
        item_variants = updates["items"]["oneOf"]
        self.assertEqual(len(item_variants), 2)
        self.assertEqual(
            item_variants[0]["properties"]["value"]["type"],
            "boolean",
        )
        self.assertEqual(
            item_variants[1]["properties"]["value"]["maxLength"],
            500,
        )
        for variant in item_variants:
            self.assertNotIn("source", variant["properties"])
