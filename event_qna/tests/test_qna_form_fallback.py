"""UX-08: the ask form survives the Q&A scripts not running.

Without JavaScript the form posts natively to the participant page, which
walks the same service and rate limits as the JSON API, redirects after
success, and re-renders with a safe error while preserving the authored
text in the body — never the query string.
"""

from __future__ import annotations

from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from core.models import RevisionConflict
from event_qna import services
from event_qna.models import EventQnaSession
from events.models import create_event_identity


class EventQnaFormFallbackTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            title="Form fallback test event",
            source_repository="DataTalksClub/events",
            source_revision="e" * 40,
            source_key="form-fallback-test",
        )
        services.transition_session(self.event.id, EventQnaSession.State.OPEN)
        self.page_url = reverse(
            "public-event-qna",
            kwargs={"event_id": self.event.public_id, "slug": self.event.slug},
        )
        self.client = Client()

    def _count(self) -> int:
        items, _counts, _etag, _session = services.list_questions(self.event.id)
        return len(items)

    def test_native_post_creates_one_question_and_redirects_clean(self) -> None:
        page = self.client.get(self.page_url)
        self.assertEqual(page.status_code, 200)
        csrf = page.cookies["csrftoken"].value
        response = self.client.post(
            self.page_url,
            data={"text": "Asked without JavaScript", "author_name": "No Script"},
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 303)
        # POST/redirect/GET back to the plain page: no authored content in
        # the URL.
        self.assertEqual(response["Location"], f"{services.event_qna_path(self.event)}/")
        self.assertNotIn("?", response["Location"])
        self.assertEqual(self._count(), 1)

    def test_csrf_is_enforced_on_the_native_post(self) -> None:
        strict = Client(enforce_csrf_checks=True)
        response = strict.post(self.page_url, data={"text": "No token"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._count(), 0)

    def test_rate_limit_rerenders_with_preserved_text(self) -> None:
        page = self.client.get(self.page_url)
        csrf = page.cookies["csrftoken"].value
        first = self.client.post(
            self.page_url,
            data={"text": "Once is fine", "author_name": ""},
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(first.status_code, 303)
        again = self.client.post(
            self.page_url,
            data={"text": "Once is fine", "author_name": ""},
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(again.status_code, 429)
        body = again.content.decode()
        self.assertEqual(self._count(), 1)
        self.assertIn("Once is fine", body)  # the draft survives in the body
        self.assertIn("Too many requests", body)

    def test_closed_session_post_rerenders_the_page_not_json(self) -> None:
        services.transition_session(self.event.id, EventQnaSession.State.CLOSED)
        page = self.client.get(self.page_url)
        csrf = page.cookies["csrftoken"].value
        response = self.client.post(
            self.page_url, data={"text": "Late question"}, HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(response.status_code, 409)
        body = response.content.decode()
        # HTML-escaped like any interpolated string; still the bounded,
        # human-safe service message.
        self.assertIn("is not accepting questions.", body)
        self.assertIn("Late question", body)
        self.assertEqual(self._count(), 0)

    def test_revision_conflict_rerenders_with_a_bounded_cue(self) -> None:
        page = self.client.get(self.page_url)
        csrf = page.cookies["csrftoken"].value
        with mock.patch(
            "event_qna.services.submit_question",
            side_effect=RevisionConflict(expected=1, actual=2),
        ):
            response = self.client.post(
                self.page_url, data={"text": "Raced the host"}, HTTP_X_CSRFTOKEN=csrf
            )
        self.assertEqual(response.status_code, 409)
        body = response.content.decode()
        self.assertIn("The session just changed; refresh and try again.", body)
        self.assertNotIn("expected 1", body)  # no revision internals
        self.assertIn("Raced the host", body)  # the draft survives in the body
        self.assertEqual(self._count(), 0)

    def test_get_rendering_is_unchanged_for_the_json_room(self) -> None:
        page = self.client.get(self.page_url)
        body = page.content.decode()
        self.assertIn('id="qna-question-form" method="post"', body)
        self.assertIn('name="csrfmiddlewaretoken"', body)
