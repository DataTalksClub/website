from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase

from content.public_data import event_date_groups, event_groups, public_projection
from content.public_views import EVENT_PAGE_SIZE
from events.identity import canonical_detail_path
from events.models import Event


class EventTimelineDataTests(SimpleTestCase):
    def test_date_groups_use_local_calendar_date_and_preserve_input_order(self) -> None:
        first = {
            "starts_at_value": datetime(2026, 8, 17, 22, tzinfo=ZoneInfo("UTC")),
            "identity_id": "00000000-0000-4000-8000-000000000001",
        }
        second = {
            "starts_at_value": datetime(2026, 8, 18, 0, tzinfo=ZoneInfo("UTC")),
            "identity_id": "00000000-0000-4000-8000-000000000002",
        }
        groups = event_date_groups([first, second])

        self.assertEqual([group.key for group in groups], ["2026-08-18"])
        self.assertEqual(groups[0].weekday, "Tuesday")
        self.assertEqual(groups[0].events, (first, second))

    def test_event_groups_are_deterministic_with_title_and_uuid_ties(self) -> None:
        current = datetime(2026, 8, 12, tzinfo=ZoneInfo("Europe/Berlin"))
        grouped = event_groups(current)

        self.assertEqual(
            [event["starts_at_value"] for event in grouped.upcoming],
            sorted(event["starts_at_value"] for event in grouped.upcoming),
        )
        self.assertEqual(
            [group.key for group in grouped.recent_groups],
            sorted((group.key for group in grouped.recent_groups), reverse=True),
        )
        self.assertEqual(
            sum(len(group.events) for group in grouped.upcoming_groups),
            len(grouped.upcoming),
        )


class EventTimelineRouteTests(TestCase):
    def test_legacy_aliases_accept_trailing_slash_and_cache_redirects(self) -> None:
        events = Event.objects.prefetch_related("aliases").order_by("source_key")
        self.assertEqual(events.count(), 421)
        for event in events:
            alias = event.aliases.get()
            with self.subTest(alias=alias.source_path):
                response = self.client.get(
                    alias.source_path + "/?utm_source=qa",
                    follow=False,
                )

                self.assertEqual(response.status_code, 301)
                self.assertEqual(
                    response.headers["Location"],
                    canonical_detail_path(event.id) + "?utm_source=qa",
                )
                self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")

    def test_event_identity_errors_and_unsafe_methods_have_bounded_cache_headers(self) -> None:
        event = Event.objects.order_by("source_key").first()
        assert event is not None
        canonical = canonical_detail_path(event.id)
        stale = f"/events/{event.id}/stale-title"
        alias = event.aliases.get().source_path

        for path in (
            "/events/00000000-0000-4000-8000-000000000000/nope",
            "/events/not-inventoried",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers["Cache-Control"], "max-age=0")
                self.assertContains(response, "Page not found", status_code=404)

        for path in (canonical, stale, alias):
            with self.subTest(path=path):
                response = self.client.post(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["Allow"], "GET, HEAD")
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")

        for path in (canonical, stale, alias):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 200 if path == canonical else 301)
                if path != canonical:
                    self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")

    def test_clean_hub_is_upcoming_and_past_filter_is_paginated(self) -> None:
        groups = event_groups(datetime(2026, 8, 12, tzinfo=ZoneInfo("Europe/Berlin")))
        upcoming = self.client.get("/events")
        past = self.client.get("/events?filter=past")

        self.assertEqual(upcoming.status_code, 200)
        self.assertEqual(past.status_code, 200)
        self.assertContains(upcoming, 'href="/events?filter=past"')
        self.assertContains(upcoming, 'aria-current="page"')
        self.assertContains(upcoming, 'data-event-view="upcoming"')
        self.assertContains(past, 'data-event-view="past"')
        self.assertContains(past, "Page 1 of ")
        self.assertEqual(
            len(re.findall(r'<article class="grid gap-3 py-5">', past.content.decode())),
            min(EVENT_PAGE_SIZE, len(groups.recent)),
        )
        self.assertNotContains(upcoming, groups.recent[0]["title"])
        self.assertNotContains(past, groups.upcoming[0]["title"])
        self.assertContains(
            past,
            '<link rel="canonical" href="https://datatalks.club/events?filter=past">',
        )

    def test_past_pagination_preserves_filter_and_bad_queries_fail_closed(self) -> None:
        response = self.client.get("/events?filter=past&page=2")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/events?filter=past")
        self.assertContains(response, "/events?filter=past&amp;page=3")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/events?filter=past&amp;page=2">',
        )

        for query in ("page=2", "filter=upcoming", "filter=past&filter=past", "filter=past&page=0"):
            with self.subTest(query=query):
                bad = self.client.get(f"/events?{query}")
                self.assertEqual(bad.status_code, 400)
                self.assertEqual(bad.headers["Cache-Control"], "no-store, max-age=0")

    def test_event_catalogue_rejects_unsafe_methods_and_supports_head(self) -> None:
        response = self.client.post("/events")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET, HEAD")
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")

        get_response = self.client.get("/events?filter=past")
        head_response = self.client.head("/events?filter=past")
        self.assertEqual(head_response.status_code, get_response.status_code)
        self.assertEqual(
            head_response.headers["Cache-Control"], get_response.headers["Cache-Control"]
        )
        self.assertEqual(head_response.content, b"")


class EventTimelineTemplateTests(SimpleTestCase):
    def test_timeline_uses_uuid_paths_from_the_checked_projection(self) -> None:
        projection = public_projection()
        self.assertTrue(
            all("/events/202" not in event["public_path"] for event in projection["events"])
        )
        self.assertTrue(
            all(event["public_path"].startswith("/events/") for event in projection["events"])
        )
