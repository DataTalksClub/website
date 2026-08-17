from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from content import public_views
from content.public_data import (
    EventGroups,
    event_date_groups,
    event_groups,
    public_projection,
)
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


class EventDetailDesignSystemTests(TestCase):
    """The event detail page is a design 5a page (issue #179).

    It is the page a reader reaches by clicking a row on the index, so the two are
    written in one vocabulary: the same kind pill with the same mapping, the same
    date rail, the same dashed rows.  Before this rebuild the click-through stepped
    out of the design system and into the adopted shell mid-journey.
    """

    def detail(self, event: dict[str, object]) -> str:
        response = self.client.get(str(event["public_path"]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_detail_carries_its_own_stylesheet_and_loads_no_legacy_css(self) -> None:
        body = self.detail(event_groups().upcoming[0])

        self.assertIn("<style>", body)
        self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', body), [])
        for retired in (
            "/static/courses.css",
            "/static/core/site_shell.css",
            "/static/core/accessibility.css",
            "tailwindcss",
            "fontawesome",
            "home-events-cmp-surface",
        ):
            self.assertNotIn(retired, body)
        for leak in ("{#", "#}", "{%", "%}", "{{", "}}"):
            self.assertNotIn(leak, body)

    def test_the_page_is_built_from_the_shared_design_system_primitives(self) -> None:
        event = event_groups().upcoming[0]
        body = self.detail(event)

        self.assertIn('<nav class="shell event-shell breadcrumbs" aria-label="Breadcrumb">', body)
        self.assertIn('<a href="/events">Events</a>', body)
        # The dateline is one <time> around both the date and the clock: the machine
        # value carries a clock, so the accessible text has to name the zone, and the
        # zone only exists in display_clock.
        self.assertIn(f'<time class="when event-when" datetime="{event["starts_at"]}">', body)
        self.assertIn('<span class="when-time">', body)
        self.assertIn(f"<strong>{event['display_date']}</strong>", body)
        self.assertIn(f'<h1 id="event-heading">{event["title"]}</h1>', body)
        # One h1, and nothing on the page reaches back into the retired shell.
        self.assertEqual(body.count("<h1"), 1)
        self.assertNotIn("primer-button", body)

    def test_the_kind_pill_and_the_state_word_match_the_index(self) -> None:
        groups = event_groups()
        upcoming = groups.upcoming[0]
        past = groups.recent[0]

        upcoming_body = self.detail(upcoming)
        past_body = self.detail(past)

        self.assertIn(
            f'<span class="status-pill status-pill-open">{upcoming["type"]}</span>',
            upcoming_body,
        )
        self.assertIn(
            f'<span class="status-pill status-pill-wait">{past["type"]}</span>',
            past_body,
        )
        # State is never colour alone: the pill beside the kind carries the word.
        self.assertIn('<span class="status-pill">upcoming</span>', upcoming_body)
        self.assertIn('<span class="status-pill">past</span>', past_body)
        self.assertNotIn('<span class="status-pill">past</span>', upcoming_body)
        self.assertNotIn('<span class="status-pill">upcoming</span>', past_body)

    def test_a_live_podcast_recording_keeps_the_mint_pill_the_index_gives_it(self) -> None:
        podcast = {**event_groups().upcoming[0], "type": "podcast"}

        with patch.object(public_views, "event_groups", lambda: EventGroups((podcast,), ())):
            body = self.detail(podcast)

        self.assertIn('<span class="status-pill status-pill-mint">podcast</span>', body)
        self.assertIn('<span class="status-pill">upcoming</span>', body)

    def test_speakers_and_links_keep_their_destinations_and_their_labels(self) -> None:
        event = next(
            event
            for event in (*event_groups().upcoming, *event_groups().recent)
            if event["speakers"] and event["links"]
        )
        body = self.detail(event)

        self.assertIn('<h2 id="event-speakers-heading">Speakers</h2>', body)
        self.assertIn('<ul class="row-list speaker-rows">', body)
        for speaker in event["speakers"]:
            self.assertIn(
                f'<a class="band-link" href="{speaker["public_path"]}">{speaker["name"]}</a>',
                body,
            )
        # The links keep the group label the old heading carried, so the actions are
        # still named without adding a heading between the title and the body.
        self.assertIn('aria-label="Event links"', body)
        self.assertEqual(body.count('aria-label="Event links"'), 1)
        for link in event["links"]:
            self.assertIn(f'href="{link["url"]}"', body)
            self.assertIn(link["label"], body)
        self.assertIn('<span class="sr-only"> (opens in a new tab)</span>', body)

    def test_an_event_without_speakers_or_links_draws_neither_of_them(self) -> None:
        """Every catalogued event has a speaker or a link, so this state is composed.

        The projection carries no event without both, and an optional part that is only
        ever exercised with data present is exactly the one that breaks when it is not.
        """

        bare = {**event_groups().upcoming[0], "speakers": (), "links": ()}

        with patch.object(public_views, "event_groups", lambda: EventGroups((bare,), ())):
            body = self.detail(bare)

        self.assertIn(f'<h1 id="event-heading">{bare["title"]}</h1>', body)
        # Asserted on the markup rather than on the words, because the page's own
        # stylesheet comments name the parts it draws.
        self.assertNotIn('id="event-speakers-heading"', body)
        self.assertNotIn('<ul class="row-list speaker-rows">', body)
        self.assertNotIn('aria-label="Event links"', body)
        self.assertNotIn('class="cta cta-compact cta-secondary"', body)
