"""One principal's retry key must not fence another's commands (audit EVT-03).

The management API's idempotency records were scoped by capability key only,
so two independently authorized principals sharing a client-local key
("seq-1", a generated UUID) collided: the second principal received the
first one's replay, or a conflict against the first one's stored request,
instead of executing its own command.  The namespace is now fenced per
authenticated principal; these tests pin the A/B isolation matrix.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.identity import create_event_identity
from events.models import EventQnaSession
from events.qna import security, services
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal, issue_credential_once


class PrincipalNamespaceIsolationTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Idempotency namespace test event",
            source_repository="DataTalksClub/events",
            source_revision="d" * 40,
            source_key="idempotency-namespace-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        participant, _token = security.new_participant()
        self.question = services.submit_question(
            self.event.id, text="Namespace test question", participant=participant
        )
        self.tokens: dict[str, str] = {}
        for name in ("principal-a", "principal-b"):
            permission = Permission.objects.get(
                content_type__app_label="events",
                codename="manage_event_qna",
            )
            principal = create_principal(
                kind=APIPrincipal.Kind.SERVICE,
                name=f"Q&A isolation {name}",
                identity_snapshot=f"service:{name}",
                permissions=(permission,),
            )
            issued = issue_credential_once(
                actor_principal=principal,
                target_principal_id=principal.id,
                name=f"Q&A isolation credential {name}",
                scopes=("events.qna.read", "events.qna.moderate"),
                idempotency_key=f"isolation-credential-{name}",
                actor_permission="events.manage_event_qna",
            )
            self.tokens[name] = str(issued.response["token"])

    def _headers(self, principal: str, key: str) -> dict[str, Any]:
        return {
            "HTTP_AUTHORIZATION": f"Bearer {self.tokens[principal]}",
            "HTTP_IDEMPOTENCY_KEY": key,
        }

    def _moderate(self, principal: str, key: str, payload: dict[str, Any]):
        return self.client.patch(
            reverse(
                "api:admin-event-qna-moderate",
                kwargs={"event_id": self.event.id, "question_id": self.question.question_id},
            ),
            data=json.dumps(payload),
            content_type="application/json",
            **self._headers(principal, key),
        )

    def test_two_principals_use_the_same_key_independently(self) -> None:
        # A pins under its client-local key; B answers under the very same
        # key.  A shared namespace would hand B A's replay or a conflict
        # against A's stored request instead of executing B's command.
        pinned = self._moderate("principal-a", "client-retry-seq-1", {"pinned": True})
        self.assertEqual(pinned.status_code, 200, pinned.content)
        self.assertFalse(pinned.json()["replayed"])
        self.assertTrue(pinned.json()["pinned"])

        answered = self._moderate("principal-b", "client-retry-seq-1", {"status": "answered"})
        self.assertEqual(answered.status_code, 200, answered.content)
        self.assertFalse(answered.json()["replayed"])
        self.assertEqual(answered.json()["status"], "answered")

        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "answered")

    def test_each_principal_replays_only_its_own_command(self) -> None:
        first = self._moderate("principal-a", "client-retry-seq-1", {"pinned": True})
        self.assertEqual(first.status_code, 200, first.content)
        other = self._moderate("principal-b", "client-retry-seq-1", {"status": "answered"})
        self.assertEqual(other.status_code, 200, other.content)

        def _payload(response) -> dict:
            # The replayed flag is merged by the view, not stored; compare the
            # stored command result itself.
            return {key: value for key, value in response.json().items() if key != "replayed"}

        a_replay = self._moderate("principal-a", "client-retry-seq-1", {"pinned": True})
        self.assertEqual(a_replay.status_code, 200, a_replay.content)
        self.assertTrue(a_replay.json()["replayed"])
        self.assertEqual(_payload(a_replay), _payload(first))

        b_replay = self._moderate("principal-b", "client-retry-seq-1", {"status": "answered"})
        self.assertEqual(b_replay.status_code, 200, b_replay.content)
        self.assertTrue(b_replay.json()["replayed"])
        self.assertEqual(_payload(b_replay), _payload(other))

    def test_a_changed_payload_conflicts_inside_one_namespace_only(self) -> None:
        first = self._moderate("principal-a", "client-retry-seq-1", {"pinned": True})
        self.assertEqual(first.status_code, 200, first.content)

        # A's own key with a different command is a genuine conflict.
        conflict = self._moderate("principal-a", "client-retry-seq-1", {"pinned": False})
        self.assertEqual(conflict.status_code, 409, conflict.content)
        self.assertEqual(conflict.json()["error"]["code"], "idempotency_conflict")

        # B's command under the same key is unaffected by A's stored request.
        independent = self._moderate("principal-b", "client-retry-seq-1", {"pinned": False})
        self.assertEqual(independent.status_code, 200, independent.content)
        self.assertFalse(independent.json()["replayed"])

    def test_a_revoked_credential_cannot_reach_its_stored_replay(self) -> None:
        first = self._moderate("principal-a", "client-retry-seq-1", {"pinned": True})
        self.assertEqual(first.status_code, 200, first.content)
        APICredential.objects.update(revoked_at=timezone.now())

        revoked = self._moderate("principal-a", "client-retry-seq-1", {"pinned": True})
        self.assertEqual(revoked.status_code, 401, revoked.content)
