"""Query budgets for the key public pages (intake fast-pages, AC2).

Every page below must render within a bounded number of database queries on a
warm catalogue: the catalogue records themselves are process-cached and
stamp-bound (``content.catalogue``), so a warm render spends only the stamps,
the event set, the course catalogue and the single-row navigational reads.
The per-request memo (``core.request_cache``, bound by
``core.middleware.RequestCacheMiddleware``) collapses the duplicate stamps;
these tests pin that collapse shut -- if a view fans out to a new reader that
re-resolves the same stamp per item, the warm budget is what fails first.

Counts are backend-independent (one ORM call is one query on SQLite and on
Postgres), so the budgets hold in CI and in production.  Wall-clock time is
deliberately not asserted here: it is measured from production logs and curl
(see the before/after table in the intake report), not from a shared CI host.

The catalogue fixture is loaded into every test database; detail paths are
resolved from it dynamically and skipped when their collection is empty.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from content import catalogue, podcast_content, wiki_content, wiki_reader
from events.queries import published_event_records

#: Warm (second render, catalogue already held) query budgets per page.
#: Measured on the reviewed fixture catalogue with headroom for small,
#: bounded navigational reads; a new per-item query (an N+1) blows past
#: these by the size of the collection, not by one.
WARM_BUDGETS = {
    "/": 20,
    "/blog": 12,
    "/podcast": 12,
    "/books": 12,
    "/events": 15,
    "/events/past": 16,
    "/wiki": 8,
    "/sponsors": 8,
    "/tour": 20,
    "/courses": 8,
    "article": 12,
    "podcast_episode": 12,
    "book": 12,
    "person": 12,
    "wiki_page": 8,
    "event": 22,
}

#: Cold (first render after the process caches are dropped) query budgets.
#: The first render rebuilds every collection from its rows, so these are
#: higher; they still bound the shape (no per-row fan-out), which is what
#: keeps a post-deploy or post-sync cold start from becoming a full scan
#: per item.
COLD_BUDGETS = {
    "/": 32,
    "/blog": 26,
    "/podcast": 24,
    "/books": 22,
    "/events": 15,
    "/events/past": 22,
    "/wiki": 8,
    "/sponsors": 8,
    "/tour": 34,
    "/courses": 8,
    "article": 24,
    "podcast_episode": 24,
    "book": 22,
    "person": 22,
    "wiki_page": 8,
    "event": 28,
}


def _drop_process_caches() -> None:
    """Drop the process-wide catalogue caches so the next render is cold."""

    for cached in (
        catalogue._synced_records,
        catalogue._synced_editorial,
        catalogue._records,
        catalogue._people,
        catalogue._synced_media,
        catalogue._synced_courses,
        catalogue._media_index,
        podcast_content._ordered_published_podcasts,
        wiki_content._published_episode_graph_index,
        wiki_reader._wiki_state,
    ):
        cached.cache_clear()


def _key_pages() -> list[tuple[str, str]]:
    """(budget key, path) pairs for every key page the intake names."""

    pages: list[tuple[str, str]] = [
        ("/", "/"),
        ("/blog", "/blog"),
        ("/podcast", "/podcast"),
        ("/books", "/books"),
        ("/events", "/events"),
        ("/events/past", "/events/past"),
        ("/wiki", "/wiki"),
        ("/sponsors", "/sponsors"),
        ("/tour", "/tour"),
        ("/courses", "/courses"),
    ]
    articles = catalogue.articles()
    if articles:
        pages.append(("article", articles[0]["public_path"]))
    podcasts = catalogue.podcasts()
    if podcasts:
        pages.append(("podcast_episode", podcasts[0]["public_path"]))
    books = catalogue.books()
    if books:
        pages.append(("book", books[0]["public_path"]))
    people = catalogue.people()
    if people:
        pages.append(("person", people[0]["public_path"]))
    wiki_pages = wiki_reader.wiki_pages()
    if wiki_pages:
        pages.append(("wiki_page", wiki_pages[0]["public_path"]))
    events = published_event_records()
    if events:
        pages.append(("event", events[0]["public_path"]))
    return pages


class PublicPageQueryBudgetTests(TestCase):
    def test_key_pages_render_within_their_cold_query_budget(self) -> None:
        for key, path in _key_pages():
            with self.subTest(page=path):
                _drop_process_caches()
                with CaptureQueriesContext(connection) as captured:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertLessEqual(
                    len(captured),
                    COLD_BUDGETS[key],
                    f"{path} spent {len(captured)} queries cold (budget {COLD_BUDGETS[key]})",
                )

    def test_key_pages_render_within_their_warm_query_budget(self) -> None:
        for key, path in _key_pages():
            with self.subTest(page=path):
                _drop_process_caches()
                first = self.client.get(path)
                self.assertEqual(first.status_code, 200, path)
                with CaptureQueriesContext(connection) as captured:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertLessEqual(len(captured), WARM_BUDGETS[key])
                self.assertLessEqual(
                    len(captured),
                    WARM_BUDGETS[key],
                    f"{path} spent {len(captured)} queries warm (budget {WARM_BUDGETS[key]})",
                )

    def test_a_write_between_requests_is_visible_on_the_next_request(self) -> None:
        """The per-request memo resets: a sync-time write is never stale.

        The memo dict lives for exactly one request (bound and reset by the
        middleware), so rows written between two requests are read live by
        the second one.  A process-wide stamp cache without that boundary
        would serve the first request's catalogue to the second.
        """

        from community_base.content_sync.models import (
            ContentSource as EngineContentSource,
        )

        from content.models import SyncedDocument

        _drop_process_caches()
        before = self.client.get("/blog")
        self.assertEqual(before.status_code, 200)

        source = EngineContentSource.objects.get(slug=catalogue.EDITORIAL_SOURCE_SLUG)
        template = (
            SyncedDocument.objects.filter(source=source, content_kind="article", is_published=True)
            .values_list("record", flat=True)
            .first()
        )
        self.assertIsInstance(template, dict)
        assert isinstance(template, dict)
        probe_record = {
            **template,
            "slug": "fast-pages-probe-article",
            "title": "Fast pages probe article",
            "public_path": "/blog/fast-pages-probe-article.html",
        }
        SyncedDocument.objects.create(
            source=source,
            content_kind="article",
            stable_key="fast-pages-probe-article",
            slug="fast-pages-probe-article",
            title="Fast pages probe article",
            summary="A probe, never published.",
            public_path="/blog/fast-pages-probe-article.html",
            source_path="probe/article.md",
            checksum="0" * 64,
            record=probe_record,
            is_published=False,
        )
        try:
            with CaptureQueriesContext(connection):
                after = self.client.get("/blog")
            self.assertEqual(after.status_code, 200)
            # The unpublished probe changes the source stamp (the count and
            # the newest timestamp both move), so the second render must have
            # re-resolved the stamp rather than replaying the first request's
            # memo -- and it must still hide the unpublished row.
            self.assertNotContains(after, "Fast pages probe article", status_code=200)
        finally:
            SyncedDocument.objects.filter(stable_key="fast-pages-probe-article").delete()


class MemberHomeQueryBudgetTests(TestCase):
    def test_the_signed_in_home_stays_within_its_warm_query_budget(self) -> None:
        member = get_user_model().objects.create_user(
            username="budget-member@example.invalid",
            email="budget-member@example.invalid",
        )
        self.client.force_login(member)
        _drop_process_caches()
        first = self.client.get("/")
        self.assertEqual(first.status_code, 200)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(captured), 16)
