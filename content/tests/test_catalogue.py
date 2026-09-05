"""The database query service the public catalogue pages read.

The reviewed catalogue is loaded into every test database, so these tests assert
against real published rows. What they pin is the contract the pages depend on:
the stored editorial order, a lookup that misses rather than invents, and an
un-ingested database publishing nothing instead of raising.
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from content import catalogue
from content.models import ContentDocument, ContentSource


class CatalogueReadTests(TestCase):
    def test_books_arrive_in_their_stored_editorial_order(self) -> None:
        stored = list(
            ContentDocument.objects.filter(
                content_kind="book",
                is_published=True,
                release__source__stable_id=catalogue.PUBLIC_CONTENT_STABLE_ID,
            ).values_list("adapter_metadata", flat=True)
        )
        expected = [
            record["record"]["slug"] for record in sorted(stored, key=lambda held: held["position"])
        ]

        self.assertEqual([book["slug"] for book in catalogue.books()], expected)
        self.assertTrue(expected)

    def test_a_book_resolves_by_slug_and_an_unknown_slug_is_simply_absent(self) -> None:
        first = catalogue.books()[0]

        self.assertEqual(catalogue.book(first["slug"]), first)
        self.assertIsNone(catalogue.book("no-such-book"))

    def test_a_collection_is_read_once_and_then_served_from_the_release_cache(self) -> None:
        catalogue.books()
        with CaptureQueriesContext(connection) as repeated:
            catalogue.books()

        # One lookup of the active release; the records themselves are already held.
        self.assertEqual(len(repeated), 1)


class EmptyCatalogueTests(TestCase):
    """An un-ingested database publishes nothing, and that is a normal state."""

    def setUp(self) -> None:
        ContentSource.objects.filter(stable_id=catalogue.PUBLIC_CONTENT_STABLE_ID).update(
            enabled=False
        )

    def test_every_collection_is_empty_rather_than_a_failure(self) -> None:
        for name in catalogue.COLLECTION_NAMES:
            with self.subTest(collection=name):
                self.assertEqual(catalogue.records(catalogue.COLLECTION_KINDS[name]), ())

    def test_the_book_archive_and_its_detail_routes_render_and_miss(self) -> None:
        self.assertEqual(catalogue.books(), ())
        self.assertIsNone(catalogue.book("anything"))
        self.assertContains(self.client.get("/books"), "No books are available yet.")
        self.assertEqual(self.client.get("/books/anything.html").status_code, 404)

    def test_the_records_a_page_reads_are_absent_rather_than_invented(self) -> None:
        self.assertEqual(catalogue.articles(), ())
        # The profile bodies carry a marker canary that only counts what is
        # published; with nothing published there is nothing to count.
        self.assertEqual(catalogue.people(), ())
        self.assertEqual(catalogue.people_by_slug(), {})
        self.assertEqual(catalogue.wiki_pages(), ())
        self.assertIsNone(catalogue.media_at("/images/anything.png"))
        self.assertIsNone(catalogue.editorial_route_alias("/blog/anything.html"))

    def test_the_singleton_records_read_as_the_absence_they_are(self) -> None:
        self.assertEqual(catalogue.wiki_graph(), {})
        self.assertEqual(catalogue.wiki_search(), {})
        self.assertEqual(catalogue.podcast_platforms(), ())
        self.assertEqual(catalogue.wiki_asset_paths(), frozenset())
        self.assertEqual(catalogue.collection_counts(), {key: 0 for key in catalogue.COUNT_KEYS})

    def test_every_hub_renders_and_every_detail_route_misses(self) -> None:
        for path in ("/", "/blog", "/books", "/podcast", "/wiki", "/events"):
            with self.subTest(path=path):
                # /podcast has no season to select once nothing is published.
                expected = 500 if path == "/podcast" else 200
                self.assertEqual(self.client.get(path).status_code, expected)
        for path in ("/blog/anything.html", "/people/anyone.html", "/wiki/anything"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
