"""The CI-triggered content sync accepts only the content-sync workflow's OIDC token."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import jwt
import yaml
from community_base.content_sync.models import ContentSource, SyncLog, SyncStatus
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from content_sync import ci_trigger

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CONFIG = settings.CONTENT_SYNC_CI


def claims(**overrides):
    now = datetime.now(UTC)
    base = {
        "iss": ci_trigger.GITHUB_OIDC_ISSUER,
        "aud": CONFIG["audience"],
        "sub": "repo:DataTalksClub/website:ref:refs/heads/main",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "repository": CONFIG["repository"],
        "repository_id": str(CONFIG["repository_id"]),
        "ref": "refs/heads/main",
        "workflow_ref": (
            "DataTalksClub/website/.github/workflows/content-sync.yml@refs/heads/main"
        ),
        "event_name": "schedule",
        "run_id": "123",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def token(key=PRIVATE_KEY, **overrides):
    return jwt.encode(claims(**overrides), key, algorithm="RS256", headers={"kid": "k1"})


class CITriggerTests(TestCase):
    def setUp(self):
        self.source, _ = ContentSource.objects.update_or_create(
            slug="dtc-content",
            defaults={
                "repo_name": "DataTalksClub/content",
                "webhook_secret": "synthetic",
                "is_enabled": True,
                "last_synced_commit": "a" * 40,
                "last_sync_status": SyncStatus.SUCCESS,
            },
        )
        patcher = patch.object(ci_trigger, "signing_key", return_value=PRIVATE_KEY.public_key())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = reverse("content-sync-ci")

    def get(self, bearer, source="dtc-content"):
        return self.client.get(self.url, {"source": source}, HTTP_AUTHORIZATION=f"Bearer {bearer}")

    def post(self, bearer, body):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {bearer}",
        )

    def test_get_reports_sync_state(self):
        response = self.get(token())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["last_synced_commit"], "a" * 40)
        self.assertTrue(response.json()["enabled"])

    def test_refuses_tokens_not_from_the_content_sync_workflow(self):
        cases = {
            "missing_token": None,
            "invalid_token": token(key=OTHER_KEY),
            "unexpected_repository": token(repository="attacker/website"),
            "unexpected_repository_id": token(repository_id="1"),
            "unexpected_ref": token(ref="refs/heads/feature"),
            "unexpected_workflow_ref": token(
                workflow_ref="DataTalksClub/website/.github/workflows/ci.yml@refs/heads/main"
            ),
            "unexpected_event_name": token(event_name="pull_request"),
        }
        for code, bearer in cases.items():
            with self.subTest(code=code):
                headers = {} if bearer is None else {"HTTP_AUTHORIZATION": f"Bearer {bearer}"}
                response = self.client.get(self.url, {"source": "dtc-content"}, **headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": code})

    def test_refuses_wrong_audience_and_expired_tokens(self):
        expired = datetime.now(UTC) - timedelta(minutes=10)
        for bearer in (
            token(aud="https://example.com/"),
            token(exp=expired, iat=expired - timedelta(minutes=5), nbf=None),
        ):
            with self.subTest(bearer=bearer[-12:]):
                self.assertEqual(self.get(bearer).status_code, 401)

    def test_refuses_undeclared_source(self):
        response = self.get(token(), source="dtc-main-site")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "source_not_allowed"})

    def test_post_starts_the_sync_and_answers_at_once(self):
        with patch.object(ci_trigger, "start_sync") as start:
            response = self.post(
                token(event_name="workflow_dispatch"), {"source": "dtc-content", "force": True}
            )

        self.assertEqual(response.status_code, 202)
        start.assert_called_once_with(self.source.pk, True)
        self.assertIn("requested_at", response.json())

    def test_get_reports_the_latest_sync_log(self):
        SyncLog.objects.create(
            source=self.source, status=SyncStatus.FAILED, errors=["academy: bad field"]
        )

        latest = self.get(token()).json()["latest_sync"]

        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["errors"], ["academy: bad field"])

    def test_run_sync_calls_the_engine_with_the_requested_force(self):
        with (
            patch.object(ci_trigger, "sync_content_source") as sync,
            patch.object(ci_trigger.connections, "close_all") as close_all,
        ):
            ci_trigger.run_sync(self.source.pk, False)

        sync.assert_called_once_with(self.source, force=False)
        close_all.assert_called_once_with()

    def test_post_rejects_malformed_force(self):
        response = self.post(token(), {"source": "dtc-content", "force": "yes"})

        self.assertEqual(response.status_code, 400)


class WorkflowContractTests(TestCase):
    def test_workflow_matches_the_endpoint_contract(self):
        path = ROOT / CONFIG["workflow_path"]
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = workflow[True]  # YAML 1.1 reads the `on` key as True.

        self.assertEqual(set(triggers), set(CONFIG["events"]))
        self.assertEqual(workflow["permissions"], {"contents": "read", "id-token": "write"})
        text = path.read_text(encoding="utf-8")
        self.assertIn(CONFIG["audience"], text)
        self.assertNotIn("secrets.", text)
