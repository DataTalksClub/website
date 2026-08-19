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
from core.idempotency import JsonObject
from core.models import AuditEvent, SiteNavigationEntry, SiteNavigationMenu, SiteNavigationRevision
from core.navigation import default_navigation_entries
from management_api.concurrency import navigation_revision_etag
from management_api.openapi import generate_document
from management_auth.models import APICredential, APIPrincipal, APIRateAdmission
from management_auth.services import issue_credential_once

OWNER_EMAIL = "navigation-api-owner@example.test"
OWNER_PASSWORD = "navigation-api-owner-password-187"


def default_entries() -> list[JsonObject]:
    return [entry.as_dict() for entry in default_navigation_entries()]


@override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST)
class AdminSiteNavigationTests(TestCase):
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
            name="Site navigation actor",
            scopes=("site.navigation.read", "site.navigation.write"),
            idempotency_key="site-navigation-actor",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        self.token = str(issued.response["token"])
        self.credential_id = uuid.UUID(str(issued.response["credential_id"]))
        self.url = "/api/v1/admin/navigation"

    def get(self, *, token: str | None = None, **extra):
        return self.client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {token or self.token}",
            **extra,
        )

    def put(
        self,
        payload: object,
        *,
        key: str | None = None,
        revision: int = 0,
        token: str | None = None,
        client: Client | None = None,
        **extra,
    ):
        return (client or self.client).put(
            self.url,
            data=json.dumps(payload),
            content_type="application/json; charset=utf-8",
            HTTP_AUTHORIZATION=f"Bearer {token or self.token}",
            HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()),
            HTTP_IF_MATCH=navigation_revision_etag(revision),
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

    def test_get_and_put_exact_contract_replay_and_public_safe_evidence(self) -> None:
        defaults = self.get()
        self.assert_private_json(defaults, 200)
        self.assertEqual(defaults.json()["source"], "code_default")
        self.assertEqual(defaults.json()["revision"], 0)
        self.assertEqual(defaults.json()["menu"], "primary")
        self.assertEqual(defaults["ETag"], navigation_revision_etag(0))
        self.assertEqual(
            [item["key"] for item in defaults.json()["entries"]],
            [entry["key"] for entry in default_entries()],
        )

        key = str(uuid.uuid4())
        payload = {
            "entries": [
                {**default_entries()[0], "label": "  API gatherings  "},
                *default_entries()[1:],
            ]
        }
        changed = self.put(payload, key=key)
        self.assert_private_json(changed, 200)
        self.assertFalse(changed.json()["replayed"])
        self.assertTrue(changed.json()["changed"])
        self.assertEqual(changed.json()["source"], "admin_api")
        self.assertEqual(changed.json()["entries"][0]["label"], "API gatherings")
        self.assertEqual(SiteNavigationRevision.objects.count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.site_navigation.updated").count(),
            1,
        )

        self.clear_rate_admissions()
        replay = self.put(
            {
                "entries": [
                    {**default_entries()[0], "label": "API gatherings"},
                    *default_entries()[1:],
                ]
            },
            key=key,
        )
        self.assert_private_json(replay, 200)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["entries"], changed.json()["entries"])
        self.assertEqual(SiteNavigationRevision.objects.count(), 1)

    def test_strict_validation_stale_and_idempotency_conflicts_are_safe(self) -> None:
        invalid_cases: tuple[tuple[dict[str, Any], str], ...] = (
            ({}, "invalid_fields"),
            ({"entries": [], "source": "client"}, "invalid_fields"),
            (
                {
                    "entries": [
                        {
                            "key": "events",
                            "label": "Events",
                            "target": "https://evil.example",
                            "position": 1,
                            "visible": True,
                        }
                    ]
                },
                "invalid_request",
            ),
        )
        for payload, code in invalid_cases:
            with self.subTest(payload=payload):
                response = self.put(payload)
                self.assert_private_json(response, 400)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertNotIn("Traceback", response.content.decode())
                self.clear_rate_admissions()
        self.assertFalse(SiteNavigationMenu.objects.exists())

        missing_key = self.client.put(
            self.url,
            data=json.dumps({"entries": default_entries()}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
            HTTP_IF_MATCH=navigation_revision_etag(0),
        )
        self.assert_private_json(missing_key, 400)
        self.assertEqual(missing_key.json()["error"]["code"], "invalid_idempotency_key")
        self.clear_rate_admissions()

        shared_key = str(uuid.uuid4())
        first = self.put(
            {"entries": [{**default_entries()[0], "label": "First"}, *default_entries()[1:]]},
            key=shared_key,
        )
        self.assert_private_json(first, 200)
        self.clear_rate_admissions()
        conflict = self.put(
            {"entries": [{**default_entries()[0], "label": "Other"}, *default_entries()[1:]]},
            key=shared_key,
            revision=1,
        )
        self.assert_private_json(conflict, 409)
        self.assertEqual(conflict.json()["error"]["code"], "idempotency_conflict")
        self.clear_rate_admissions()

        stale = self.put(
            {"entries": [{**default_entries()[0], "label": "Stale"}, *default_entries()[1:]]},
            revision=0,
        )
        self.assert_private_json(stale, 409)
        self.assertEqual(stale.json()["error"]["code"], "revision_conflict")
        self.assertEqual(
            stale.json()["result"],
            {"menu": "primary", "revision": 1},
        )
        self.assertEqual(SiteNavigationMenu.objects.get().revision, 1)

        self.clear_rate_admissions()
        read_permission = Permission.objects.get(
            content_type__app_label="core",
            codename="read_site_navigation",
        )
        self.human.permissions.remove(read_permission)
        denied_permission = self.put(
            {"entries": [{**default_entries()[0], "label": "Denied"}, *default_entries()[1:]]},
            revision=1,
        )
        self.assert_private_json(denied_permission, 403)
        self.assertEqual(SiteNavigationEntry.objects.get(key="events").label, "First")

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
            name="Site navigation reader",
            scopes=("site.navigation.read",),
            idempotency_key="site-navigation-reader",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        read_only_token = str(read_only_issued.response["token"])
        denied = self.put(
            {"entries": [{**default_entries()[0], "label": "Denied"}, *default_entries()[1:]]},
            token=read_only_token,
        )
        self.assert_private_json(denied, 403)
        self.assertFalse(SiteNavigationMenu.objects.exists())

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_independent = self.put(
            {"entries": [{**default_entries()[0], "label": "CSRF"}, *default_entries()[1:]]},
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
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="PUT",
        )
        self.assert_private_json(preflight, 401)

        self.clear_rate_admissions()
        with mock.patch(
            "core.navigation.SiteNavigationMenu.objects.using",
            side_effect=DatabaseError("unavailable"),
        ):
            failed = self.get()
        self.assert_private_json(failed, 500)
        self.assertEqual(failed.json()["error"]["code"], "internal_error")
        self.assertNotIn("unavailable", failed.content.decode())

    def test_studio_and_api_share_authorization_and_public_visibility(self) -> None:
        studio_admin = make_studio_user(username="navigation-parity-admin", roles=("site_admin",))
        studio = authenticated_studio_client(studio_admin)
        page = studio.get("/studio/navigation")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Site navigation")

        auditor = make_studio_user(username="navigation-parity-auditor", roles=("auditor",))
        auditor_client = authenticated_studio_client(auditor)
        self.assertEqual(auditor_client.get("/studio/navigation").status_code, 200)
        denied = auditor_client.post(
            "/studio/navigation",
            {
                "idempotency_key": str(uuid.uuid4()),
                "expected_revision": "0",
                "entry-0-key": "events",
                "entry-0-label": "Denied",
                "entry-0-target": "events",
                "entry-0-position": "1",
                "entry-0-visible": "true",
            },
        )
        self.assertEqual(denied.status_code, 403)

        revoked = make_studio_user(
            username="navigation-revoked-session-admin",
            roles=("site_admin",),
        )
        revoked_client = authenticated_studio_client(revoked)
        revoke_staff_session(revoked_client.session[SESSION_REFERENCE_KEY])
        revoked_studio = revoked_client.get("/studio/navigation")
        self.assertEqual(revoked_studio.status_code, 403)

        credential = APICredential.objects.get(pk=self.credential_id)
        credential.revoked_at = timezone.now()
        credential.revision += 1
        credential.save(update_fields=("revoked_at", "revision", "updated_at"))
        revoked_api = self.put({"entries": default_entries()})
        self.assert_private_json(revoked_api, 401)

    def test_openapi_declares_exact_navigation_operations_and_bounds(self) -> None:
        document = generate_document()
        operations = document["paths"]["/navigation"]
        self.assertEqual(set(operations), {"get", "put"})
        self.assertEqual(operations["get"]["operationId"], "site.navigation.read")
        self.assertEqual(operations["put"]["operationId"], "site.navigation.write")
        self.assertEqual(
            operations["put"]["security"],
            [{"BearerAuth": ["site.navigation.write"]}],
        )
        headers = {item["name"]: item for item in operations["put"]["parameters"]}
        self.assertEqual(headers["Idempotency-Key"]["schema"]["maxLength"], 512)
        self.assertEqual(headers["If-Match"]["schema"]["pattern"], '^"rev-[0-9]+"$')
        request_schema = document["components"]["schemas"]["SiteNavigationReplaceRequest"]
        entries = request_schema["properties"]["entries"]
        self.assertEqual((entries["minItems"], entries["maxItems"]), (1, 12))
        self.assertFalse(request_schema["additionalProperties"])
        self.assertNotIn("source", request_schema["properties"])
        self.assertNotIn("expected_revision", request_schema["properties"])
