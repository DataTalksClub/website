"""Tests for the event management admin API, Studio parity, and public registration extensions."""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.studio_roles import set_single_studio_role, synchronize_studio_roles
from management_auth.constants import DEFAULT_CREDENTIAL_LIFETIME, DIGEST_ALGORITHM, DIGEST_VERSION
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal
from management_auth.tokens import encode_secret, generate_token
from test_support.event_pictures import TEST_PICTURE_PAYLOAD

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
                    "picture": TEST_PICTURE_PAYLOAD,
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
        # Only an event with a type and a speaker can be published.
        speaker = self.client.post(
            "/api/v1/admin/events/speakers",
            data=json.dumps({"short": "alexeygrigorev", "name": "Alexey Grigorev"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(speaker.status_code, 201, speaker.content)
        start = (timezone.now() + timedelta(days=30)).replace(microsecond=0)
        resp = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps(
                {
                    "picture": TEST_PICTURE_PAYLOAD,
                    "title": "Draft Podcast",
                    "description": "About it.",
                    "type": "podcast",
                    "season": 20,
                    "episode": 5,
                    "speakers": ["alexeygrigorev"],
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

        # Completion follows from the schedule: an event that has not ended
        # cannot be completed by hand.
        early = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/complete",
            data=json.dumps({"recording_url": "https://youtube.com/watch?v=abc"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(early.status_code, 400, early.content)

        from community_base.events.models import Event

        past_start = timezone.now() - timedelta(days=2)
        Event.objects.filter(content_id=event_uuid).update(
            start_datetime=past_start, end_datetime=past_start + timedelta(hours=1)
        )
        comp_resp = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/complete",
            data=json.dumps({"recording_url": "https://youtube.com/watch?v=abc"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(comp_resp.status_code, 200)
        self.assertEqual(comp_resp.json()["event"]["status"], "past")
        # Nothing time-derived was written: the row stays published.
        self.assertEqual(Event.objects.get(content_id=event_uuid).status, "upcoming")
        self.assertEqual(
            comp_resp.json()["event"]["recording_url"], "https://youtube.com/watch?v=abc"
        )

    def test_chunked_banner_upload_exceeding_waf_and_rate_limits(self) -> None:
        speaker = self.client.post(
            "/api/v1/admin/events/speakers",
            data=json.dumps({"short": "alexeygrigorev", "name": "Alexey Grigorev"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(speaker.status_code, 201, speaker.content)
        start = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        created = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps(
                {
                    "picture": TEST_PICTURE_PAYLOAD,
                    "title": "Chunked Banner Event",
                    "description": "About it.",
                    "type": "podcast",
                    "speakers": ["alexeygrigorev"],
                    "start_datetime": start.isoformat(),
                    "publish": True,
                }
            ),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(created.status_code, 201, created.content)
        event_uuid = created.json()["event"]["id"]
        public_id = created.json()["event"]["public_id"]

        # 140 KB payload in 35 x 4096B chunks (> WRITE_RATE_LIMIT=30 and > 64KB JSON cap).
        payload_bytes = TINY_PNG + (b"\x01\x02\x03\x04" * 35_000)
        chunk_size = 4096
        chunks = [
            payload_bytes[i : i + chunk_size] for i in range(0, len(payload_bytes), chunk_size)
        ]
        self.assertGreater(len(chunks), 30)

        import uuid

        upload_id = str(uuid.uuid4())
        # Continuation chunk before chunk 0 must fail loudly.
        uninit = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/banner",
            data=json.dumps(
                {
                    "upload_id": upload_id,
                    "chunk_index": 1,
                    "chunk_count": len(chunks),
                    "content_type": "image/png",
                    "chunk_base64": base64.b64encode(chunks[1]).decode("ascii"),
                }
            ),
            content_type="application/json",
            headers={**self.auth_headers, "X-Banner-Upload-Chunk": "1"},
        )
        self.assertEqual(uninit.status_code, 400, uninit.content)
        self.assertEqual(uninit.json()["error"]["code"], "banner_upload_not_initialized")

        last_resp = None
        for idx, chunk in enumerate(chunks):
            headers = dict(self.auth_headers)
            if idx > 0:
                headers["X-Banner-Upload-Chunk"] = "1"
            last_resp = self.client.post(
                f"/api/v1/admin/events/{event_uuid}/banner",
                data=json.dumps(
                    {
                        "upload_id": upload_id,
                        "chunk_index": idx,
                        "chunk_count": len(chunks),
                        "content_type": "image/png",
                        "chunk_base64": base64.b64encode(chunk).decode("ascii"),
                    }
                ),
                content_type="application/json",
                headers=headers,
            )
            self.assertEqual(last_resp.status_code, 200, last_resp.content)
            if idx < len(chunks) - 1:
                self.assertFalse(last_resp.json()["complete"])
            else:
                self.assertTrue(last_resp.json()["complete"])
                self.assertEqual(last_resp.json()["size_bytes"], len(payload_bytes))

        banner_resp = self.client.get(f"/events/{public_id}/banner")
        self.assertEqual(banner_resp.status_code, 200)
        self.assertEqual(banner_resp["Content-Type"], "image/png")
        self.assertEqual(banner_resp.content, payload_bytes)

        # Single-shot upload > 64 KB also succeeds when not blocked by edge WAF.
        single_bytes = TINY_PNG + (b"\x09\x08\x07\x06" * 20_000)
        single = self.client.post(
            f"/api/v1/admin/events/{event_uuid}/banner",
            data=json.dumps(
                {
                    "content_type": "image/png",
                    "image_base64": base64.b64encode(single_bytes).decode("ascii"),
                }
            ),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(single.status_code, 200, single.content)
        self.assertEqual(single.json()["size_bytes"], len(single_bytes))

    def test_create_requires_a_picture_and_accepts_one_staged_in_chunks(self) -> None:
        import uuid

        self.client.post(
            "/api/v1/admin/events/speakers",
            data=json.dumps({"short": "alexeygrigorev", "name": "Alexey Grigorev"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        start = (timezone.now() + timedelta(days=12)).replace(microsecond=0)
        body = {
            "title": "Picture Rule Event",
            "type": "webinar",
            "description": "About it.",
            "speakers": ["alexeygrigorev"],
            "start_datetime": start.isoformat(),
            "publish": True,
        }
        refused = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps(body),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()["error"]["code"], "event_picture_required")

        # 140 KB in 35 chunks: more than the write rate limit and the 64 KB JSON cap.
        payload_bytes = TINY_PNG + (b"\x01\x02\x03\x04" * 35_000)
        chunks = [payload_bytes[i : i + 4096] for i in range(0, len(payload_bytes), 4096)]
        upload_id = str(uuid.uuid4())
        for idx, chunk in enumerate(chunks):
            headers = dict(self.auth_headers)
            if idx > 0:
                headers["X-Banner-Upload-Chunk"] = "1"
            staged = self.client.post(
                "/api/v1/admin/events",
                data=json.dumps(
                    {
                        "picture_chunk": {
                            "upload_id": upload_id,
                            "chunk_index": idx,
                            "chunk_count": len(chunks),
                            "content_type": "image/png",
                            "chunk_base64": base64.b64encode(chunk).decode("ascii"),
                        }
                    }
                ),
                content_type="application/json",
                headers=headers,
            )
            self.assertEqual(staged.status_code, 200, staged.content)
        self.assertTrue(staged.json()["picture_upload"]["complete"])

        created = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps({**body, "picture": {"upload_id": upload_id}}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(created.status_code, 201, created.content)
        public_id = created.json()["event"]["public_id"]
        banner = self.client.get(f"/events/{public_id}/banner")
        self.assertEqual(banner.content, payload_bytes)

        # The staged chunks were consumed: the upload id cannot create a second event.
        again = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps({**body, "title": "Second", "picture": {"upload_id": upload_id}}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(again.status_code, 400, again.content)
        self.assertEqual(again.json()["error"]["code"], "event_picture_required")

    def test_unhandled_errors_report_specific_cause_instead_of_generic_internal_error(self) -> None:
        from django.db import IntegrityError, NotSupportedError

        speaker = self.client.post(
            "/api/v1/admin/events/speakers",
            data=json.dumps({"short": "alexeygrigorev", "name": "Alexey Grigorev"}),
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(speaker.status_code, 201, speaker.content)
        start = (timezone.now() + timedelta(days=15)).replace(microsecond=0)
        created = self.client.post(
            "/api/v1/admin/events",
            data=json.dumps(
                {
                    "picture": TEST_PICTURE_PAYLOAD,
                    "title": "Error Reporting Event",
                    "description": "About it.",
                    "type": "podcast",
                    "speakers": ["alexeygrigorev"],
                    "start_datetime": start.isoformat(),
                    "publish": False,
                }
            ),
            content_type="application/json",
            headers=self.auth_headers,
        )
        event_uuid = created.json()["event"]["id"]

        with patch(
            "events.admin_services.publish_event",
            side_effect=NotSupportedError(
                "FOR UPDATE cannot be applied to the nullable side of an outer join"
            ),
        ):
            resp = self.client.post(
                f"/api/v1/admin/events/{event_uuid}/publish",
                data="{}",
                content_type="application/json",
                headers=self.auth_headers,
            )
        self.assertEqual(resp.status_code, 500)
        err = resp.json()["error"]
        self.assertEqual(err["code"], "database_not_supported")
        self.assertIn("NotSupportedError", err["message"])
        self.assertIn("events.manage.publish", err["message"])

        with patch(
            "events.admin_services.publish_event",
            side_effect=IntegrityError("duplicate key"),
        ):
            resp2 = self.client.post(
                f"/api/v1/admin/events/{event_uuid}/publish",
                data="{}",
                content_type="application/json",
                headers=self.auth_headers,
            )
        self.assertEqual(resp2.status_code, 409)
        self.assertEqual(resp2.json()["error"]["code"], "database_integrity_error")

        with patch(
            "events.admin_services.publish_event",
            side_effect=RuntimeError("allocator boom"),
        ):
            resp3 = self.client.post(
                f"/api/v1/admin/events/{event_uuid}/publish",
                data="{}",
                content_type="application/json",
                headers=self.auth_headers,
            )
        self.assertEqual(resp3.status_code, 500)
        self.assertIn("RuntimeError", resp3.json()["error"]["message"])
        self.assertIn("allocator boom", resp3.json()["error"]["message"])
