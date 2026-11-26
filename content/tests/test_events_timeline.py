from __future__ import annotations

import re
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from community_base.events.models import Event
from django.db import connection
from django.db.models import Max
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.html import escape

from content import catalogue, public_views
from content.event_content import (
    EventGroups,
    event_date_groups,
    event_groups,
    event_timeline_groups,
)
from content.pagination import PUBLIC_PAGE_SIZE
from content.public_routes import public_paths
from events.identity import canonical_detail_path
from events.queries import published_event_records

from .pagination_support import catalogue_page_bodies

STABLE_EVENT_NOW = datetime(2026, 8, 12, tzinfo=ZoneInfo("Europe/Berlin"))


class StableEventClockTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.event_clock = patch(
            "content.event_content.timezone.now",
            return_value=STABLE_EVENT_NOW,
        )
        self.event_clock.start()
        self.addCleanup(self.event_clock.stop)


class EventTimelineDataTests(StableEventClockTestCase):
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

    def test_timeline_groups_for_chosen_paths_mix_past_and_future_newest_first(self) -> None:
        grouped = event_groups()
        if not grouped.upcoming or not grouped.recent:
            self.skipTest("the dataset needs both an upcoming and a past event")
        chosen = (grouped.upcoming[0], grouped.recent[0])
        groups = event_timeline_groups(
            [event["public_path"] for event in chosen] + ["/events/0/not-published"]
        )
        events = [event for group in groups for event in group.events]

        self.assertEqual(
            [event["public_path"] for event in events],
            [
                grouped.upcoming[0]["public_path"],
                grouped.recent[0]["public_path"],
            ],
        )
        self.assertEqual([event["is_past"] for event in events], [False, True])
        self.assertEqual(event_timeline_groups([]), ())


class EventTimelineRouteTests(StableEventClockTestCase):
    def test_retired_legacy_event_paths_are_plain_404s(self) -> None:
        """Events are addressed only by public id: no retired spelling resolves.

        A manifest-sourced event's ``source_key`` still carries the retired
        date-prefixed spelling verbatim, so it stands in for the removed
        ``legacy_date_path`` alias rows without depending on data that no longer
        exists.  UUID spellings and out-of-range numeric ids resolve to nothing
        as well; a numeric spelling is a public id and selects the event by
        itself, so the storage pk is deliberately not exercised here — in the
        shared-app world the two are indistinguishable at the URL level.
        """

        events = Event.objects.order_by("source_identity__source_key")
        assigned_public_id_max = Event.objects.aggregate(max_public_id=Max("public_id"))[
            "max_public_id"
        ]
        unassigned_public_id = assigned_public_id_max + 1_000_000
        for event in events:
            for path in (
                f"/events/{event.source_identity.source_key}",
                f"/events/{event.source_identity.source_key}/",
                f"/events/{event.content_id}",
                f"/events/{event.content_id}/{event.slug}",
                f"/events/{unassigned_public_id}",
                f"/events/{unassigned_public_id}/{event.slug}",
            ):
                with self.subTest(path=path):
                    response = self.client.get(path, follow=False)
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn("Location", response.headers)

    def test_event_identity_errors_and_unsafe_methods_have_bounded_cache_headers(self) -> None:
        event = Event.objects.order_by("source_identity__source_key").first()
        assert event is not None
        canonical = canonical_detail_path(event.content_id)
        stale = f"/events/{event.public_id}/stale-title"

        for path in (
            "/events/00000000-0000-4000-8000-000000000000/nope",
            "/events/not-inventoried",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertContains(response, "Page not found", status_code=404)

        for path in (canonical, stale):
            with self.subTest(path=path):
                response = self.client.post(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["Allow"], "GET, HEAD")
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")

        for path in (canonical, stale):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 200 if path == canonical else 301)
                if path != canonical:
                    self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")

    def test_clean_hub_is_upcoming_and_past_filter_is_paginated(self) -> None:
        # The expectation reads the same clock the view reads.  It used to be
        # frozen at 2026-08-12 while the pages it was compared against were
        # rendered against the real one, so the test held only until the
        # calendar reached the first event of that frozen split: on 2026-08-17
        # the event the frozen clock called upcoming was genuinely past, and
        # finding it on /events/past failed a test that was describing the
        # catalogue rather than the routing this test is about.
        groups = event_groups()
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
            len(
                re.findall(
                    r'<article class="event-item interactive-card interactive-lift">',
                    past_page.content.decode(),
                )
            ),
            min(PUBLIC_PAGE_SIZE, len(groups.recent)),
        )
        # The pages escape the titles they draw, so the needle has to be the
        # escaped form: the raw string could never match and the check would
        # pass even if the other timeline leaked onto the page. Pin an
        # ampersand-bearing past title so that coverage does not depend on
        # whichever record happens to be recent[0] or upcoming[0].
        amp_recent = next(event for event in groups.recent if "&" in event["title"])
        self.assertNotEqual(escape(amp_recent["title"]), amp_recent["title"])
        self.assertNotContains(upcoming, escape(groups.recent[0]["title"]))
        self.assertNotContains(upcoming, escape(amp_recent["title"]))
        self.assertNotContains(past_page, escape(groups.upcoming[0]["title"]))
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

        # A campaign tag selects nothing, so a tagged link is the plain index, and a
        # tagged legacy link still redirects on the selector it does carry.
        for query in ("utm_source=newsletter", "fbclid=IwAR0synthetic"):
            with self.subTest(query=query):
                tagged = self.client.get(f"/events?{query}")
                self.assertEqual(tagged.status_code, 200)
                self.assertNotIn(query, tagged.content.decode())

                redirected = self.client.get(f"/events?filter=past&page=2&{query}")
                self.assertEqual(redirected.status_code, 301)
                self.assertEqual(redirected["Location"], "/events/past?page=2")

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


class PastEventPaginationTests(StableEventClockTestCase):
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
            found = re.findall(r'<h4><a href="(/events/[^"]+)">', body)
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
        # The timeline draws one day heading per page for the group, so the same
        # heading appears once on each page, over two of the four sessions.
        self.assertEqual(first.count(f'<time datetime="{key}">'), 1)
        self.assertEqual(second.count(f'<time datetime="{key}">'), 1)
        for page in (first, second):
            day = page.split(f'<time datetime="{key}">')[1]
            day = day.split("</section>")[0]
            self.assertEqual(day.count('<article class="event-item'), 2)
        # The split does not change how many events each page carries, and no event
        # is duplicated across the boundary or lost at it.
        self.assertEqual(
            first.count('<article class="event-item interactive-card interactive-lift">'),
            PUBLIC_PAGE_SIZE,
        )
        self.assertEqual(
            second.count('<article class="event-item interactive-card interactive-lift">'),
            5,
        )
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

    def test_the_slash_alias_is_one_permanent_hop_that_preserves_the_raw_query(self) -> None:
        """`/events/past/` stays the archive's explicit slash alias (issue #177).

        One hop to the clean path, the viewer's raw query kept byte-for-byte —
        the rule every hub alias follows — and an unsafe method answered before
        any redirect work, like the archive itself is.
        """

        for method in ("get", "head"):
            with self.subTest(method=method):
                response = getattr(self.client, method)("/events/past/?utm_source=qa", follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], "/events/past?utm_source=qa")
                self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")
        self.assertEqual(self.client.head("/events/past/?utm_source=qa", follow=False).content, b"")
        post = self.client.post("/events/past/")
        self.assertEqual(post.status_code, 405)
        self.assertEqual(post.headers["Allow"], "GET, HEAD")
        self.assertEqual(post.headers["Cache-Control"], "no-store, max-age=0")

    def test_a_page_beyond_the_last_is_a_rendered_public_404(self) -> None:
        """A valid number past the real end is a miss, not a nearest-page guess.

        The empty-archive case pins page two of nothing; this one pins the page
        after the last real page of the populated archive, which is the miss a
        visitor or a crawler actually reaches by walking the numbers up.
        """

        page_count = -(-len(event_groups().recent) // PUBLIC_PAGE_SIZE)
        for page in (page_count + 1, 999):
            with self.subTest(page=page):
                response = self.client.get(f"/events/past?page={page}")
                head = self.client.head(f"/events/past?page={page}")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(head.status_code, 404)
                self.assertEqual(head.content, b"")
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                self.assertContains(response, "Page not found", status_code=404)
                # The refusal names neither the page asked for nor the last real
                # one, and offers no canonical a miss could be indexed under.
                self.assertNotContains(response, f"page={page}", status_code=404)
                self.assertNotContains(response, f"page={page_count}", status_code=404)
                self.assertNotContains(response, 'rel="canonical"', status_code=404)
                self.assertNotContains(response, "Traceback", status_code=404)

    def test_malformed_page_queries_on_the_real_archive_are_bounded_400s(self) -> None:
        """The archive itself refuses malformed selectors, not only the fixture.

        The shared paginator's grammar is pinned on its synthetic catalogue; this
        walks the same malformed spellings through the real route, where a
        bespoke parser could still be hiding behind the shared include.
        """

        for query in ("page=", "page=0", "page=01", "page=%31", "page=1&page=2", "page=1000"):
            with self.subTest(query=query):
                response = self.client.get(f"/events/past?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                self.assertContains(response, "Bad request", status_code=400)
                # The needle is the escaped form, as #192 pinned for this file:
                # a reflected selector would reach the document escaped, and the
                # raw spelling of an ampersand-bearing query could never match.
                self.assertNotContains(response, escape(query), status_code=400)
                self.assertNotContains(response, 'rel="canonical"', status_code=400)
                self.assertNotContains(response, "Traceback", status_code=400)
                # The inlined design-system stylesheet alone is ~190 KB, so the
                # budget is a runaway guard (a reflected or looping page), not a
                # byte count of the shell.
                self.assertLess(len(response.content), 300_000)

    def test_page_two_publishes_its_exact_canonical_and_og_url(self) -> None:
        """Canonical and OG agree with the selected representation on every page.

        Page one — however it is spelled — is the clean URL in both, and a later
        page is its own query URL in both, so a share and a crawl name the same
        address.
        """

        body = self.client.get("/events/past?page=2").content.decode()
        self.assertIn(
            '<link rel="canonical" href="https://datatalks.club/events/past?page=2">', body
        )
        self.assertIn(
            '<meta property="og:url" content="https://datatalks.club/events/past?page=2">', body
        )
        for spelling in ("/events/past", "/events/past?page=1"):
            with self.subTest(spelling=spelling):
                first = self.client.get(spelling).content.decode()
                self.assertIn(
                    '<meta property="og:url" content="https://datatalks.club/events/past">', first
                )

    def test_the_archive_rejects_unsafe_methods_and_answers_head_identically(self) -> None:
        post = self.client.post("/events/past?page=2")
        self.assertEqual(post.status_code, 405)
        self.assertEqual(post.headers["Allow"], "GET, HEAD")
        self.assertEqual(post.headers["Cache-Control"], "no-store, max-age=0")

        get_response = self.client.get("/events/past?page=2")
        head_response = self.client.head("/events/past?page=2")
        self.assertEqual(head_response.status_code, get_response.status_code)
        self.assertEqual(
            head_response.headers["Cache-Control"], get_response.headers["Cache-Control"]
        )
        self.assertEqual(head_response.content, b"")

    def test_every_archive_link_is_the_numeric_current_slug_canonical(self) -> None:
        body = "".join(catalogue_page_bodies(self.client, "/events/past"))
        hrefs = re.findall(r'<h4><a href="(/events/[^"]+)">', body)

        self.assertTrue(hrefs)
        for href in hrefs:
            self.assertRegex(href, r"^/events/[1-9][0-9]*/[-a-z0-9]+$")
        self.assertNotRegex(body, r"/events/[0-9a-f]{8}-[0-9a-f]{4}-")

    def test_clean_archive_hub_is_in_the_public_path_registry(self) -> None:
        paths = public_paths()

        self.assertIn("/events", paths)
        self.assertIn("/events/past", paths)
        self.assertFalse(any("?page=" in path for path in paths))

    def test_the_events_sitemap_lists_both_clean_hubs_and_no_page_query(self) -> None:
        sitemap = self.client.get("/sitemaps/events.xml").content.decode()

        self.assertIn("<loc>https://datatalks.club/events</loc>", sitemap)
        self.assertIn("<loc>https://datatalks.club/events/past</loc>", sitemap)
        self.assertNotIn("?page=", sitemap)
        self.assertEqual(sitemap.count("https://datatalks.club/events/past"), 1)


class EventTimelineTemplateTests(StableEventClockTestCase):
    def test_timeline_uses_numeric_paths_at_runtime(self) -> None:
        self.assertTrue(
            all(
                re.fullmatch(r"/events/[1-9][0-9]*/[-a-z0-9]+", event["public_path"])
                for event in published_event_records()
            )
        )
        self.assertTrue(
            all(event["public_path"].startswith("/events/") for event in published_event_records())
        )
        for event in published_event_records():
            public_id = event["public_id"]
            self.assertIsInstance(public_id, int)
            assert isinstance(public_id, int)
            self.assertGreater(public_id, 0)
            self.assertEqual(event["public_path"], f"/events/{public_id}/{event['slug']}")


class EventIndexDesignSystemTests(StableEventClockTestCase):
    """The events index is a design system page (issue #179, mockup 6c).

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
            self.assertIn('<div class="event-timeline" data-event-view=', body)
            self.assertIn('<section class="event-day" aria-labelledby="event-day-', body)
            self.assertIn('<span class="event-day-dot" aria-hidden="true"></span>', body)
            self.assertIn('<p class="event-item-time">', body)
            self.assertIn('<ul class="event-item-meta">', body)
            self.assertIn(
                '<article class="event-item interactive-card interactive-lift">',
                body,
            )
        # The kind is a quiet word in the card's meta line, never a boxed pill
        # (owner feedback 2026-11-25): upcoming says where, past says whether
        # there is a recording.
        kind = escape(str(first_upcoming.get("kind_label") or first_upcoming["type"]))
        self.assertRegex(
            re.sub(r"\s+", " ", upcoming),
            rf"<span>\s*{re.escape(kind.capitalize())} · Live online\s*</span>",
        )
        self.assertNotIn(" · Live online", past)
        self.assertRegex(past, r"<span>[^<]+</span>\s*</li>")
        if any(event.get("has_youtube_link") for event in event_groups().recent[:PUBLIC_PAGE_SIZE]):
            self.assertRegex(re.sub(r"\s+", " ", past), r" · Recording on YouTube\s*</span>")
        for body in (upcoming, past):
            self.assertNotRegex(body, r'class="status-pill status-pill-(open|wait|mint)"')
            self.assertNotIn('class="kind-tip"', body)
            self.assertNotIn('class="when-kind"', body)

    def test_the_view_pills_mark_the_current_view_through_aria(self) -> None:
        upcoming = self.client.get("/events").content.decode()
        past = self.client.get("/events/past").content.decode()

        self.assertIn(
            '<a class="filter-pill interactive-lift" href="/events" '
            'aria-current="page">Upcoming events</a>',
            upcoming,
        )
        self.assertIn(
            '<a class="filter-pill interactive-lift" href="/events/past">Past events</a>',
            upcoming,
        )
        self.assertIn(
            '<a class="filter-pill interactive-lift" href="/events/past" '
            'aria-current="page">Past events</a>',
            past,
        )
        self.assertIn(
            '<a class="filter-pill interactive-lift" href="/events">Upcoming events</a>',
            past,
        )


class EventDetailDesignSystemTests(StableEventClockTestCase):
    """The event detail page is a design system page (issue #179).

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

        self.assertNotIn('<nav class="shell event-shell breadcrumbs"', body)
        # The dateline is one <time> around both the date and the clock: the machine
        # value carries a clock, so the accessible text has to name the zone, and the
        # zone only exists in display_clock.
        self.assertIn(f'<time class="event-fact-text" datetime="{event["starts_at"]}">', body)
        self.assertIn('<span class="when-time-range">', body)
        self.assertIn(f"<strong>{escape(event['display_date_long'])}</strong>", body)
        # The h1 escapes the title it draws (an "&" in a title reaches the
        # document as "&amp;"), so the expected markup is the escaped form.
        self.assertIn(f'<h1 id="event-heading">{escape(event["title"])}</h1>', body)
        # One h1, and nothing on the page reaches back into the retired shell.
        self.assertEqual(body.count("<h1"), 1)
        self.assertNotIn("primer-button", body)

    def test_the_kind_leads_the_dateline_and_no_state_pill_is_drawn(self) -> None:
        """The kind is a word before the date, as on the cards; the date alone says
        whether the event has happened, so there is no past/upcoming pill."""

        groups = event_groups()
        upcoming = groups.upcoming[0]
        past = groups.recent[0]

        upcoming_body = self.detail(upcoming)
        past_body = self.detail(past)

        self.assertIn(f'<span class="when-kind">{upcoming["type"]}</span>', upcoming_body)
        self.assertIn(f'<span class="when-kind">{past["type"]}</span>', past_body)
        for body in (upcoming_body, past_body):
            self.assertNotIn('<span class="status-pill">upcoming</span>', body)
            self.assertNotIn('<span class="status-pill">past</span>', body)
            self.assertNotRegex(body, r'class="status-pill status-pill-(open|wait|mint)"')

    def test_display_tags_render_on_events_index_and_detail(self) -> None:
        base = next(iter((*event_groups().upcoming, *event_groups().recent)))
        tagged = {
            **base,
            "tags": ["Академия"],
            "kind_label": f"{base['type']} · Академия",
        }

        with patch.object(public_views, "event_groups", lambda: EventGroups((tagged,), ())):
            index_response = self.client.get("/events")
            detail_body = self.detail(tagged)

        self.assertEqual(index_response.status_code, 200)
        index_body = index_response.content.decode("utf-8")
        self.assertRegex(
            re.sub(r"\s+", " ", index_body),
            rf"<span>\s*{re.escape(tagged['type'].capitalize())}"
            r" · Академия · Live online\s*</span>",
        )
        self.assertIn(f'<span class="when-kind">{tagged["type"]} · Академия</span>', detail_body)

    def test_speakers_and_links_keep_their_destinations_and_their_labels(self) -> None:
        event = next(
            event
            for event in (*event_groups().upcoming, *event_groups().recent)
            if event["speakers"] and event["links"]
        )
        body = self.detail(event)

        self.assertIn('<h2 id="event-hosts-heading" class="event-side-heading">Speakers</h2>', body)
        self.assertIn('<ul class="event-host-list">', body)
        for speaker in event["speakers"]:
            # A speaker is the shared person chip: their portrait beside their
            # name, and the name is the link to their profile.
            self.assertIn(
                f'<a class="band-link person-chip-name" '
                f'href="{speaker["public_path"]}">{escape(speaker["name"])}</a>',
                body,
            )
        # The links keep the group label the old heading carried, so the actions are
        # still named without adding a heading between the title and the body.
        self.assertIn('<ul class="event-facts">', body)
        self.assertEqual(body.count('<li class="event-fact event-link">'), len(event["links"]))
        for link in event["links"]:
            self.assertIn(f'href="{link["url"]}"', body)
            self.assertIn(escape(link["label"]), body)
        self.assertIn('<span class="sr-only"> (opens in a new tab)</span>', body)

    def test_event_speakers_render_their_canonical_profile_bio(self) -> None:
        event = next(
            event
            for event in published_event_records()
            if event["slug"] == "ai-dev-tools-zoomcamp-2026-course-launch"
        )
        speaker = event["speakers"][0]
        bio = catalogue.people_by_slug()[speaker["key"]]["blocks"][0]["text"]
        body = self.detail(event)

        self.assertIn(
            '<h2 id="event-speakers-heading" class="event-section-head">About the speakers</h2>',
            body,
        )
        self.assertIn('<article class="event-speaker">', body)
        self.assertIn('<div class="prose event-speaker-bio">', body)
        self.assertIn(escape(bio), body)
        # The normalization lane removed the duplicate from the event description;
        # the dedicated speaker region is now its one public rendering home.
        self.assertNotIn("About the Speaker", body)

    def test_an_ampersand_in_an_event_title_is_escaped_in_the_heading(self) -> None:
        event = next(item for item in published_event_records() if "&" in item["title"])
        self.assertNotEqual(escape(event["title"]), event["title"])
        body = self.detail(event)

        self.assertIn(f'<h1 id="event-heading">{escape(event["title"])}</h1>', body)

    def test_an_apostrophe_in_a_speaker_name_is_escaped_in_the_chip(self) -> None:
        event = next(
            item
            for item in published_event_records()
            if any("'" in speaker["name"] for speaker in item["speakers"])
        )
        speaker = next(item for item in event["speakers"] if "'" in item["name"])
        self.assertNotEqual(escape(speaker["name"]), speaker["name"])
        body = self.detail(event)

        self.assertIn(
            f'<a class="band-link person-chip-name" '
            f'href="{speaker["public_path"]}">{escape(speaker["name"])}</a>',
            body,
        )

    def test_an_event_without_speakers_or_links_draws_neither_of_them(self) -> None:
        """Every catalogued event has a speaker or a link, so this state is composed.

        The projection carries no event without both, and an optional part that is only
        ever exercised with data present is exactly the one that breaks when it is not.
        """

        bare = {**event_groups().upcoming[0], "speakers": (), "links": ()}

        with patch.object(public_views, "event_groups", lambda: EventGroups((bare,), ())):
            body = self.detail(bare)

        self.assertIn(f'<h1 id="event-heading">{escape(bare["title"])}</h1>', body)
        # Asserted on the markup rather than on the words, because the page's own
        # stylesheet comments name the parts it draws.
        self.assertNotIn('id="event-speakers-heading"', body)
        self.assertNotIn('<ul class="row-list speaker-rows">', body)
        self.assertNotIn('aria-label="Event links"', body)
        self.assertNotIn('class="cta cta-compact cta-secondary"', body)


class PastArchiveQueryBudgetTests(StableEventClockTestCase):
    """`/events/past` reads the event rows once, however many events exist.

    The archive paginates the recent sequence and then renders it, and the
    render used to call ``event_groups()`` a second time -- doubling the row
    fetch and the speaker prefetch on every request, which tied up gunicorn
    workers behind 6s+ past-page responses.  These tests pin the single read
    and the absence of any per-event query.
    """

    BUDGET_PUBLIC_ID_BASE = 95_000

    def setUp(self) -> None:
        super().setUp()
        from events.queries import _cached_published_records

        _cached_published_records.cache_clear()
        self.addCleanup(_cached_published_records.cache_clear)
        self._seeded = 0

    def _seed_past_events(self, count: int) -> None:
        """Add ``count`` completed events well before the frozen test clock."""

        import uuid

        base = STABLE_EVENT_NOW - timedelta(days=60)
        Event.objects.bulk_create(
            [
                Event(
                    content_id=uuid.uuid4(),
                    public_id=self.BUDGET_PUBLIC_ID_BASE + self._seeded + index,
                    title=f"Budget past event {self._seeded + index}",
                    slug=f"budget-past-{self._seeded + index}",
                    status="completed",
                    start_datetime=base - timedelta(days=index),
                    kind="standard",
                    tags=["dtc-type:webinar"],
                )
                for index in range(count)
            ]
        )
        self._seeded += count

    def _past_queries(self) -> list[str]:
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/events/past")
        self.assertEqual(response.status_code, 200)
        return [query["sql"] for query in captured.captured_queries]

    def _row_fetch_queries(self, queries: list[str]) -> list[str]:
        # The full-row fetch selects the event schedule; the stamp queries
        # that guard the cache only count and max the table.
        return [sql for sql in queries if '"events_event"."start_datetime"' in sql]

    def test_past_archive_reads_event_rows_once_per_request(self) -> None:
        self._seed_past_events(25)

        cold = self._past_queries()

        self.assertEqual(len(self._row_fetch_queries(cold)), 1)

    def test_past_archive_query_count_ignores_event_volume(self) -> None:
        self._seed_past_events(25)
        self._past_queries()
        small = self._past_queries()

        self._seed_past_events(50)
        self._past_queries()
        large = self._past_queries()

        self.assertEqual(len(large), len(small))
        self.assertLessEqual(len(small), 20)
