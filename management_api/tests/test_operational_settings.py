"""The admin API surface for the operator-tunable runtime settings."""

from __future__ import annotations

import json
import uuid

from django.test import TestCase, override_settings

from accounts.development_owner import bootstrap_development_owner
from accounts.studio_roles import MANAGE_API_CREDENTIALS
from core.bootstrap import RuntimeEnvironment
from core.models import AuditEvent, OperationalSetting, OperationalSettingRevision
from core.operational_settings import OPERATIONAL_SETTING_KEYS
from core.runtime_config import get_setting, reset_runtime_settings_cache
from management_api.openapi import generate_document
from management_auth.models import APIPrincipal, APIRateAdmission
from management_auth.services import issue_credential_once

OWNER_EMAIL = "operational-settings-owner@example.test"
OWNER_PASSWORD = "operational-settings-owner-password-114"


@override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST)
class AdminOperationalSettingsTests(TestCase):
    def setUp(self) -> None:
        reset_runtime_settings_cache()
        self.addCleanup(reset_runtime_settings_cache)
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
            name="Operational settings actor",
            scopes=("settings.operational.read", "settings.operational.write"),
            idempotency_key="operational-settings-actor",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        self.token = str(issued.response["token"])
        self.url = "/api/v1/admin/settings/operational"

    def get(self, *, token: str | None = None):
        return self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {token or self.token}")

    def patch(self, payload: object, *, key: str | None = None, token: str | None = None):
        return self.client.patch(
            self.url,
            data=json.dumps(payload),
            content_type="application/json; charset=utf-8",
            HTTP_AUTHORIZATION=f"Bearer {token or self.token}",
            HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()),
        )

    def clear_rate_admissions(self) -> None:
        APIRateAdmission.objects.all().delete()

    def assert_private_json(self, response, status: int) -> None:
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertTrue(response.headers["Content-Type"].startswith("application/json"))

    def test_get_lists_every_key_with_definition_and_effective_value(self) -> None:
        response = self.get()
        self.assert_private_json(response, 200)
        settings = response.json()["settings"]
        self.assertEqual([item["key"] for item in settings], list(OPERATIONAL_SETTING_KEYS))
        by_key = {item["key"]: item for item in settings}

        prefix = by_key["public_media.s3_prefix"]
        self.assertEqual(prefix["value_type"], "string")
        self.assertEqual(prefix["source"], "code_default")
        self.assertEqual(prefix["revision"], 0)
        self.assertEqual(prefix["sensitivity"], "operational")
        self.assertEqual(prefix["cache_policy"], "stamped")
        self.assertEqual(prefix["env_var"], "PUBLIC_MEDIA_S3_PREFIX")
        self.assertEqual(prefix["settings_attr"], "PUBLIC_MEDIA_S3_PREFIX")
        # The stored row is absent, so the effective value names the layer that
        # actually answers today rather than pretending the default is in use.
        self.assertEqual(prefix["effective_value"], "public-projection")
        self.assertEqual(prefix["effective_layer"], "settings")

    def test_patch_applies_a_batch_and_the_running_process_sees_it(self) -> None:
        self.assertEqual(get_setting("public_media.s3_bucket"), "")
        self.clear_rate_admissions()
        # The cache is dropped on commit, so the commit has to actually run for
        # the "no restart" claim to be the thing under test.
        with self.captureOnCommitCallbacks(execute=True):
            response = self.patch(
                {
                    "updates": [
                        {
                            "key": "public_media.s3_bucket",
                            "value": "dtc-website-media",
                            "expected_revision": 0,
                        },
                        {
                            "key": "public_media.max_object_bytes",
                            "value": 12 * 1024 * 1024,
                            "expected_revision": 0,
                        },
                    ]
                }
            )
        self.assert_private_json(response, 200)
        body = response.json()
        self.assertFalse(body["replayed"])
        self.assertEqual(
            [item["key"] for item in body["settings"]],
            ["public_media.max_object_bytes", "public_media.s3_bucket"],
        )
        self.assertTrue(all(item["changed"] for item in body["settings"]))
        self.assertTrue(all(item["source"] == "admin_api" for item in body["settings"]))

        self.assertEqual(
            OperationalSetting.objects.filter(key="public_media.s3_bucket").get().value,
            "dtc-website-media",
        )
        self.assertEqual(
            OperationalSettingRevision.objects.filter(key="public_media.s3_bucket").count(),
            1,
        )
        self.assertEqual(
            AuditEvent.objects.filter(action="core.runtime_settings.batch_updated").count(),
            1,
        )
        # No restart: the write invalidated this process's cache on commit.
        self.assertEqual(get_setting("public_media.s3_bucket"), "dtc-website-media")
        self.assertEqual(get_setting("public_media.max_object_bytes"), 12 * 1024 * 1024)

    def test_a_stale_expected_revision_conflicts_and_changes_nothing(self) -> None:
        self.clear_rate_admissions()
        self.patch(
            {
                "updates": [
                    {
                        "key": "public_media.s3_bucket",
                        "value": "first",
                        "expected_revision": 0,
                    }
                ]
            }
        )
        self.clear_rate_admissions()
        conflict = self.patch(
            {
                "updates": [
                    {
                        "key": "public_media.s3_bucket",
                        "value": "second",
                        "expected_revision": 0,
                    }
                ]
            }
        )
        self.assert_private_json(conflict, 409)
        self.assertEqual(conflict.json()["error"]["code"], "revision_conflict")
        self.assertEqual(
            OperationalSetting.objects.get(key="public_media.s3_bucket").value,
            "first",
        )

    def test_replaying_one_idempotency_key_does_not_write_twice(self) -> None:
        payload = {
            "updates": [
                {
                    "key": "public_media.s3_prefix",
                    "value": "replayed-prefix",
                    "expected_revision": 0,
                }
            ]
        }
        key = str(uuid.uuid4())
        self.clear_rate_admissions()
        first = self.patch(payload, key=key)
        self.assert_private_json(first, 200)
        self.assertFalse(first.json()["replayed"])
        self.clear_rate_admissions()
        second = self.patch(payload, key=key)
        self.assert_private_json(second, 200)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(
            OperationalSettingRevision.objects.filter(key="public_media.s3_prefix").count(),
            1,
        )

    def test_a_value_the_validator_refuses_is_rejected_whole(self) -> None:
        self.clear_rate_admissions()
        response = self.patch(
            {
                "updates": [
                    {
                        "key": "site.origin.canonical_host",
                        "value": "https://elsewhere.example",
                        "expected_revision": 0,
                    },
                    {
                        "key": "public_media.s3_bucket",
                        "value": "would-have-been-written",
                        "expected_revision": 0,
                    },
                ]
            }
        )
        self.assert_private_json(response, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")
        self.assertFalse(OperationalSetting.objects.exists())

    def test_a_secret_bearing_key_is_not_reachable_through_this_endpoint(self) -> None:
        self.clear_rate_admissions()
        response = self.patch(
            {
                "updates": [
                    {
                        "key": "datamailer.api_key",
                        "value": "anything",
                        "expected_revision": 0,
                    }
                ]
            }
        )
        self.assert_private_json(response, 400)
        self.assertFalse(OperationalSetting.objects.exists())

    def test_the_public_announcement_keys_are_out_of_this_scope(self) -> None:
        self.clear_rate_admissions()
        response = self.patch(
            {
                "updates": [
                    {
                        "key": "site.announcement.enabled",
                        "value": True,
                        "expected_revision": 0,
                    }
                ]
            }
        )
        self.assert_private_json(response, 400)

    def test_an_unauthenticated_request_is_refused(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_a_credential_without_the_write_scope_cannot_write(self) -> None:
        issued = issue_credential_once(
            actor_principal=self.human,
            target_principal_id=self.human.id,
            name="Operational settings reader",
            scopes=("settings.operational.read",),
            idempotency_key="operational-settings-reader",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        self.clear_rate_admissions()
        response = self.patch(
            {
                "updates": [
                    {
                        "key": "public_media.s3_bucket",
                        "value": "denied",
                        "expected_revision": 0,
                    }
                ]
            },
            token=str(issued.response["token"]),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(OperationalSetting.objects.exists())

    def test_post_is_not_an_allowed_method_on_the_operational_collection(self) -> None:
        self.clear_rate_admissions()
        response = self.client.post(
            self.url,
            data=json.dumps({"updates": []}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, 405)

    def test_the_openapi_document_declares_both_operations(self) -> None:
        document = generate_document()
        operations = document["paths"]["/settings/operational"]
        self.assertEqual(operations["get"]["operationId"], "settings.operational.read")
        self.assertEqual(operations["patch"]["operationId"], "settings.operational.write")
        schema = document["components"]["schemas"]["OperationalSetting"]
        self.assertEqual(
            schema["properties"]["key"]["enum"],
            list(OPERATIONAL_SETTING_KEYS),
        )
