"""The public Q&A configuration is an explicit allowlist (audit EVT-09).

The public page once embedded the management session serializer, so an
anonymous visitor's HTML carried the internal Event and Q&A session UUIDs,
the revision, retention and expiry internals, and live counters -- and any
future management field would have leaked the same way.  The public config
is now its own allowlisted DTO; these tests pin the exact schema.
"""

from __future__ import annotations

import json
from typing import Any

from django.test import TestCase
from django.urls import reverse

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from events.identity import create_event_identity
from events.models import EventQnaSession
from events.qna import services

#: The exact participant-facing schema: contract, canonical paths, state,
#: public settings, text limit, capability flags, banner.  Nothing else.
PUBLIC_SCHEMA = {
    "contract",
    "api_base",
    "share_url",
    "qr_url",
    "state",
    "settings",
    "max_length",
    "can_ask",
    "can_vote",
    "banner",
}

#: Management-only fields that must never appear in a public configuration.
MANAGEMENT_FIELDS = (
    "session_id",
    "event_id",
    "revision",
    "retention_days",
    "expires_at",
    "counts",
    "host_links",
    "present_url",
)


def _embedded_config(response) -> dict[str, Any]:
    """Parse the JSON configuration the rendered page embeds."""

    content = response.content.decode()
    marker = 'id="qna-config"'
    start = content.index(marker)
    payload_start = content.index(">", start) + 1
    payload_end = content.index("</script>", payload_start)
    return json.loads(content[payload_start:payload_end])


class PublicConfigContractTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Public config test event",
            source_repository="DataTalksClub/events",
            source_revision="3" * 40,
            source_key="public-config-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        self.path = f"{services.event_qna_path(self.event)}/"

    def test_an_anonymous_config_has_exactly_the_safe_schema(self) -> None:
        response = self.client.get(self.path)

        self.assertEqual(response.status_code, 200)
        config = _embedded_config(response)
        self.assertEqual(set(config), PUBLIC_SCHEMA)
        self.assertEqual(config["contract"], "qna.v1")
        self.assertEqual(config["max_length"], services.MAX_QUESTION_LENGTH)
        self.assertTrue(config["can_ask"])
        self.assertTrue(config["can_vote"])
        self.assertEqual(config["banner"], "")
        self.assertEqual(config["settings"]["default_sort"], "popular")

    def test_no_management_identifier_reaches_the_public_page(self) -> None:
        session = EventQnaSession.objects.get(event=self.event)

        response = self.client.get(self.path)

        config = _embedded_config(response)
        for field in MANAGEMENT_FIELDS:
            self.assertNotIn(field, config)
        body = response.content
        # Neither internal UUID may appear anywhere in the rendered page.
        self.assertNotIn(str(session.id).encode(), body)
        self.assertNotIn(str(self.event.id).encode(), body)

    def test_a_closed_session_carries_the_banner_and_refusal_flags(self) -> None:
        services.update_session(self.event.id, {"state": "closed"})

        config = _embedded_config(self.client.get(self.path))

        self.assertFalse(config["can_ask"])
        self.assertFalse(config["can_vote"])
        self.assertEqual(config["banner"], "Questions are closed for this session.")

    def test_a_staff_viewer_gets_only_the_host_links_addition(self) -> None:
        client = authenticated_studio_client(
            make_studio_user(username="qna-host-viewer", roles=("event_operator",))
        )

        config = _embedded_config(client.get(self.path))

        self.assertEqual(set(config), PUBLIC_SCHEMA | {"host_links"})
        self.assertIn("/studio/events/", config["host_links"]["studio"])

    def test_the_json_api_error_envelope_stays_free_of_management_ids(self) -> None:
        # The questions endpoint speaks per-question data, never the session
        # DTO; even its error envelopes must not introduce one.
        response = self.client.get(
            f"{services.event_qna_path(self.event)}/api/questions/?bogus=1"
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b"session_id", response.content)
        self.assertNotIn(b"event_id", response.content)

    def test_the_admin_api_still_returns_the_management_metadata(self) -> None:
        from django.contrib.auth.models import Permission

        from management_auth.models import APIPrincipal
        from management_auth.services import create_principal, issue_credential_once

        permission = Permission.objects.get(
            content_type__app_label="events", codename="manage_event_qna"
        )
        principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="Q&A public config admin check",
            identity_snapshot="service:public-config-admin",
            permissions=(permission,),
        )
        issued = issue_credential_once(
            actor_principal=principal,
            target_principal_id=principal.id,
            name="Q&A public config admin credential",
            scopes=("events.qna.read",),
            idempotency_key="public-config-admin-credential",
            actor_permission="events.manage_event_qna",
        )
        session = EventQnaSession.objects.get(event=self.event)

        response = self.client.get(
            reverse("api:admin-event-qna-read", kwargs={"event_id": self.event.id}),
            HTTP_AUTHORIZATION=f"Bearer {issued.response['token']}",
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        # The management DTO keeps its identities and internals unchanged.
        self.assertEqual(payload["session_id"], str(session.id))
        self.assertEqual(payload["event_id"], str(self.event.id))
        self.assertIn("revision", payload)
        self.assertIn("retention_days", payload)
