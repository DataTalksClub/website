"""UX-07: the questions poll carries the session lifecycle, and the ETag
vouches for it.

A closed session must reach an already-open participant room even when no
question changed: the list ETag covers the session state/revision, so the
poll answers 200 with the new state instead of a 304 that would leave a
closed room inviting questions and votes.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from event_qna import security, services
from event_qna.models import EventQnaSession
from events.models import create_event_identity


class EventQnaLifecycleEtagTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Lifecycle ETag test event",
            source_repository="DataTalksClub/events",
            source_revision="c" * 40,
            source_key="lifecycle-etag-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        participant, _token = security.new_participant()
        services.submit_question(self.event.id, text="First question", participant=participant)
        self.open_etag = services.list_questions(self.event.id)[2]

    def test_closing_without_question_changes_invalidates_the_etag(self) -> None:
        services.transition_session(self.event.id, EventQnaSession.State.CLOSED)
        items, _counts, closed_etag, session = services.list_questions(self.event.id)
        self.assertEqual(session.state, EventQnaSession.State.CLOSED)
        self.assertNotEqual(closed_etag, self.open_etag)
        # The questions themselves are untouched and stay readable.
        self.assertEqual(len(items), 1)

    def test_reopening_produces_a_third_validator(self) -> None:
        services.transition_session(self.event.id, EventQnaSession.State.CLOSED)
        _items, _counts, closed_etag, _session = services.list_questions(self.event.id)
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        _items, _counts, reopened_etag, _session = services.list_questions(self.event.id)
        self.assertNotEqual(reopened_etag, self.open_etag)
        self.assertNotEqual(reopened_etag, closed_etag)

    def test_expiry_driven_close_invalidates_the_etag(self) -> None:
        session = EventQnaSession.objects.get(event=self.event)
        session.expires_at = timezone.now() - timedelta(seconds=1)
        session.save()
        _items, _counts, expired_etag, refreshed = services.list_questions(self.event.id)
        self.assertEqual(refreshed.state, EventQnaSession.State.CLOSED)
        self.assertNotEqual(expired_etag, self.open_etag)

    def test_sort_selection_is_part_of_the_validated_representation(self) -> None:
        # One client reuses its stored validator across sort selections, so
        # two orderings that happen to coincide must still carry different
        # ETags or a sort switch could be answered with a false 304.
        _items, _counts, popular_etag, _session = services.list_questions(
            self.event.id, sort="popular"
        )
        _items, _counts, recent_etag, _session = services.list_questions(
            self.event.id, sort="recent"
        )
        self.assertNotEqual(popular_etag, recent_etag)

    def test_unchanged_room_still_answers_304(self) -> None:
        _items, _counts, etag, _session = services.list_questions(self.event.id)
        _items, _counts, again_etag, _session = services.list_questions(self.event.id)
        self.assertEqual(etag, again_etag)


class EventQnaLifecyclePollContractTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Lifecycle poll test event",
            source_repository="DataTalksClub/events",
            source_revision="d" * 40,
            source_key="lifecycle-poll-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        self.page_url = reverse(
            "public-event-qna",
            kwargs={"event_id": self.event.public_id, "slug": self.event.slug},
        )
        self.api_url = reverse(
            "public-event-qna-questions",
            kwargs={"event_id": self.event.public_id, "slug": self.event.slug},
        )

    def _poll(self) -> dict:
        client = Client()
        response = client.get(self.api_url)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_open_poll_reports_capabilities(self) -> None:
        payload = self._poll()
        self.assertEqual(payload["state"], "open")
        self.assertEqual(payload["capabilities"], {"can_ask": True, "can_vote": True})

    def test_closed_poll_answers_200_with_the_new_state(self) -> None:
        stale = self._poll()
        services.transition_session(self.event.id, EventQnaSession.State.CLOSED)
        client = Client()
        # The exact audit reproduction: the old validator, no question
        # change — the room must learn about the close, not cache it away.
        response = client.get(self.api_url, HTTP_IF_NONE_MATCH=stale["etag"])
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["state"], "closed")
        self.assertEqual(payload["capabilities"], {"can_ask": False, "can_vote": False})

    def test_participant_page_renders_lifecycle_from_the_session(self) -> None:
        participant, _token = security.new_participant()
        services.submit_question(
            self.event.id, text="Readable while closed", participant=participant
        )
        services.transition_session(self.event.id, EventQnaSession.State.CLOSED)
        page = self.client.get(self.page_url)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        # The ask form is withdrawn, the explanation is shown, and the
        # banner names the state.
        self.assertIn('id="qna-question-form" method="post" hidden', body)
        self.assertIn("Questions are closed for this session.", body)
        self.assertIn("Closed", body)
        # The list itself renders client-side; the browser suite in
        # playwright_tests/test_qna_lifecycle_state.py owns readability.

        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        reopened = self.client.get(self.page_url)
        body = reopened.content.decode()
        self.assertNotIn('id="qna-question-form" method="post" hidden', body)
        self.assertIn('id="qna-ask-closed" class="qna-muted" role="status" hidden', body)

    def test_participant_page_selects_the_configured_default_sort(self) -> None:
        session = EventQnaSession.objects.get(event=self.event)
        session.default_sort = EventQnaSession.DefaultSort.RECENT
        session.save()
        page = self.client.get(self.page_url)
        body = page.content.decode()
        self.assertIn('<option value="recent" selected>', body)
        self.assertNotIn('<option value="popular" selected>', body)
