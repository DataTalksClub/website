"""Expected concurrency failures stay inside the public error contract.

The public adapters caught only ``QnaError``, so a ``RevisionConflict``
raised by the session's optimistic compare-and-swap -- plausible during
live polling, moderation, and audience voting on one shared session
revision -- escaped as an unhandled exception instead of a safe JSON 409.
These tests also pin that a repeated identical vote is a true no-op: the
session revision is what every adapter preconditions on, so burning it
on nothing would invalidate other actors' forms and ETags for no state
change (audit EVT-08).
"""

from __future__ import annotations

import json

from django.test import TestCase

from events.identity import create_event_identity
from events.models import EventQnaSession
from events.qna import security, services
from events.qna.services import RevisionConflict


class ConflictContractTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Conflict contract test event",
            source_repository="DataTalksClub/events",
            source_revision="1" * 40,
            source_key="conflict-contract-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        self.participant, _author_token = security.new_participant()
        self.question = services.submit_question(
            self.event.id, text="Conflict contract question", participant=self.participant
        )
        # Submitting implicitly votes with the author's digest, so the vote
        # cases below speak as a second audience member: their first add is a
        # real state change, not a no-op on the author's existing vote.
        self.voter, self.voter_token = security.new_participant()
        self.vote_path = (
            f"{services.event_qna_path(self.event)}/api/questions/{self.question.question_id}/vote/"
        )
        self.questions_path = f"{services.event_qna_path(self.event)}/api/questions/"

    def _session(self) -> EventQnaSession:
        return EventQnaSession.objects.get(event=self.event)

    def _fail_bump(self, original):
        def broken(session, **kwargs):
            raise RevisionConflict(expected=1, actual=2)

        return broken

    def test_an_injected_conflict_on_vote_is_a_safe_json_409(self) -> None:
        from unittest.mock import patch

        self.client.cookies[security.PARTICIPANT_COOKIE] = self.voter_token
        with patch.object(services, "_bump_session", self._fail_bump(None)):
            response = self.client.post(self.vote_path, REMOTE_ADDR="203.0.113.10")

        self.assertEqual(response.status_code, 409, response.content)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "revision_conflict")
        # The envelope is safe: no revision numbers, question text, or traceback.
        self.assertNotIn("expected", json.dumps(payload))
        self.assertNotIn(b"Conflict contract question", response.content)
        self.assertIn("Retry-After", response.headers)
        # The atomic transaction rolled back: the voter's vote added nothing,
        # and the author's implicit vote is all the score still holds.
        self.question.refresh_from_db()
        self.assertEqual(self.question.score, 1)

    def test_an_injected_conflict_on_submit_is_a_safe_json_409(self) -> None:
        from unittest.mock import patch

        self.client.cookies[security.PARTICIPANT_COOKIE] = self.voter_token
        with patch.object(services, "_bump_session", self._fail_bump(None)):
            response = self.client.post(
                self.questions_path,
                data=json.dumps({"text": "A fresh question"}),
                content_type="application/json",
                REMOTE_ADDR="203.0.113.10",
            )

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "revision_conflict")

    def test_a_repeated_identical_vote_does_not_consume_the_session_revision(self) -> None:
        self.client.cookies[security.PARTICIPANT_COOKIE] = self.voter_token
        first = self.client.post(self.vote_path, REMOTE_ADDR="203.0.113.10")
        self.assertEqual(first.status_code, 200, first.content)
        revision_after_first = self._session().revision

        repeat = self.client.post(self.vote_path, REMOTE_ADDR="203.0.113.10")
        remove = self.client.delete(self.vote_path, REMOTE_ADDR="203.0.113.10")
        repeat_removal = self.client.delete(self.vote_path, REMOTE_ADDR="203.0.113.10")

        self.assertEqual(repeat.status_code, 200, repeat.content)
        self.assertEqual(remove.status_code, 200, remove.content)
        self.assertEqual(repeat_removal.status_code, 200, repeat_removal.content)
        # The captured revision already includes the add's bump; only the
        # removal is a state change after it.  The no-op repeats moved the
        # revision not at all.
        self.assertEqual(self._session().revision, revision_after_first + 1)
        self.question.refresh_from_db()
        # Back to the author's implicit vote only.
        self.assertEqual(self.question.score, 1)

    def test_a_real_state_change_still_moves_the_revision(self) -> None:
        self.client.cookies[security.PARTICIPANT_COOKIE] = self.voter_token
        before = self._session().revision

        self.client.post(self.vote_path, REMOTE_ADDR="203.0.113.10")

        self.assertEqual(self._session().revision, before + 1)

    def test_duplicate_add_remove_never_corrupts_the_score(self) -> None:
        self.client.cookies[security.PARTICIPANT_COOKIE] = self.voter_token
        for _ in range(3):
            self.client.post(self.vote_path, REMOTE_ADDR="203.0.113.10")
            self.client.delete(self.vote_path, REMOTE_ADDR="203.0.113.10")

        # Every cycle nets to zero; the score is the author's implicit vote.
        self.question.refresh_from_db()
        self.assertEqual(self.question.score, 1)

    def test_the_management_adapter_maps_the_same_conflict_to_409(self) -> None:
        # The management API already translated RevisionConflict; the public
        # contract now matches it, so both surfaces agree on the shape.
        from django.contrib.auth.models import Permission
        from django.urls import reverse

        from management_auth.models import APIPrincipal
        from management_auth.services import create_principal, issue_credential_once

        permission = Permission.objects.get(
            content_type__app_label="events", codename="manage_event_qna"
        )
        principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="Q&A conflict contract test",
            identity_snapshot="service:conflict-contract",
            permissions=(permission,),
        )
        issued = issue_credential_once(
            actor_principal=principal,
            target_principal_id=principal.id,
            name="Q&A conflict contract credential",
            scopes=("events.qna.read", "events.qna.moderate"),
            idempotency_key="conflict-contract-credential",
            actor_permission="events.manage_event_qna",
        )
        from unittest.mock import patch

        # Moderation preconditions on the session revision (EVT-02); the
        # injected conflict fires after that precondition passes.
        revision = EventQnaSession.objects.get(event=self.event).revision
        with patch.object(services, "_bump_session", self._fail_bump(None)):
            response = self.client.patch(
                reverse(
                    "api:admin-event-qna-moderate",
                    kwargs={
                        "event_id": self.event.id,
                        "question_id": self.question.question_id,
                    },
                ),
                data=json.dumps({"status": "answered"}),
                content_type="application/json",
                HTTP_IF_MATCH=f'"rev-{revision}"',
                HTTP_AUTHORIZATION=f"Bearer {issued.response['token']}",
                HTTP_IDEMPOTENCY_KEY="conflict-contract-key-1",
            )

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "revision_conflict")
