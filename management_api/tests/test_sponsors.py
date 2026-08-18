from __future__ import annotations

import json
import uuid

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.development_owner import bootstrap_development_owner
from accounts.studio_roles import MANAGE_API_CREDENTIALS
from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.bootstrap import RuntimeEnvironment
from core.models import AuditEvent, Sponsor
from management_api.openapi import generate_document
from management_auth.models import APIPrincipal
from management_auth.services import issue_credential_once

OWNER_EMAIL = "sponsors-api-owner@example.test"
OWNER_PASSWORD = "sponsors-api-owner-password-188"


@override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST)
class AdminSponsorTests(TestCase):
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
            name="Sponsor actor",
            scopes=(
                "site.sponsors.read",
                "site.sponsors.write",
                "site.sponsors.detail",
                "site.sponsors.update",
                "site.sponsors.archive",
                "site.sponsors.reactivate",
                "site.sponsors.export",
            ),
            idempotency_key="sponsor-actor",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        self.token = str(issued.response["token"])
        self.url = "/api/v1/admin/sponsors"

    def auth(self, **extra):
        extra.setdefault("HTTP_AUTHORIZATION", f"Bearer {self.token}")
        return extra

    def post(self, payload: object, *, url: str | None = None, key: str | None = None, **extra):
        return self.client.post(
            url or self.url,
            data=json.dumps(payload),
            content_type="application/json; charset=utf-8",
            HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()),
            **self.auth(**extra),
        )

    def patch(self, url: str, payload: object, *, revision: int, key: str | None = None):
        return self.client.patch(
            url,
            data=json.dumps(payload),
            content_type="application/json; charset=utf-8",
            HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()),
            HTTP_IF_MATCH=f'"rev-{revision}"',
            **self.auth(),
        )

    def assert_private_json(self, response, status: int) -> None:
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertTrue(response.headers["Content-Type"].startswith("application/json"))
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_create_get_patch_archive_export_and_openapi(self) -> None:
        empty = self.client.get(self.url, **self.auth())
        self.assert_private_json(empty, 200)
        self.assertEqual(empty.json()["items"], [])
        created = self.post(
            {
                "key": "acme",
                "name": "Acme Analytics",
                "url": "https://acme.example",
                "tagline": "Data for everyone",
                "lifecycle": "active",
                "assignments": [
                    {"placement": "events_hub", "position": 1, "enabled": True},
                ],
            }
        )
        self.assert_private_json(created, 201)
        sponsor_id = created.json()["sponsor"]["id"]
        detail = self.client.get(f"{self.url}/{sponsor_id}", **self.auth())
        self.assert_private_json(detail, 200)
        self.assertEqual(detail.headers["ETag"], '"rev-1"')
        updated = self.patch(
            f"{self.url}/{sponsor_id}",
            {
                "name": "Acme Analytics",
                "tagline": "Updated",
                "lifecycle": "active",
                "assignments": [
                    {"placement": "events_hub", "position": 1, "enabled": True},
                ],
            },
            revision=1,
        )
        self.assert_private_json(updated, 200)
        self.assertEqual(updated.json()["sponsor"]["revision"], 2)
        archived = self.post(
            {"confirmed": True, "expected_revision": 2},
            url=f"{self.url}/{sponsor_id}/archive",
        )
        self.assert_private_json(archived, 200)
        self.assertEqual(archived.json()["sponsor"]["lifecycle"], "archived")
        exported = self.post(
            {"confirmed": True, "reason": "api review"},
            url="/api/v1/admin/sponsor-directory-exports",
        )
        self.assert_private_json(exported, 200)
        self.assertIn("acme", exported.json()["csv"])
        document = generate_document()
        self.assertIn("/sponsors", document["paths"])
        self.assertIn("/sponsors/{sponsor_id}", document["paths"])
        self.assertIn("/sponsors/{sponsor_id}/archive", document["paths"])
        self.assertIn("/sponsor-directory-exports", document["paths"])
        self.assertEqual(
            document["paths"]["/sponsors/{sponsor_id}"]["patch"]["operationId"],
            "site.sponsors.update",
        )

    def test_validation_auth_and_stale_are_safe(self) -> None:
        invalid = self.post(
            {
                "key": "acme",
                "name": "Unsafe <markup>",
                "lifecycle": "draft",
                "assignments": [],
            }
        )
        self.assert_private_json(invalid, 400)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_request")
        self.assertFalse(Sponsor.objects.exists())
        created = self.post(
            {
                "key": "acme",
                "name": "Acme Analytics",
                "lifecycle": "draft",
                "assignments": [],
            }
        )
        sponsor_id = created.json()["sponsor"]["id"]
        stale = self.patch(
            f"{self.url}/{sponsor_id}",
            {"name": "Stale", "lifecycle": "draft", "assignments": []},
            revision=9,
        )
        self.assert_private_json(stale, 409)
        missing = self.client.get(self.url)
        self.assert_private_json(missing, 401)
        unknown = self.client.get(f"{self.url}/{uuid.uuid4()}", **self.auth())
        self.assert_private_json(unknown, 404)

    def test_scope_and_permission_intersection(self) -> None:
        issued = issue_credential_once(
            actor_principal=self.human,
            target_principal_id=self.human.id,
            name="Read only sponsor",
            scopes=("site.sponsors.read",),
            idempotency_key="sponsor-read-only",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        token = str(issued.response["token"])
        listed = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assert_private_json(listed, 200)
        denied = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "key": "denied",
                    "name": "Denied",
                    "lifecycle": "draft",
                    "assignments": [],
                }
            ),
            content_type="application/json; charset=utf-8",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assert_private_json(denied, 403)
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="change_sponsors",
        )
        self.human.permissions.remove(permission)
        user = self.human.user
        assert user is not None
        user.user_permissions.remove(permission)
        blocked = self.post(
            {
                "key": "blocked",
                "name": "Blocked",
                "lifecycle": "draft",
                "assignments": [],
            }
        )
        self.assert_private_json(blocked, 403)

    def test_studio_and_api_create_have_equivalent_effects(self) -> None:
        admin = make_studio_user(username="sponsor-parity", roles=("site_admin",))
        studio = authenticated_studio_client(admin)
        studio_response = studio.post(
            reverse("studio:sponsor-list"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "key": "studio-acme",
                "name": "Studio Acme",
                "url": "https://studio.example",
                "tagline": "Studio",
                "lifecycle": "draft",
                "placement": "events_hub",
                "position": "2",
                "assignment_enabled": "true",
            },
        )
        self.assertEqual(studio_response.status_code, 302)
        api_response = self.post(
            {
                "key": "api-acme",
                "name": "API Acme",
                "url": "https://api.example",
                "tagline": "API",
                "lifecycle": "draft",
                "assignments": [
                    {"placement": "events_hub", "position": 3, "enabled": True},
                ],
            }
        )
        self.assert_private_json(api_response, 201)
        self.assertEqual(Sponsor.objects.count(), 2)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.sponsor.created").count(),
            2,
        )
