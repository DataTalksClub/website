"""Tests for the event management admin API, Studio parity, and public registration extensions."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.studio_roles import set_single_studio_role, synchronize_studio_roles
from management_auth.constants import DEFAULT_CREDENTIAL_LIFETIME, DIGEST_ALGORITHM, DIGEST_VERSION
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal
from management_auth.tokens import encode_secret, generate_token

User = get_user_model()

# Minimal valid 1x1 PNG
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class EventAdminAPITests(TestCase):
    def setUp(self) -> None:
        synchronize_studio_roles()
        self.operator = User.objects.create_user(
            username="event-op",
            email="event-op@dtcdev.click",
            password="pw",
            is_staff=True,
        )
        set_single_studio_role(self.operator, "event_operator")
        self.principal = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="Event Operator",
            identity_snapshot="event-op",
            user=self.operator,
            created_by=self.operator,
        )
        group = self.operator.groups.first()
        assert group is not None
        self.principal.permissions.set(group.permissions.all())
        scopes = [
            "events.manage.list",
            "events.manage.detail",
            "events.manage.create",
            "events.manage.update",
            "events.manage.publish",
            "events.manage.cancel",
            "events.manage.complete",
            "events.manage.banner",
            "events.manage.registrants.export",
            "events.manage.attendance.import",
            "events.manage.message",
            "events.manage.reminders",
            "events.manage.speakers.list",
            "events.manage.speakers.create",
        ]
        generated = generate_token()
        APICredential.objects.create(
            principal=self.principal,
            name="test-token",
            prefix=generated.prefix,
            digest_algorithm=DIGEST_ALGORITHM,
            digest_version=DIGEST_VERSION,
            secret_digest=encode_secret(generated.secret),
            scopes=scopes,
            expires_at=timezone.now() + DEFAULT_CREDENTIAL_LIFETIME,
            created_by=self.operator,
        )
        self.token = generated.raw
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.client = Client()

    def test_full_event_lifecycle_speakers_banner_registrants_and_reminders(self) -> None:
        # 1. Create speaker
        resp = self.client.post(
            "/api/v1/admin/events/speakers",
            data=json.dumps(
                {"short": "alexeygrigorev", "name": "Alexey Grigorev", "title": "Founder"}
            ),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["speaker"]["short"], "alexeygrigorev")

        # 2. Create event (published by default)
        start = (timezone.now() + timedelta(days=7)).replace(microsecond=0)
        end = start + timedelta(hours=1)
        resp = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps(
                {
                    "title": "CLI Test Webinar",
                    "type": "webinar",
                    "description": "Testing the event CLI.",
                    "start_datetime": start.isoformat(),
                    "end_datetime": end.isoformat(),
                    "timezone": "Europe/Berlin",
                    "location": "https://youtube.com/live/test",
                    "speakers": ["alexeygrigorev"],
                    "tags": ["mlops"],
                    "sponsor_sharing_enabled": True,
                    "collect_company": True,
                    "sponsor_name": "Acme Corp",
                    "reminder_intervals": "24h",
                    "publish": True,
                }
            ),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        event_data = resp.json()["event"]
        event_uuid = event_data["id"]
        public_id = event_data["public_id"]
        self.assertEqual(event_data["status"], "upcoming")
        self.assertIsNotNone(public_id)
        self.assertEqual(event_data["reminder_intervals"], ["24h"])
        self.assertTrue(event_data["sponsor_sharing_enabled"])
        self.assertTrue(event_data["collect_company"])

        # 3. Upload banner and verify public serve
        resp = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/banner",
            data=json.dumps(
                {
                    "content_type": "image/png",
                    "image_base64": base64.b64encode(TINY_PNG).decode("ascii"),
                }
            ),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        banner_resp = self.client.get(f"/events/{public_id}/banner")
        self.assertEqual(banner_resp.status_code, 200)
        self.assertEqual(banner_resp["Content-Type"], "image/png")

        # 4. Public registration with company + sponsor sharing + newsletter consent
        reg_resp = self.client.post(
            f"/events/{public_id}/{event_data['slug']}/register",
            data={
                "email": "dev+test@dtcdev.click",
                "display_name": "Dev Tester",
                "company": "Test Co",
                "newsletter_consent": "true",
                "sponsor_sharing_consent": "true",
            },
        )
        self.assertEqual(reg_resp.status_code, 302)
        self.assertIn("registered=ok", reg_resp["Location"])

        # 5. Export registrants
        export_resp = self.client.get(
            f"/api/v1/admin/events/{event_uuid}/registrants",
            headers=self.auth_headers,
        )
        self.assertEqual(export_resp.status_code, 200)
        items = export_resp.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["email"], "dev+test@dtcdev.click")
        self.assertEqual(items[0]["company"], "Test Co")
        self.assertTrue(items[0]["sponsor_sharing_consent"])
        self.assertTrue(items[0]["newsletter_consent"])

        # 6. Import attendance
        att_resp = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/attendance",
            data=json.dumps({"emails": ["dev+test@dtcdev.click"], "status": "attended"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(att_resp.status_code, 200)
        self.assertEqual(att_resp.json()["updated"], 1)

        # 7. Message registrants (dry-run and real)
        msg_dry = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/message",
            data=json.dumps({"subject": "Hi", "body": "Body", "dry_run": True}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(msg_dry.status_code, 200)
        self.assertEqual(msg_dry.json()["recipient_count"], 1)
        self.assertEqual(msg_dry.json()["sent"], 0)

        with patch("events.admin_services.send_mail") as mock_send:
            msg_live = self.client.post(
                f"/api/v1/admin/events/{event_uuid}/message",
                data=json.dumps({"subject": "Hi", "body": "Body", "dry_run": False}),
                content_type="application/json",
                headers=self.auth_headers,
            )
            self.assertEqual(msg_live.status_code, 200)
            self.assertEqual(msg_live.json()["sent"], 1)
            mock_send.assert_called_once()

        # 8. Update reminders
        rem_resp = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/reminders",
            data=json.dumps({"reminder_intervals": "24h,20m"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(rem_resp.status_code, 200)
        self.assertEqual(rem_resp.json()["reminder_intervals"], ["24h", "20m"])

        # 9. Cancel event
        cancel_resp = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/cancel",
            data=json.dumps({"reason": "Test complete"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertEqual(cancel_resp.json()["event"]["status"], "cancelled")

    def test_draft_publish_and_complete(self) -> None:
        start = datetime(2026, 12, 1, 17, 0, tzinfo=UTC)
        resp = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps(
                {
                    "title": "Draft Podcast",
                    "type": "podcast",
                    "season": 20,
                    "episode": 5,
                    "start_datetime": start.isoformat(),
                    "publish": False,
                }
            ),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(resp.status_code, 201)
        event_uuid = resp.json()["event"]["id"]
        self.assertEqual(resp.json()["event"]["status"], "draft")

        pub_resp = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/publish",
            data="{}",
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(pub_resp.status_code, 200, pub_resp.content)
        self.assertEqual(pub_resp.json()["event"]["status"], "upcoming")

        comp_resp = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/complete",
            data=json.dumps({"recording_url": "https://youtube.com/watch?v=abc"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(comp_resp.status_code, 200)
        self.assertEqual(comp_resp.json()["event"]["status"], "completed")
        self.assertEqual(
            comp_resp.json()["event"]["recording_url"], "https://youtube.com/watch?v=abc"
        )
