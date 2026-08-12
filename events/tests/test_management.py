from __future__ import annotations

import csv
import hashlib
import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from content.public_data import public_projection
from events.models import HistoricalEventMapping, HistoricalRegistrationSourceRun
from management_api.concurrency import revision_etag
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import (
    create_principal,
    issue_credential_once,
    principal_has_permission,
)
from management_registry import CAPABILITY_REGISTRY


def tree_checksum(root: Path) -> str:
    digest = hashlib.sha256(b"dtc-protected-tree-v1\0")
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


class HistoricalRegistrationManagementTests(TestCase):
    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.source = Path(self.temporary.name) / "source"
        self.source.mkdir()
        self.event = public_projection()["events"][0]
        self.external_id = "synthetic-management-event"
        self.external_url = "https://example.test/synthetic-management-event"
        (self.source / "synthetic.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": self.external_id,
                    "event_url": self.external_url,
                }
            ),
            encoding="utf-8",
        )
        with (self.source / "synthetic.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=("event_id", "guest_id", "approval_status"),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "event_id": self.external_id,
                    "guest_id": "synthetic-registration",
                    "approval_status": "approved",
                }
            )
        provenance = self.event["provenance"]
        self.registry = {
            "synthetic-management-luma": {
                "provider": "luma",
                "reconciliation_profile": "synthetic",
                "path": str(self.source),
                "sha256": tree_checksum(self.source),
                "mapping_bridge": {
                    self.external_url: {
                        "repository": provenance["repository"],
                        "revision": provenance["revision"],
                        "source_key": provenance["source_key"],
                        "slug": self.event["slug"],
                    }
                },
            }
        }
        permissions = tuple(
            Permission.objects.filter(
                content_type__app_label="events",
                codename__in=(
                    "historical_registration_import_manage",
                    "historical_registration_mapping_manage",
                ),
            )
        )
        self.principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="Synthetic historical automation",
            identity_snapshot="service:synthetic-historical",
            permissions=permissions,
        )
        scopes = (
            "events.historical_registration_import.manage",
            "events.historical_registration_import.create",
            "events.historical_registration_import.detail",
            "events.historical_registration_import.dry-run",
            "events.historical_registration_import.validate",
            "events.historical_registration_import.activate",
            "events.historical_registration_import.cancel",
            "events.historical_registration_import.rollback",
            "events.historical_registration_mapping.manage",
            "events.historical_registration_mapping.create",
            "events.historical_registration_mapping.update",
            "events.historical_registration_total.read",
        )
        issued = issue_credential_once(
            actor_principal=self.principal,
            target_principal_id=self.principal.id,
            name="Synthetic historical credential",
            scopes=scopes,
            idempotency_key="synthetic-historical-credential",
            actor_permission="events.historical_registration_import_manage",
        )
        self.token = str(issued.response["token"])
        self.credential = APICredential.objects.get(id=str(issued.response["credential_id"]))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _headers(self, *, key: str | None = None, revision: int | None = None) -> dict:
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        if key is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        if revision is not None:
            headers["HTTP_IF_MATCH"] = revision_etag(revision)
        return headers

    def test_api_parity_stages_maps_validates_activates_and_reads_safe_total(self) -> None:
        capability = CAPABILITY_REGISTRY.require("events.historical_registration_import.create")
        self.assertIn(capability.key, self.credential.scopes)
        self.assertTrue(principal_has_permission(self.principal, capability.django_permission))
        self.assertIsNotNone(capability.function_policy)
        assert capability.function_policy is not None
        self.assertTrue(capability.function_policy(self.principal, self.credential))
        with override_settings(HISTORICAL_REGISTRATION_SOURCES=self.registry):
            created = self.client.post(
                "/api/v1/admin/historical-registration-imports",
                data=json.dumps(
                    {
                        "provider": "luma",
                        "source_reference": "synthetic-management-luma",
                        "mapping_set_revision": 1,
                    }
                ),
                content_type="application/json",
                **self._headers(key="synthetic-stage"),
            )
        self.assertEqual(created.status_code, 201, created.content)
        run_id = created.json()["id"]
        self.assertNotIn("source_reference", created.json())

        listing = self.client.get(
            "/api/v1/admin/historical-registration-imports", **self._headers()
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["total_count"], 1)

        mappings = self.client.get("/api/v1/admin/historical-event-mappings", **self._headers())
        self.assertEqual(mappings.status_code, 200)
        mapping_payload = mappings.json()["items"][0]
        self.assertEqual(mapping_payload["external_event_identifier"], self.external_id)

        updated = self.client.patch(
            f"/api/v1/admin/historical-event-mappings/{mapping_payload['id']}",
            data=json.dumps(
                {
                    "state": HistoricalEventMapping.State.MAPPED,
                    "event_id": self.event["identity_id"],
                    "mapping_set_revision": 1,
                    "reason_code": "exact_review",
                    "reason": "Synthetic exact mapping.",
                    "coverage_boundary": "historical",
                    "combination_policy": "replacement",
                }
            ),
            content_type="application/json",
            **self._headers(revision=mapping_payload["revision"]),
        )
        self.assertEqual(updated.status_code, 200, updated.content)

        for action in ("validate", "activate"):
            response = self.client.post(
                f"/api/v1/admin/historical-registration-imports/{run_id}/{action}",
                data=json.dumps({"confirmed": True, "reason_code": f"synthetic_{action}"}),
                content_type="application/json",
                **self._headers(key=f"synthetic-{action}"),
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["run_id"], run_id)

        preview = self.client.get(
            f"/api/v1/admin/events/{self.event['identity_id']}/registration-total",
            **self._headers(),
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["count"], 1)
        self.assertEqual(preview.json()["contributions"][0]["provider"], "luma")
        self.assertNotIn(self.external_id, json.dumps(preview.json()))
        slug_preview = self.client.get(
            f"/api/v1/admin/events/{self.event['slug']}/registration-total",
            **self._headers(),
        )
        self.assertEqual(slug_preview.status_code, 404)
        self.assertEqual(
            HistoricalRegistrationSourceRun.objects.get().state,
            HistoricalRegistrationSourceRun.State.ACTIVE,
        )

    def test_api_denies_missing_scope_and_requires_idempotency_and_if_match(self) -> None:
        anonymous = self.client.get("/api/v1/admin/historical-registration-imports")
        self.assertEqual(anonymous.status_code, 401)

        with override_settings(HISTORICAL_REGISTRATION_SOURCES=self.registry):
            missing_key = self.client.post(
                "/api/v1/admin/historical-registration-imports",
                data=json.dumps(
                    {
                        "provider": "luma",
                        "source_reference": "synthetic-management-luma",
                        "mapping_set_revision": 1,
                    }
                ),
                content_type="application/json",
                **self._headers(),
            )
        self.assertEqual(missing_key.status_code, 400)
        self.assertEqual(missing_key.json()["error"]["code"], "invalid_idempotency_key")

        with override_settings(HISTORICAL_REGISTRATION_SOURCES=self.registry):
            staged = self.client.post(
                "/api/v1/admin/historical-registration-imports",
                data=json.dumps(
                    {
                        "provider": "luma",
                        "source_reference": "synthetic-management-luma",
                        "mapping_set_revision": 1,
                    }
                ),
                content_type="application/json",
                **self._headers(key="precondition-stage"),
            )
        self.assertEqual(staged.status_code, 201)
        mapping = self.client.get(
            "/api/v1/admin/historical-event-mappings", **self._headers()
        ).json()["items"][0]
        precondition = self.client.patch(
            f"/api/v1/admin/historical-event-mappings/{mapping['id']}",
            data=json.dumps(
                {
                    "state": "mapped",
                    "event_id": self.event["identity_id"],
                    "mapping_set_revision": 1,
                    "reason_code": "exact_review",
                    "reason": "Synthetic exact mapping.",
                    "coverage_boundary": "historical",
                    "combination_policy": "replacement",
                }
            ),
            content_type="application/json",
            **self._headers(),
        )
        self.assertEqual(precondition.status_code, 428)
        self.assertEqual(precondition.json()["error"]["code"], "precondition_required")


class HistoricalRegistrationStudioAccessTests(TestCase):
    def test_event_operator_has_private_safe_routes_and_other_operator_is_denied(self) -> None:
        event_operator = make_studio_user(
            username="synthetic-event-operator",
            roles=("event_operator",),
        )
        client = authenticated_studio_client(event_operator)
        for path in (
            "/studio/events/historical-registration-totals/",
            "/studio/events/historical-registration-totals/mappings/",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("private", response.headers["Cache-Control"])
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
            self.assertNotContains(response, "attendee-card")

        content_operator = make_studio_user(
            username="synthetic-content-operator",
            roles=("content_operator",),
        )
        denied = authenticated_studio_client(content_operator).get(
            "/studio/events/historical-registration-totals/"
        )
        self.assertEqual(denied.status_code, 403)
