from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.http import QueryDict
from django.test import RequestFactory, TestCase
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from management_api.concurrency import require_if_match, revision_etag
from management_api.errors import APIError
from management_api.json_input import parse_json_object
from management_api.openapi import generate_document, render_document
from management_api.parity import parity_errors, runtime_operations
from management_api.query import parse_page_query
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import (
    create_principal,
    issue_credential_once,
    replace_principal_permissions,
    revoke_credential,
    set_principal_active,
)
from management_auth.tokens import encode_secret, generate_token


class AdminAPIHealthTests(TestCase):
    def setUp(self) -> None:
        self.permission = Permission.objects.get(
            content_type__app_label="core",
            codename="access_studio",
        )
        self.principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="health service",
            identity_snapshot="service:health",
            permissions=(self.permission,),
        )
        issued = issue_credential_once(
            actor_principal=self.principal,
            target_principal_id=self.principal.id,
            name="health credential",
            scopes=("studio.home.read",),
            idempotency_key="health-fixture",
            actor_permission="core.access_studio",
        )
        self.raw_token = str(issued.response["token"])
        self.credential = APICredential.objects.get(pk=str(issued.response["credential_id"]))
        self.url = reverse("api:admin-health")

    def bearer(self, token=None, **extra):
        return self.client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {token or self.raw_token}",
            **extra,
        )

    def assert_generic_401(self, response) -> None:
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "authentication_required",
                    "message": "Valid Bearer authentication is required.",
                    "request_id": response.headers["X-Request-ID"],
                }
            },
        )
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")

    def test_real_bearer_health_is_non_pii_private_and_last_used_is_throttled(self) -> None:
        response = self.bearer()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("version", response.json())
        self.assertEqual(response.json()["source_sha"], None)
        self.assertEqual(response.json()["image_digest"], None)
        self.assertNotIn("actor", response.json())
        self.assertNotIn("email", json.dumps(response.json()).casefold())
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.credential.refresh_from_db()
        first_used = self.credential.last_used_at
        self.bearer()
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.last_used_at, first_used)

    def test_openapi_uses_the_canonical_release_identity_contract(self) -> None:
        document = generate_document()
        health = document["components"]["schemas"]["AdminHealth"]
        event_identity = document["components"]["schemas"]["EventIdentity"]

        self.assertEqual(document["info"]["version"], settings.VERSION)
        self.assertEqual(
            health["required"],
            ["status", "version", "source_sha", "image_digest"],
        )
        self.assertEqual(health["properties"]["source_sha"]["type"], ["string", "null"])
        self.assertEqual(health["properties"]["image_digest"]["type"], ["string", "null"])
        self.assertEqual(event_identity["properties"]["id"], {"type": "string", "format": "uuid"})
        self.assertEqual(
            event_identity["properties"]["public_id"],
            {"type": "integer", "minimum": 1, "readOnly": True},
        )
        self.assertEqual(
            event_identity["properties"]["canonical_path"]["pattern"],
            "^/events/[1-9][0-9]*/[-a-z0-9]+$",
        )
        self.assertIn("/events/identities/{event_id}", document["paths"])
        self.assertNotIn("/events/identities/{public_id}", document["paths"])

    def test_failure_matrix_is_generic_and_confused_deputies_are_rejected(self) -> None:
        staff = get_user_model().objects.create_user(username="session-staff", is_staff=True)
        self.client.force_login(staff)
        responses = [
            self.client.get(self.url),
            self.client.get(self.url, HTTP_AUTHORIZATION="Token legacy-token"),
            self.client.get(self.url, HTTP_AUTHORIZATION="bearer lowercase"),
            self.client.get(self.url, HTTP_AUTHORIZATION="Bearer malformed"),
            self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {self.raw_token}, Bearer other"),
            self.client.get(self.url, {"token": str(self.raw_token)}),
            self.client.post(
                self.url,
                {"token": str(self.raw_token)},
                content_type="application/x-www-form-urlencoded",
            ),
        ]
        self.client.cookies["token"] = str(self.raw_token)
        responses.append(self.client.get(self.url))
        del self.client.cookies["token"]
        unknown = generate_token().raw
        responses.append(self.bearer(unknown))
        parsed_prefix, secret = self.raw_token.rsplit("_", 1)
        wrong = f"{parsed_prefix}_{'x' * len(secret)}"
        responses.append(self.bearer(wrong))
        other_algorithm = generate_token()
        APICredential.objects.create(
            principal=self.principal,
            name="other algorithm",
            prefix=other_algorithm.prefix,
            secret_digest=encode_secret(other_algorithm.secret),
            digest_algorithm="other",
            digest_version=1,
            scopes=["studio.home.read"],
            expires_at=timezone.now() + timedelta(days=1),
        )
        responses.append(self.bearer(other_algorithm.raw))
        unusable = generate_token()
        APICredential.objects.create(
            principal=self.principal,
            name="unusable digest",
            prefix=unusable.prefix,
            digest_algorithm="pbkdf2_sha256",
            digest_version=1,
            secret_digest="pbkdf2_sha256$unusable",
            scopes=["studio.home.read"],
            expires_at=timezone.now() + timedelta(days=1),
        )
        responses.append(self.bearer(unusable.raw))
        for response in responses:
            with self.subTest(response=response):
                self.assert_generic_401(response)

    def test_expiry_revocation_disable_and_permission_removal_apply_next_request(self) -> None:
        expiry = max(self.credential.created_at + timedelta(microseconds=1), timezone.now())
        APICredential.objects.filter(pk=self.credential.pk).update(expires_at=expiry)
        self.assert_generic_401(self.bearer())

        APICredential.objects.filter(pk=self.credential.pk).update(
            expires_at=timezone.now() + timedelta(days=1)
        )
        self.credential.refresh_from_db()
        revoke_credential(
            actor_principal=self.principal,
            credential_id=self.credential.id,
            expected_revision=self.credential.revision,
            actor_permission="core.access_studio",
        )
        self.assert_generic_401(self.bearer())

        APICredential.objects.filter(pk=self.credential.pk).update(revoked_at=None)
        self.principal.refresh_from_db()
        set_principal_active(
            principal_id=self.principal.id,
            is_active=False,
            expected_revision=self.principal.revision,
        )
        self.assert_generic_401(self.bearer())

        self.principal.refresh_from_db()
        set_principal_active(
            principal_id=self.principal.id,
            is_active=True,
            expected_revision=self.principal.revision,
        )
        self.principal.refresh_from_db()
        replace_principal_permissions(
            principal_id=self.principal.id,
            permissions=(),
            expected_revision=self.principal.revision,
        )
        denied = self.bearer()
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["code"], "permission_denied")

    def test_human_linked_user_disablement_applies_on_the_next_request(self) -> None:
        user = get_user_model().objects.create_user(username="health-human")
        user.user_permissions.add(self.permission)
        human = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="health human",
            identity_snapshot="human:health",
            user=user,
            permissions=(self.permission,),
        )
        issued = issue_credential_once(
            actor_principal=human,
            target_principal_id=human.id,
            name="human health credential",
            scopes=("studio.home.read",),
            idempotency_key="human-health",
            actor_permission="core.access_studio",
            created_by=user,
        )
        self.assertEqual(self.bearer(issued.response["token"]).status_code, 200)
        user.is_active = False
        user.save(update_fields=("is_active",))
        self.assert_generic_401(self.bearer(issued.response["token"]))

    def test_method_preflight_cors_and_unknown_route_use_safe_json(self) -> None:
        post = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_token}",
        )
        self.assertEqual(post.status_code, 405)
        self.assertEqual(post.json()["error"]["code"], "method_not_allowed")
        preflight = self.client.options(
            self.url,
            HTTP_ORIGIN="https://untrusted.invalid",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        self.assert_generic_401(preflight)
        for response in (post, preflight):
            self.assertNotIn("Access-Control-Allow-Origin", response.headers)
            self.assertNotIn("Access-Control-Allow-Credentials", response.headers)
            self.assertIn("private", response.headers["Cache-Control"])
        missing = self.client.get("/api/v1/admin/not-a-resource")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "not_found")


class AdminAPIContractHelperTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def test_strict_json_duplicate_bounds_and_media_type(self) -> None:
        valid = self.factory.post(
            "/fixture",
            data=b'{"name":"value"}',
            content_type="application/json; charset=utf-8",
        )
        self.assertEqual(parse_json_object(valid), {"name": "value"})
        invalid_requests = (
            self.factory.post(
                "/fixture",
                data=b'{"name":1,"name":2}',
                content_type="application/json",
            ),
            self.factory.post(
                "/fixture",
                data=b"[]",
                content_type="application/json",
            ),
            self.factory.post(
                "/fixture",
                data=b'{"value":NaN}',
                content_type="application/json",
            ),
            self.factory.post(
                "/fixture",
                data=b"{}",
                content_type="text/plain",
            ),
        )
        for request in invalid_requests:
            with self.subTest(content_type=request.content_type), self.assertRaises(APIError):
                parse_json_object(request)

    def test_etag_and_query_allowlists_are_exact(self) -> None:
        request = self.factory.patch("/fixture", HTTP_IF_MATCH='"rev-7"')
        self.assertEqual(require_if_match(request), 7)
        self.assertEqual(revision_etag(7), '"rev-7"')
        for value in (None, "*", 'W/"rev-7"', '"rev-0"', '"rev-7", "rev-8"'):
            headers = {} if value is None else {"If-Match": value}
            with self.subTest(value=value), self.assertRaises(APIError):
                require_if_match(self.factory.patch("/fixture", headers=headers))

        query = QueryDict("page=2&page_size=20&sort=-created_at&status=ready")
        parsed = parse_page_query(
            query,
            filter_fields=("status",),
            sort_fields=("created_at",),
        )
        self.assertEqual((parsed.page, parsed.page_size), (2, 20))
        for raw in ("unknown=1", "page=1&page=2", "page_size=101", "sort=email"):
            with self.subTest(raw=raw), self.assertRaises(APIError):
                parse_page_query(
                    QueryDict(raw),
                    filter_fields=("status",),
                    sort_fields=("created_at",),
                )

    def test_openapi_and_runtime_registry_are_bidirectional_and_fixture_free(self) -> None:
        document = generate_document()
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertEqual(parity_errors(), ())
        self.assertEqual(document["servers"], [{"url": "/api/v1/admin"}])
        health = document["paths"]["/health"]["get"]
        self.assertEqual(health["operationId"], "admin.health.read")
        self.assertEqual(
            document["components"]["securitySchemes"]["BearerAuth"]["scheme"],
            "bearer",
        )
        rendered = render_document()
        self.assertNotIn("TokenAuth", rendered)
        self.assertNotIn("_fixtures", rendered)
        for fixture_route in (
            "/api/v1/admin/_fixtures/credentials",
            "/api/v1/admin/_fixtures/bulk",
            "/api/v1/admin/_fixtures/operations/not-a-uuid",
            "/studio/_fixtures/credentials/",
        ):
            with self.subTest(route=fixture_route), self.assertRaises(Resolver404):
                resolve(fixture_route)

    def test_runtime_parity_detects_service_result_and_audit_binding_drift(self) -> None:
        operation = next(
            item
            for item in runtime_operations()
            if item.capability_key == "management.credentials.rotate"
        )
        self.assertTrue(callable(operation.service))
        self.assertEqual(operation.result_schema, "CredentialSecret")
        self.assertEqual(operation.audit_action, "management.credential.rotated")
        callback = resolve(
            "/api/v1/admin/credentials/00000000-0000-0000-0000-000000000001/rotate"
        ).func
        cases = (
            ("management_service", object(), "admin service drifted"),
            ("management_result_schema", "DriftedResult", "admin result schema drifted"),
            ("management_audit_action", "management.drifted", "admin audit action drifted"),
        )
        for attribute, value, expected in cases:
            with self.subTest(attribute=attribute), patch.object(callback, attribute, value):
                self.assertTrue(
                    any(expected in error for error in parity_errors()),
                    parity_errors(),
                )
