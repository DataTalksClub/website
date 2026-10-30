"""Anonymous-only full-page cache (intake fast-pages, AC2).

Public pages must stay fast for logged-out readers without ever serving one
viewer's bytes to another: the first anonymous ``GET`` renders normally and
the second one is served from the shared page cache with two cache-table
reads (generation token + page entry) and no template render, while
logged-in requests bypass lookup and store alike and keep their ``private,
no-store`` directives.  Any database write bumps the shared generation once
it commits, so content published between two requests is visible on the
next one.
"""

from __future__ import annotations

import pathlib
from typing import Any

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from content import catalogue
from content.tests.test_public_page_query_budgets import _drop_process_caches, _key_pages
from core import migrations as core_migrations
from core.page_cache import (
    CACHEABLE_VIEWS,
    cache_generation,
    reset_page_cache_state,
)


@override_settings(ANONYMOUS_PAGE_CACHE_ENABLED=True)
class AnonymousPageCacheTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        _drop_process_caches()
        reset_page_cache_state()

    def tearDown(self) -> None:
        reset_page_cache_state()
        super().tearDown()

    def _miss_then_hit(self, path: str) -> tuple[Any, Any]:
        first = self.client.get(path)
        self.assertEqual(first.status_code, 200, path)
        self.assertNotIn("X-Page-Cache", first.headers, path)
        with CaptureQueriesContext(connection) as captured:
            second = self.client.get(path)
        self.assertEqual(second.status_code, 200, path)
        self.assertEqual(second.headers.get("X-Page-Cache"), "HIT", path)
        # A hit reads the shared generation token and the page entry from the
        # cache table and nothing else: the cap proves no page-level query
        # (stamps, catalogue, events) runs on the served path.
        self.assertLessEqual(len(captured), 2, f"{path} hit spent {len(captured)} queries")
        return first, second

    def test_key_pages_serve_the_second_anonymous_hit_from_two_cache_reads(self) -> None:
        for _key, path in _key_pages():
            with self.subTest(page=path):
                first, second = self._miss_then_hit(path)
                self.assertEqual(second.content, first.content, path)
                self.assertEqual(
                    second.headers.get("Cache-Control"),
                    first.headers.get("Cache-Control"),
                    path,
                )
                self.assertIn("Cookie", second.headers.get("Vary", ""), path)

    def test_shared_backend_table_names_agree(self) -> None:
        """The settings alias, the module constant and the migration are one table."""

        from django.conf import settings

        from core.page_cache import PAGE_CACHE_TABLE

        caches_setting: dict[str, Any] = settings.CACHES
        self.assertEqual(caches_setting["anonymous_pages"]["LOCATION"], PAGE_CACHE_TABLE)
        migration_source = (
            pathlib.Path(core_migrations.__file__).parent / "0011_anonymous_page_cache_table.py"
        )
        self.assertIn("from core.page_cache import PAGE_CACHE_TABLE", migration_source.read_text())

    def test_every_key_page_view_is_cache_eligible(self) -> None:
        for _budget_key, path in _key_pages():
            with self.subTest(page=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                match = response.wsgi_request.resolver_match
                self.assertIsNotNone(match, path)
                assert match is not None
                func = match.func
                view_class = getattr(func, "view_class", None)
                if view_class is not None:
                    view_path = f"{view_class.__module__}.{view_class.__name__}"
                else:
                    view_path = f"{func.__module__}.{func.__name__}"
                self.assertIn(view_path, CACHEABLE_VIEWS, path)

    def test_logged_in_requests_neither_hit_nor_store(self) -> None:
        member = get_user_model().objects.create_user(
            username="page-cache-member@example.invalid",
            email="page-cache-member@example.invalid",
        )
        self.client.force_login(member)
        for path in ("/blog", "/"):
            with self.subTest(page=path):
                first = self.client.get(path)
                self.assertEqual(first.status_code, 200, path)
                self.assertNotIn("X-Page-Cache", first.headers, path)
                second = self.client.get(path)
                self.assertEqual(second.status_code, 200, path)
                self.assertNotIn("X-Page-Cache", second.headers, path)
                directives = {
                    directive.strip().casefold()
                    for directive in second.headers.get("Cache-Control", "").split(",")
                }
                self.assertTrue({"private", "no-store"}.issubset(directives), path)
        # A logged-in render must not prime the anonymous cache either.
        self.client.logout()
        response = self.client.get("/blog")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Page-Cache", response.headers)

    def test_unknown_cookie_bypasses_but_preference_cookie_still_hits(self) -> None:
        self._miss_then_hit("/blog")
        preference = self.client_class()
        preference.cookies.load({"browser_timezone": "Europe/Berlin"})
        hit = preference.get("/blog")
        self.assertEqual(hit.headers.get("X-Page-Cache"), "HIT")
        stranger = self.client_class()
        stranger.cookies.load({"someone_elses_state": "1"})
        miss = stranger.get("/blog")
        self.assertEqual(miss.status_code, 200)
        self.assertNotIn("X-Page-Cache", miss.headers)

    def test_search_query_is_never_cached(self) -> None:
        for _ in range(2):
            response = self.client.get("/wiki", {"q": "launch"})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("X-Page-Cache", response.headers)

    def test_uncacheable_statuses_are_never_stored(self) -> None:
        response = self.client.get("/no-such-page-here")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("X-Page-Cache", response.headers)
        again = self.client.get("/no-such-page-here")
        self.assertEqual(again.status_code, 404)
        self.assertNotIn("X-Page-Cache", again.headers)

    def test_out_of_range_pagination_is_never_stored(self) -> None:
        for path in ("/blog?page=99", "/events/past?page=99"):
            with self.subTest(page=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404, path)
                self.assertNotIn("X-Page-Cache", response.headers)
                again = self.client.get(path)
                self.assertEqual(again.status_code, 404, path)
                self.assertNotIn("X-Page-Cache", again.headers)

    def test_a_published_write_between_requests_is_visible_next(self) -> None:
        from community_base.content_sync.models import (
            ContentSource as EngineContentSource,
        )

        from content.models import SyncedDocument

        before = self.client.get("/blog")
        self.assertEqual(before.status_code, 200)
        self.assertNotContains(before, "Fast pages cache probe", status_code=200)
        generation = cache_generation()

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
            "slug": "zzz-fast-pages-cache-probe",
            "title": "Fast pages cache probe",
            "public_path": "/blog/zzz-fast-pages-cache-probe.html",
        }
        # The invalidation waits for the write to commit (transaction.on_commit),
        # so the test replays the commit through the capture helper.
        with self.captureOnCommitCallbacks(execute=True):
            SyncedDocument.objects.create(
                source=source,
                content_kind="article",
                stable_key="zzz-fast-pages-cache-probe",
                slug="zzz-fast-pages-cache-probe",
                title="Fast pages cache probe",
                summary="A probe, published.",
                public_path="/blog/zzz-fast-pages-cache-probe.html",
                source_path="probe/article.md",
                checksum="1" * 64,
                record=probe_record,
                is_published=True,
            )
        try:
            # The committed write orphaned the stored page, so the next
            # request re-renders and sees the probe instead of the stale bytes.
            self.assertIsNotNone(cache_generation())
            self.assertNotEqual(cache_generation(), generation)
            after = self.client.get("/blog")
            self.assertNotIn("X-Page-Cache", after.headers)
            self.assertContains(after, "Fast pages cache probe", status_code=200)
            third = self.client.get("/blog")
            self.assertEqual(third.headers.get("X-Page-Cache"), "HIT")
            self.assertContains(third, "Fast pages cache probe", status_code=200)
        finally:
            SyncedDocument.objects.filter(stable_key="zzz-fast-pages-cache-probe").delete()
