"""Deletion is terminal for Q&A questions (audit EVT-04).

The architecture contract (``_docs/architecture/event-qna-integration.md``)
says a deleted question is excluded from public **and moderator** collections
and can never be restored.  The service once defaulted a moderator listing to
every status -- deleted included -- and happily re-incremented the question
total when a deleted row was moved back to a listed status, so withdrawn
content stayed visible to moderation and could be republished.
"""

from __future__ import annotations

from django.test import TestCase

from events.models import EventQnaSession, create_event_identity
from events.qna import security, services
from events.qna.errors import QnaError


class DeletedQuestionTerminalTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Terminal deletion test event",
            source_repository="DataTalksClub/events",
            source_revision="b" * 40,
            source_key="terminal-deletion-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        self.participant, _token = security.new_participant()
        self.question = services.submit_question(
            self.event.id, text="Visible question", participant=self.participant
        )

    def _session(self) -> EventQnaSession:
        return EventQnaSession.objects.get(event=self.event)

    def _delete(self) -> None:
        services.update_question(
            self.event.id, self.question.question_id, {"status": "deleted"}, moderator=True
        )

    def test_a_deleted_question_leaves_every_collection_including_moderator_ones(self) -> None:
        self._delete()

        for moderator in (False, True):
            with self.subTest(moderator=moderator):
                items, counts, _etag, _session = services.list_questions(
                    self.event.id, moderator=moderator
                )
                self.assertEqual(items, [])
                self.assertEqual((counts["visible"], counts["answered"]), (0, 0))
        self.assertEqual(self._session().q_total, 0)

    def test_a_deleted_filter_is_not_a_listable_status(self) -> None:
        with self.assertRaises(QnaError) as caught:
            services.list_questions(self.event.id, moderator=True, statuses={"deleted"})
        self.assertEqual(caught.exception.code, "invalid_status")

    def test_a_deleted_question_can_never_be_restored_or_edited(self) -> None:
        self._delete()

        for payload in ({"status": "visible"}, {"status": "answered"}, {"text": "revived"}):
            with self.subTest(payload=payload):
                with self.assertRaises(QnaError) as caught:
                    services.update_question(
                        self.event.id, self.question.question_id, payload, moderator=True
                    )
                self.assertEqual(caught.exception.status, 409)
                self.assertEqual(caught.exception.code, "deleted")

    def test_an_author_cannot_request_a_restore_either(self) -> None:
        self._delete()

        with self.assertRaises(QnaError) as caught:
            services.update_question(
                self.event.id,
                self.question.question_id,
                {"status": "visible"},
                participant=self.participant,
            )
        # The author branch refuses any non-delete status before the terminal
        # guard: a withdrawn question cannot even ask to come back.
        self.assertEqual(caught.exception.status, 403)

    def test_repeating_the_delete_is_a_documented_safe_no_op(self) -> None:
        self._delete()
        before = (self._session().q_total, self._session().q_answered)

        services.update_question(
            self.event.id, self.question.question_id, {"status": "deleted"}, moderator=True
        )

        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "deleted")
        self.assertEqual((self._session().q_total, self._session().q_answered), before)

    def test_a_deleted_question_can_no_longer_be_pinned(self) -> None:
        self._delete()

        with self.assertRaises(QnaError) as caught:
            services.update_question(
                self.event.id, self.question.question_id, {"pinned": True}, moderator=True
            )
        self.assertEqual(caught.exception.code, "deleted")
        self.question.refresh_from_db()
        self.assertFalse(self.question.pinned)
