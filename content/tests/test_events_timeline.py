from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import TestCase

from content.public_data import event_date_groups, event_groups, public_projection
from content.public_views import EVENT_PAGE_SIZE
from events.identity import canonical_detail_path
from events.models import Event


class EventTimelineDataTests(TestCase):
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
            alias = (
                event.aliases.filter(kind="legacy_date_path")
                .exclude(source_path__endswith="/")
                .get()
            )
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
        alias = (
            event.aliases.filter(kind="legacy_date_path")
            .exclude(source_path__endswith="/")
            .get()
            .source_path
        )

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
        self.assertEqual(past.status_code, 301)
        self.assertEqual(past["Location"], "/events/past")
        past_page = self.client.get("/events/past")
        self.assertEqual(past_page.status_code, 200)
        self.assertContains(upcoming, 'href="/events/past"')
        self.assertContains(upcoming, 'aria-current="page"')
        self.assertContains(upcoming, 'data-event-view="upcoming"')
        self.assertContains(past_page, 'data-event-view="past"')
        self.assertContains(past_page, "Page 1 of ")
        self.assertEqual(
            len(re.findall(r'<article class="card event-card">', past_page.content.decode())),
            min(EVENT_PAGE_SIZE, len(groups.recent)),
        )
        self.assertNotContains(upcoming, groups.recent[0]["title"])
        self.assertNotContains(past_page, groups.upcoming[0]["title"])
        self.assertContains(
            past_page,
            '<link rel="canonical" href="https://datatalks.club/events/past">',
        )

    def test_past_pagination_preserves_filter_and_bad_queries_fail_closed(self) -> None:
        response = self.client.get("/events?filter=past&page=2")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/events/past?page=2")
        response = self.client.get("/events/past?page=2")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/events/past")
        self.assertContains(response, "/events/past?page=3")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/events/past?page=2">',
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

        get_response = self.client.get("/events/past")
        head_response = self.client.head("/events/past")
        self.assertEqual(head_response.status_code, get_response.status_code)
        self.assertEqual(
            head_response.headers["Cache-Control"], get_response.headers["Cache-Control"]
        )
        self.assertEqual(head_response.content, b"")


class EventTimelineTemplateTests(TestCase):
    def test_timeline_uses_numeric_paths_at_runtime(self) -> None:
        projection = public_projection()
        self.assertTrue(
            all(
                re.fullmatch(r"/events/[1-9][0-9]*/[-a-z0-9]+", event["public_path"])
                for event in projection["events"]
            )
        )
        self.assertTrue(
            all(event["public_path"].startswith("/events/") for event in projection["events"])
        )


class EventIndexDesignSystemTests(TestCase):
    """The events index is a design 5a page (issue #179, mockup 6c).

    It carries one inline stylesheet built from the shared design system partial, loads no
    external CSS, and composes its rows from shared primitives instead of forking them.
    """

    def test_index_carries_its_own_stylesheet_and_loads_no_legacy_css(self) -> None:
        for path in ("/events", "/events/past"):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()

                self.assertIn("<style>", body)
                self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', body), [])
                for retired in (
                    "/static/courses.css",
                    "/static/core/site_shell.css",
                    "/static/core/accessibility.css",
                    "tailwindcss",
                    "fontawesome",
                ):
                    self.assertNotIn(retired, body)

    def test_index_leaks_no_unrendered_template_syntax(self) -> None:
        for path in ("/events", "/events/past"):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()

                for leak in ("{#", "#}", "{%", "%}", "{{", "}}"):
                    self.assertNotIn(leak, body)

    def test_rows_are_built_from_the_shared_design_system_primitives(self) -> None:
        upcoming = self.client.get("/events").content.decode()
        past = self.client.get("/events/past").content.decode()
        first_upcoming = event_groups().upcoming[0]

        for body in (upcoming, past):
            self.assertIn('<div class="list-row event-row">', body)
            self.assertIn('<div class="when">', body)
            self.assertIn('<span class="when-day">', body)
            self.assertIn('<span class="when-time">', body)
            self.assertIn('<article class="card event-card">', body)
        # The kind pill states the event type in words, and only the surface changes:
        # lavender for an upcoming session, mint for a podcast, sand for anything past.
        self.assertIn(
            f'<span class="status-pill status-pill-open">{first_upcoming["type"]}</span>',
            upcoming,
        )
        self.assertIn('<span class="status-pill status-pill-wait">', past)
        self.assertNotIn('<span class="status-pill status-pill-wait">', upcoming)
        self.assertNotIn('<span class="status-pill status-pill-open">', past)

    def test_the_view_pills_mark_the_current_view_through_aria(self) -> None:
        upcoming = self.client.get("/events").content.decode()
        past = self.client.get("/events/past").content.decode()

        self.assertIn(
            '<a class="filter-pill" href="/events" aria-current="page">Upcoming events</a>',
            upcoming,
        )
        self.assertIn('<a class="filter-pill" href="/events/past">Past events</a>', upcoming)
        self.assertIn(
            '<a class="filter-pill" href="/events/past" aria-current="page">Past events</a>',
            past,
        )
        self.assertIn('<a class="filter-pill" href="/events">Upcoming events</a>', past)


class EventKindsExplainerTests(TestCase):
    """The explainer that gives the rows' kind pills their meaning.

    A visitor's first question on this page is what a "podcast" event is as opposed
    to a "webinar"; the answer has to stay on the page that asks it, on the upcoming
    view and the past view alike.
    """

    EXPLANATIONS = (
        ("webinar", "Webinars &ndash; events on Tuesday, with slides, mostly technical"),
        (
            "podcast",
            "Live podcasts &ndash; events on Friday, a discussion without slides, "
            "the recording is published as a podcast",
        ),
        ("workshop", "Workshop &ndash; hands-on tutorials about technical topics"),
        (
            "conference",
            "Conference &ndash; bigger events with multiple talks, both webinar-type "
            "talks and podcast-type talks",
        ),
    )

    def collapsed_body(self, path: str) -> str:
        body = self.client.get(path).content.decode()
        return re.sub(r"\s+", " ", body)

    def test_both_views_explain_every_kind_a_row_can_carry(self) -> None:
        kinds = {event["type"] for event in public_projection()["events"]}
        self.assertEqual(kinds, {kind for kind, _ in self.EXPLANATIONS})

        for path in ("/events", "/events/past"):
            with self.subTest(path=path):
                body = self.collapsed_body(path)

                self.assertIn('aria-label="Types of events"', body)
                for kind, explanation in self.EXPLANATIONS:
                    self.assertIn(f'<span class="status-pill">{kind}</span>', body)
                    self.assertIn(explanation, body)

    def test_the_explainer_is_built_from_the_shared_row_primitives(self) -> None:
        body = self.collapsed_body("/events")

        self.assertIn('<ul class="row-list event-types"', body)
        self.assertIn('<li class="list-row">', body)
        # No icon font: these pages load no external CSS, so the pill marks the row.
        self.assertNotIn("fa-tv", body)
        self.assertNotIn("fa-microphone-alt", body)
