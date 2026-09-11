"""Invalid Q&A input changes meaning or bricks a room (audit EVT-05).

Three confirmed defects this pins shut:

* ``_clean_settings`` accepted ``allow_names=False`` together with
  ``require_names=True`` — a combination under which ``_validate_name``
  rejects every possible name, so an open room could accept no question at
  all;
* an unhashable ``status`` value (a list, dict, or int payload) hit Python
  set membership before any type check, escaping the safe ``QnaError``
  envelope as a raw ``TypeError``; and
* ``bool(payload["pinned"])`` coerced the JSON string ``"false"`` into a
  pin, and any truthy container into one.
"""

from __future__ import annotations

from django.test import TestCase

from events.models import EventQnaSession, create_event_identity
from events.qna import security, services
from events.qna.errors import QnaError


class InvalidInputTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Invalid input test event",
            source_repository="DataTalksClub/events",
            source_revision="c" * 40,
            source_key="invalid-input-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        self.participant, _token = security.new_participant()
        self.question = services.submit_question(
            self.event.id, text="A question", participant=self.participant
        )

    def _settings(self) -> EventQnaSession:
        return EventQnaSession.objects.get(event=self.event)

    def test_contradictory_name_settings_are_refused_and_change_nothing(self) -> None:
        for raw in (
            {"allow_names": False, "require_names": True},
            # Also through the merge path: the session already requires names,
            # so switching allow_names off alone produces the same dead state.
            {"allow_names": False},
        ):
            with self.subTest(raw=raw):
                if raw == {"allow_names": False}:
                    services.update_session(self.event.id, {"settings": {"require_names": True}})
                with self.assertRaises(QnaError) as caught:
                    services.update_session(self.event.id, {"settings": raw})
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(caught.exception.code, "invalid_settings")
                session = self._settings()
                if raw == {"allow_names": False}:
                    # The refused update left the previously valid state intact:
                    # names still allowed, still required.
                    self.assertTrue(session.require_names)
                    self.assertTrue(session.allow_names)
                else:
                    self.assertTrue(session.allow_names)
                    self.assertFalse(session.require_names)

    def test_valid_name_combinations_still_apply(self) -> None:
        services.update_session(
            self.event.id, {"settings": {"allow_names": False, "require_names": False}}
        )
        self.assertFalse(self._settings().allow_names)
        self.assertFalse(self._settings().require_names)

    def test_an_unhashable_status_value_is_a_safe_field_error(self) -> None:
        payloads: tuple[dict[str, object], ...] = (
            {"status": []},
            {"status": {"x": 1}},
            {"status": 7},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(QnaError) as caught:
                    services.update_question(
                        self.event.id, self.question.question_id, payload, moderator=True
                    )
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(caught.exception.code, "invalid_status")
                self.question.refresh_from_db()
                self.assertEqual(self.question.status, "visible")

    def test_an_author_malformed_status_is_a_safe_field_error_too(self) -> None:
        with self.assertRaises(QnaError) as caught:
            services.update_question(
                self.event.id,
                self.question.question_id,
                {"status": []},
                participant=self.participant,
            )
        self.assertEqual(caught.exception.status, 400)
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "visible")

    def test_pinned_accepts_only_real_booleans(self) -> None:
        pins: tuple[dict[str, object], ...] = (
            {"pinned": "false"},
            {"pinned": "true"},
            {"pinned": 1},
            {"pinned": []},
        )
        for payload in pins:
            with self.subTest(payload=payload):
                with self.assertRaises(QnaError) as caught:
                    services.update_question(
                        self.event.id, self.question.question_id, payload, moderator=True
                    )
                self.assertEqual(caught.exception.status, 400)
                self.question.refresh_from_db()
                self.assertFalse(self.question.pinned)

    def test_real_boolean_pin_still_works_both_ways(self) -> None:
        services.update_question(
            self.event.id, self.question.question_id, {"pinned": True}, moderator=True
        )
        self.question.refresh_from_db()
        self.assertTrue(self.question.pinned)
        services.update_question(
            self.event.id, self.question.question_id, {"pinned": False}, moderator=True
        )
        self.question.refresh_from_db()
        self.assertFalse(self.question.pinned)

    def test_explicit_null_status_still_means_omitted(self) -> None:
        services.update_question(
            self.event.id, self.question.question_id, {"status": None}, moderator=True
        )
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, "visible")

    def test_terminal_delete_guard_still_precedes_typed_paths(self) -> None:
        services.update_question(
            self.event.id, self.question.question_id, {"status": "deleted"}, moderator=True
        )
        with self.assertRaises(QnaError) as caught:
            services.update_question(
                self.event.id, self.question.question_id, {"status": "visible"}, moderator=True
            )
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "deleted")
