from __future__ import annotations

import json
from typing import Any
from xml.etree import ElementTree

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from events.identity import create_event_identity
from events.models import EventQnaSession
from events.qna import security, services
from management_api.concurrency import revision_etag
from management_auth.models import APIPrincipal
from management_auth.services import create_principal, issue_credential_once


class EventQnaServiceTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Native Q&A test event",
            source_repository="DataTalksClub/events",
            source_revision="a" * 40,
            source_key="native-qna-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        self.participant, _token = security.new_participant()

    def test_question_validation_implicit_vote_and_idempotent_votes(self) -> None:
        question = services.submit_question(
            self.event.id,
            text="  How does the native adapter work?  ",
            author_name="Ada",
            participant=self.participant,
        )
        self.assertEqual(question.text, "How does the native adapter work?")
        self.assertEqual(question.score, 1)
        self.assertEqual(EventQnaSession.objects.get(event=self.event).q_total, 1)

        score, voted = services.vote_question(
            self.event.id, question.question_id, participant=self.participant, add=True
        )
        self.assertEqual((score, voted), (1, True))
        other, _token = security.new_participant()
        score, voted = services.vote_question(
            self.event.id, question.question_id, participant=other, add=True
        )
        self.assertEqual((score, voted), (2, True))
        score, voted = services.vote_question(
            self.event.id, question.question_id, participant=other, add=False
        )
        self.assertEqual((score, voted), (1, False))

    def test_moderation_preserves_status_counters_and_singular_pin(self) -> None:
        first = services.submit_question(self.event.id, text="First", participant=self.participant)
        second = services.submit_question(
            self.event.id, text="Second", participant=self.participant
        )
        services.update_question(self.event.id, first.question_id, {"pinned": True}, moderator=True)
        services.update_question(
            self.event.id, second.question_id, {"pinned": True}, moderator=True
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.pinned)
        self.assertTrue(second.pinned)
        services.update_question(
            self.event.id, second.question_id, {"status": "answered"}, moderator=True
        )
        session = EventQnaSession.objects.get(event=self.event)
        self.assertEqual((session.q_total, session.q_answered), (2, 1))
        services.update_question(
            self.event.id, second.question_id, {"status": "deleted"}, moderator=True
        )
        session.refresh_from_db()
        self.assertEqual((session.q_total, session.q_answered), (1, 0))

    def test_cohost_link_and_passcode_are_separate_and_revocable(self) -> None:
        invite = services.create_cohost(
            self.event.id,
            name="moderator",
            passcode="open-sesame-42",
            actor_ref="user:1",
        )
        self.assertNotIn(invite["passcode"], invite["join_url"])
        found, error = services.redeem_cohost(self.event.id, "MODERATOR", "  open-sesame42 ")
        self.assertIsNone(error)
        assert found is not None
        self.assertEqual(found.name, "moderator")
        services.revoke_cohost(self.event.id, found.invite_id)
        found, error = services.redeem_cohost(self.event.id, "moderator", "open-sesame-42")
        self.assertIsNone(found)
        self.assertTrue(error)


class EventQnaHttpTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="HTTP Q&A test event",
            source_repository="DataTalksClub/events",
            source_revision="b" * 40,
            source_key="http-qna-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        self.slug = self.event.slug
        self.page_url = reverse(
            "public-event-qna",
            kwargs={"event_id": self.event.public_id, "slug": self.slug},
        )
        self.api_url = reverse(
            "public-event-qna-questions",
            kwargs={"event_id": self.event.public_id, "slug": self.slug},
        )

    def test_public_contract_uses_private_etag_polling_and_csrf(self) -> None:
        client = Client(enforce_csrf_checks=True)
        page = client.get(self.page_url)
        self.assertEqual(page.status_code, 200)
        self.assertIn("qna.v1", page.content.decode())
        csrf = page.cookies["csrftoken"].value
        payload = json.dumps({"text": "Can I ask anonymously?"})
        denied = client.post(self.api_url, data=payload, content_type="application/json")
        self.assertEqual(denied.status_code, 403)
        created = client.post(
            self.api_url,
            data=payload,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertIn("private", created["Cache-Control"])
        self.assertIn("no-store", created["Cache-Control"])
        first = client.get(self.api_url)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first["ETag"].startswith('W/"'))
        unchanged = client.get(self.api_url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(unchanged.status_code, 304)
        self.assertEqual(unchanged.content, b"")
        self.assertIn("private", unchanged["Cache-Control"])
        self.assertIn("no-store", unchanged["Cache-Control"])

    def test_archived_public_session_is_gone_but_idempotent_provisioning_remains(self) -> None:
        services.transition_session(self.event.id, EventQnaSession.State.ARCHIVED)
        response = Client().get(self.page_url)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            services.ensure_event_qna(self.event.id).session.id,
            services.ensure_event_qna(self.event.id).session.id,
        )

    def test_qr_routes_return_bounded_share_assets(self) -> None:
        svg_url = reverse(
            "public-event-qna-qr",
            kwargs={"event_id": self.event.public_id, "slug": self.slug, "kind": "svg"},
        )
        svg = Client().get(svg_url)
        self.assertEqual(svg.status_code, 200)
        document = ElementTree.fromstring(svg.content)
        self.assertEqual(document.attrib["aria-label"], "Event Q&A share code")
        self.assertIn(b'aria-label="Event Q&amp;A share code"', svg.content)
        self.assertIn(b"currentColor", svg.content)
        self.assertNotIn(b"width=", svg.content)

        png_url = reverse(
            "public-event-qna-qr",
            kwargs={"event_id": self.event.public_id, "slug": self.slug, "kind": "png"},
        )
        png = Client().get(png_url, query_params={"size": 256})
        self.assertEqual(png.status_code, 200)
        self.assertTrue(png.content.startswith(b"\x89PNG\r\n\x1a\n"))


class EventQnaManagementTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Management Q&A test event",
            source_repository="DataTalksClub/events",
            source_revision="c" * 40,
            source_key="management-qna-test",
        )
        permission = Permission.objects.get(
            content_type__app_label="events",
            codename="manage_event_qna",
        )
        self.principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="Q&A management test",
            identity_snapshot="service:qna-test",
            permissions=(permission,),
        )
        issued = issue_credential_once(
            actor_principal=self.principal,
            target_principal_id=self.principal.id,
            name="Q&A management credential",
            scopes=(
                "events.qna.read",
                "events.qna.manage",
                "events.qna.moderate",
                "events.qna.cohost.create",
            ),
            idempotency_key="qna-test-credential",
            actor_permission="events.manage_event_qna",
        )
        self.token = str(issued.response["token"])

    def _headers(self, key: str) -> dict[str, Any]:
        return {
            "HTTP_AUTHORIZATION": f"Bearer {self.token}",
            "HTTP_IDEMPOTENCY_KEY": key,
        }

    def test_admin_api_reads_and_idempotently_updates_session(self) -> None:
        url = reverse("api:admin-event-qna-read", kwargs={"event_id": self.event.id})
        read = self.client.get(url, **self._headers("qna-read"))
        self.assertEqual(read.status_code, 200, read.content)
        self.assertEqual(read.json()["contract"], "qna.v1")
        revision = read.json()["revision"]
        update = self.client.patch(
            url,
            data=json.dumps({"state": "open", "settings": {"default_sort": "recent"}}),
            content_type="application/json; charset=utf-8",
            HTTP_IF_MATCH=revision_etag(revision),
            **self._headers("qna-update"),
        )
        self.assertEqual(update.status_code, 200, update.content)
        self.assertEqual(update.json()["state"], "open")
        replay = self.client.patch(
            url,
            data=json.dumps({"state": "open", "settings": {"default_sort": "recent"}}),
            content_type="application/json; charset=utf-8",
            HTTP_IF_MATCH=revision_etag(revision),
            **self._headers("qna-update"),
        )
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.json()["replayed"])

    def test_studio_surface_is_authorized_and_private(self) -> None:
        user = make_studio_user(username="qna-studio", roles=("event_operator",))
        client = authenticated_studio_client(user)
        response = client.get(
            reverse("studio:event-qna-detail", kwargs={"event_id": self.event.id})
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, "Configure the event session")
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_admin_cohost_creation_is_one_time_and_does_not_replay_the_passcode(self) -> None:
        url = reverse("api:admin-event-qna-cohost-create", kwargs={"event_id": self.event.id})
        first = self.client.post(
            url,
            data=json.dumps({"name": "operator", "passcode": "open-sesame-42"}),
            content_type="application/json",
            **self._headers("qna-cohost-create"),
        )
        self.assertEqual(first.status_code, 201, first.content)
        self.assertIn("passcode", first.json())
        replay = self.client.post(
            url,
            data=json.dumps({"name": "operator", "passcode": "open-sesame-42"}),
            content_type="application/json",
            **self._headers("qna-cohost-create"),
        )
        self.assertEqual(replay.status_code, 409, replay.content)
        self.assertNotIn("passcode", replay.json())
