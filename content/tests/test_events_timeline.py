from __future__ import annotations

import re
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from content import public_views
from content.pagination import PUBLIC_PAGE_SIZE
from content.public_data import (
    EventGroups,
    event_date_groups,
    event_groups,
    public_projection,
)
from events.identity import canonical_detail_path
from events.models import Event

from .pagination_support import catalogue_page_bodies


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
        self.assertContains(past_page, 'aria-label="Past event pages"')
        self.assertEqual(
            len(re.findall(r'<article class="card event-card">', past_page.content.decode())),
            min(PUBLIC_PAGE_SIZE, len(groups.recent)),
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


class PastEventPaginationTests(TestCase):
    """The archive on the shared paginator (issues #177, #178).

    The archive's own parser, page-path builder, context keys and control markup are
    gone; what remains is the caller configuration.  Order and date grouping stay
    exactly where they were: paginate the deterministic sequence, then group the
    slice by Europe/Berlin calendar date.
    """

    def test_the_pages_partition_the_recent_sequence_in_its_existing_order(self) -> None:
        recent = event_groups().recent
        pages = catalogue_page_bodies(self.client, "/events/past")

        self.assertEqual(len(pages), -(-len(recent) // PUBLIC_PAGE_SIZE))
        seen: list[str] = []
        for index, body in enumerate(pages):
            expected = recent[index * PUBLIC_PAGE_SIZE : (index + 1) * PUBLIC_PAGE_SIZE]
            found = re.findall(r'<h3>\s*<a href="(/events/[^"]+)">', body)
            self.assertEqual(found, [event["public_path"] for event in expected])
            seen.extend(found)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), len(recent))

    def test_a_date_group_spanning_the_boundary_repeats_its_heading_once_per_page(self) -> None:
        """Page size is fixed, so a group is split rather than the page stretched.

        The catalogue has no such date today, and the case that is only ever exercised
        by chance is the one that breaks.  This composes it: a conference day whose
        four sessions straddle the boundary between page one and page two.
        """

        template = event_groups().recent[0]
        berlin = ZoneInfo("Europe/Berlin")
        conference_day = datetime(2026, 3, 14, 10, tzinfo=berlin)
        recent = []
        for position in range(25):
            # Items 19-22 (0-based) share one Berlin date, so the group is cut by the
            # 20-record page.  Everything else gets a day of its own.
            if 18 <= position <= 21:
                starts_at = conference_day.replace(hour=10 + position - 18)
            else:
                starts_at = datetime(2026, 6, 1, 10, tzinfo=berlin) - timedelta(days=position)
            recent.append(
                {
                    **template,
                    "title": f"Composed past event {position:02d}",
                    "public_path": f"/events/{9000 + position}/composed-past-event",
                    "starts_at_value": starts_at,
                    "identity_id": f"00000000-0000-4000-8000-0000000{position:05d}",
                }
            )

        with patch.object(
            public_views, "event_groups", lambda *args: EventGroups((), tuple(recent))
        ):
            first = self.client.get("/events/past").content.decode()
            second = self.client.get("/events/past?page=2").content.decode()

        key = conference_day.date().isoformat()
        # The rail repeats the day beside each of that day's rows, so the same
        # heading appears on both pages: two of the four sessions on each.
        self.assertEqual(first.count(f'<time datetime="{key}">'), 2)
        self.assertEqual(second.count(f'<time datetime="{key}">'), 2)
        # The split does not change how many events each page carries, and no event
        # is duplicated across the boundary or lost at it.
        self.assertEqual(first.count('<article class="card event-card">'), PUBLIC_PAGE_SIZE)
        self.assertEqual(second.count('<article class="card event-card">'), 5)
        titles = re.findall(r"Composed past event \d\d", first + second)
        self.assertEqual(len(titles), len(set(titles)))
        self.assertEqual(len(titles), len(recent))

    def test_page_one_is_clean_and_a_later_page_names_itself(self) -> None:
        for spelling in ("/events/past", "/events/past?page=1"):
            with self.subTest(spelling=spelling):
                body = self.client.get(spelling).content.decode()
                self.assertIn("<title>Past events — DataTalks.Club</title>", body)
                self.assertIn(
                    '<link rel="canonical" href="https://datatalks.club/events/past">', body
                )
                self.assertNotIn('?page=1"', body)

        body = self.client.get("/events/past?page=2").content.decode()
        self.assertIn("<title>Past events — Page 2 — DataTalks.Club</title>", body)
        self.assertIn('<link rel="prev" href="https://datatalks.club/events/past">', body)
        self.assertIn('<link rel="next" href="https://datatalks.club/events/past?page=3">', body)

    def test_the_upcoming_view_carries_no_page_control_and_accepts_no_page_query(self) -> None:
        upcoming = self.client.get("/events")
        body = upcoming.content.decode()

        self.assertEqual(upcoming.status_code, 200)
        self.assertNotIn('<nav class="pagination"', body)
        self.assertNotIn('aria-label="Past event pages"', body)
        self.assertEqual(self.client.get("/events?page=2").status_code, 400)

    def test_the_archive_control_is_the_shared_labelled_landmark(self) -> None:
        body = self.client.get("/events/past?page=2").content.decode()
        markup = body.split('<nav class="pagination"', 1)[1].split("</nav>", 1)[0]

        self.assertEqual(body.count('<nav class="pagination"'), 1)
        self.assertIn('aria-label="Past event pages"', markup)
        self.assertEqual(markup.count('aria-current="page"'), 1)
        self.assertIn('class="filter-pills pagination-pills"', markup)
        # The retired archive-only control and its rule are gone.
        self.assertNotIn("event-pagination", body)
        self.assertNotIn("Page 2 of ", body)

    def test_an_empty_archive_says_so_and_offers_no_controls(self) -> None:
        with patch.object(
            public_views, "event_groups", lambda *args: EventGroups(event_groups().upcoming, ())
        ):
            response = self.client.get("/events/past")
            body = response.content.decode()
            beyond = self.client.get("/events/past?page=2")
            upcoming = self.client.get("/events")

        self.assertEqual(response.status_code, 200)
        self.assertIn("No past events.", body)
        self.assertNotIn('<nav class="pagination"', body)
        self.assertEqual(beyond.status_code, 404)
        self.assertEqual(beyond.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(upcoming.status_code, 200)

    def test_every_archive_link_is_the_numeric_current_slug_canonical(self) -> None:
        body = "".join(catalogue_page_bodies(self.client, "/events/past"))
        hrefs = re.findall(r'<h3>\s*<a href="(/events/[^"]+)">', body)

        self.assertTrue(hrefs)
        for href in hrefs:
            self.assertRegex(href, r"^/events/[1-9][0-9]*/[-a-z0-9]+$")
        self.assertNotRegex(body, r"/events/[0-9a-f]{8}-[0-9a-f]{4}-")


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
        self.assertRegex(
            upcoming,
            rf'class="status-pill status-pill-open"[^>]*>{first_upcoming["type"]}</span>',
        )
        self.assertIn('class="status-pill status-pill-wait"', past)
        self.assertNotIn('class="status-pill status-pill-wait"', upcoming)
        self.assertNotIn('class="status-pill status-pill-open"', past)

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
        """Every kind is still explained — now on the pill that carries it.

        The index used to list all four kinds above the timeline, which said all
        of them to every reader whether or not they wanted any.  The explanation
        now hangs off the pill it explains, so the property to hold is that a
        kind appearing on this page brings its own explanation with it.
        """

        kinds = {event["type"] for event in public_projection()["events"]}
        self.assertEqual(kinds, {kind for kind, _ in self.EXPLANATIONS})

        for path in ("/events", "/events/past"):
            with self.subTest(path=path):
                body = self.collapsed_body(path)
                shown = {kind for kind, _ in self.EXPLANATIONS if f">{kind}</span>" in body}
                self.assertTrue(shown)

                for kind, explanation in self.EXPLANATIONS:
                    if kind in shown:
                        self.assertIn(explanation, body)

    def test_every_kind_pill_is_bound_to_its_own_explanation(self) -> None:
        """The tip is reachable, not hover-only, and it names its pill."""

        body = self.collapsed_body("/events")

        described = re.findall(r'aria-describedby="(kind-tip-[^"]+)"', body)
        self.assertTrue(described)
        for target in described:
            self.assertIn(f'<span class="kind-tip" role="tooltip" id="{target}">', body)
        # A pill that carries a tip takes focus, so a keyboard reaches it.
        self.assertEqual(body.count('aria-describedby="kind-tip-'), body.count('tabindex="0"'))
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

        self.assertRegex(
            upcoming_body,
            rf'class="status-pill status-pill-open"[^>]*>{upcoming["type"]}</span>',
        )
        self.assertRegex(
            past_body,
            rf'class="status-pill status-pill-wait"[^>]*>{past["type"]}</span>',
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

        self.assertRegex(body, r'class="status-pill status-pill-mint"[^>]*>podcast</span>')
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
            # A speaker is the shared person chip: their portrait beside their
            # name, and the name is the link to their profile.
            self.assertIn(
                f'<a class="band-link person-chip-name" '
                f'href="{speaker["public_path"]}">{speaker["name"]}</a>',
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
