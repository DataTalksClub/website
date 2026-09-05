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
from events.importers import (
    ProtectedSourceError,
    registered_source_options,
    resolve_registered_source_reference,
    source_reference_digest,
)
from events.models import HistoricalRegistrationSourceRun
from events.queries import published_event_records
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
        self.event = published_event_records()[0]
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
                codename__in=("historical_registration_import_manage",),
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

    def test_api_parity_stages_resolves_validates_activates_and_reads_safe_total(self) -> None:
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

        # The registry's own `mapping_bridge` names the exact provider-event-to-
        # canonical-event pair, so staging resolves the aggregate directly --
        # there is no separate mapping row or review step to act on afterward.
        detail = self.client.get(
            f"/api/v1/admin/historical-registration-imports/{run_id}", **self._headers()
        )
        self.assertEqual(detail.status_code, 200)
        aggregate_payload = detail.json()["aggregates"][0]
        self.assertTrue(aggregate_payload["resolved"])
        self.assertEqual(aggregate_payload["event_id"], self.event["identity_id"])
        self.assertNotIn(self.external_id, json.dumps(detail.json()))

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

    def test_api_denies_missing_scope_and_requires_idempotency_key(self) -> None:
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


class HistoricalRegistrationStudioAccessTests(TestCase):
    def test_reserved_source_tokens_resolve_collisions_without_disclosure(self) -> None:
        normal_key = "synthetic-normal-source"
        collision_key = f"source:{source_reference_digest(normal_key)}"
        registry = {
            normal_key: {"provider": "eventbrite"},
            collision_key: {"provider": "luma"},
        }
        user = make_studio_user(
            username="synthetic-source-collision-operator",
            roles=("event_operator",),
        )

        with override_settings(HISTORICAL_REGISTRATION_SOURCES=registry):
            response = authenticated_studio_client(user).get(
                "/studio/events/historical-registration-totals/"
            )
            options = registered_source_options()
            resolved_by_label = {
                option["label"]: resolve_registered_source_reference(option["value"])
                for option in options
            }
            direct_raw_selection = resolve_registered_source_reference(collision_key)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, normal_key)
        self.assertNotContains(response, collision_key)
        self.assertEqual(
            resolved_by_label,
            {
                "Eventbrite historical registration source": normal_key,
                "Luma historical registration source": collision_key,
            },
        )
        self.assertEqual(direct_raw_selection, collision_key)
        self.assertEqual(len(options), 2)
        self.assertTrue(
            all(
                option["value"].startswith("__dtc_historical_source_token_v1__:")
                for option in options
            )
        )

    def test_reserved_source_tokens_reject_malformed_and_unknown_values(self) -> None:
        source_key = "synthetic-token-validation-source"
        registry = {source_key: {"provider": "luma"}}
        with override_settings(HISTORICAL_REGISTRATION_SOURCES=registry):
            valid_token = registered_source_options()[0]["value"]
            malformed_token = f"{valid_token[:-1]}g"
            unknown_token = f"{valid_token[: valid_token.index(':') + 1]}{'0' * 64}"

            with self.assertRaisesMessage(ProtectedSourceError, "source_reference_token_invalid"):
                resolve_registered_source_reference(malformed_token)
            with self.assertRaisesMessage(ProtectedSourceError, "source_reference_unregistered"):
                resolve_registered_source_reference(unknown_token)

    def test_source_choices_redact_synthetic_and_real_keys(self) -> None:
        user = make_studio_user(
            username="synthetic-source-redaction-operator",
            roles=("event_operator",),
        )
        synthetic_key = "synthetic-studio-source-key"
        real_luma_key = "real-luma-protected-source"
        real_eventbrite_key = "real-eventbrite-protected-source"
        registry = {
            synthetic_key: {"provider": "luma"},
            real_luma_key: {"provider": "luma"},
            real_eventbrite_key: {"provider": "eventbrite"},
        }
        with override_settings(HISTORICAL_REGISTRATION_SOURCES=registry):
            response = authenticated_studio_client(user).get(
                "/studio/events/historical-registration-totals/"
            )
            options = registered_source_options()
            selected_keys = {
                resolve_registered_source_reference(option["value"]) for option in options
            }

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Luma historical registration source")
        self.assertContains(response, "Eventbrite historical registration source")
        for source_key in (synthetic_key, real_luma_key, real_eventbrite_key):
            self.assertNotContains(response, source_key)
        self.assertEqual(len(options), 3)
        self.assertEqual(
            selected_keys,
            {synthetic_key, real_luma_key, real_eventbrite_key},
        )
        self.assertTrue(
            all(
                option["value"].startswith("__dtc_historical_source_token_v1__:")
                for option in options
            )
        )

    def test_mixed_type_source_registry_is_safe_for_studio_and_redacts_keys(self) -> None:
        user = make_studio_user(
            username="synthetic-mixed-source-registry-operator",
            roles=("event_operator",),
        )
        string_key = "synthetic-mixed-type-source-key"
        non_string_key = ("synthetic-non-string-source-key",)
        registry = {
            string_key: {"provider": "luma"},
            non_string_key: {"provider": "eventbrite"},
        }

        with override_settings(HISTORICAL_REGISTRATION_SOURCES=registry):
            response = authenticated_studio_client(user).get(
                "/studio/events/historical-registration-totals/"
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Luma historical registration source")
        self.assertNotContains(response, string_key)
        self.assertNotContains(response, str(non_string_key))

    def test_event_operator_has_private_safe_routes_and_other_operator_is_denied(self) -> None:
        event_operator = make_studio_user(
            username="synthetic-event-operator",
            roles=("event_operator",),
        )
        client = authenticated_studio_client(event_operator)
        for path in ("/studio/events/historical-registration-totals/",):
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

    def test_unauthorized_source_registry_is_not_rendered(self) -> None:
        content_operator = make_studio_user(
            username="synthetic-source-redaction-denied",
            roles=("content_operator",),
        )
        source_key = "synthetic-denied-source-key"
        with override_settings(HISTORICAL_REGISTRATION_SOURCES={source_key: {"provider": "luma"}}):
            denied = authenticated_studio_client(content_operator).get(
                "/studio/events/historical-registration-totals/"
            )
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn(source_key, denied.content.decode())
