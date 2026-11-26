"""Book pages answer at their dateless address; every dated address redirects to it.

The book goes through the real pipeline: the books parser reads a content-repo
YAML whose slug carries the week's date, the synced row keeps that dated slug as
its identity, and the routes serve the dateless public path it records.
"""

from __future__ import annotations

from community_base.content_sync.media import media_store
from community_base.content_sync.parsers import get_parser
from django.test import SimpleTestCase

from content.book_routes import book_canonical_path, book_public_slug
from content.models import SyncedDocument

from .test_sync_parsers import _book_yaml, _CheckoutCase, _source

OLD = "/books/20201214-ml-bookcamp.html"
NEW = "/books/ml-bookcamp.html"


class BookCanonicalPathTests(SimpleTestCase):
    def test_the_week_date_is_dropped_from_the_public_slug_only(self) -> None:
        self.assertEqual(book_public_slug("20230807-driving-data-quality"), "driving-data-quality")
        self.assertEqual(
            book_canonical_path("20230807-driving-data-quality"),
            "/books/driving-data-quality.html",
        )
        # A slug without a leading week date is already its own public slug.
        self.assertEqual(book_public_slug("2023-in-review"), "2023-in-review")
        self.assertEqual(book_public_slug("ml-bookcamp"), "ml-bookcamp")


class BookRouteTests(_CheckoutCase):
    def setUp(self) -> None:
        super().setUp()
        SyncedDocument.objects.filter(source__slug="dtc-content", content_kind="book").delete()
        source = _source("dtc-content")
        parser = get_parser("book")
        tree = {
            "books/2020/201214-ml-bookcamp.yaml": _book_yaml(),
            "images/books/20201214-ml-bookcamp/cover.jpg": b"jpeg-bytes",
        }
        with self.checkout(tree) as checkout:
            (item,) = parser.discover(checkout, source)
            parser.upsert(item, source, media_store())

    def test_the_stored_identity_keeps_the_dated_slug(self) -> None:
        row = SyncedDocument.objects.get(source__slug="dtc-content", content_kind="book")
        self.assertEqual(row.stable_key, "20201214-ml-bookcamp")
        self.assertEqual(row.slug, "20201214-ml-bookcamp")
        self.assertEqual(row.public_path, NEW)

    def test_the_dateless_address_renders_with_its_own_canonical(self) -> None:
        response = self.client.get(NEW)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, f'<link rel="canonical" href="https://datatalks.club{NEW}">', html=False
        )
        self.assertContains(
            response, f'<meta property="og:url" content="https://datatalks.club{NEW}">'
        )
        self.assertContains(response, f'"url": "https://datatalks.club{NEW}"')
        self.assertNotContains(response, "20201214-ml-bookcamp.html")

    def test_every_dated_address_redirects_permanently_in_one_hop(self) -> None:
        for old in (OLD, "/books/20201214-ml-bookcamp", "/books/20201214-ml-bookcamp/"):
            with self.subTest(old=old):
                response = self.client.get(old, follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], NEW)
                head = self.client.head(old, follow=False)
                self.assertEqual(head.status_code, 301)
                self.assertEqual(head["Location"], NEW)
                self.assertEqual(self.client.get(response["Location"]).status_code, 200)

    def test_the_redirect_keeps_the_query(self) -> None:
        response = self.client.get(f"{OLD}?utm_source=x", follow=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], f"{NEW}?utm_source=x")

    def test_the_bare_dateless_alias_redirects_to_the_canonical(self) -> None:
        for alias in ("/books/ml-bookcamp", "/books/ml-bookcamp/"):
            with self.subTest(alias=alias):
                response = self.client.get(alias, follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], NEW)

    def test_unknown_dated_and_dateless_addresses_miss(self) -> None:
        for path in ("/books/20201214-missing.html", "/books/missing.html", "/books/missing"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_the_redirect_refuses_unsafe_methods(self) -> None:
        self.assertEqual(self.client.post(OLD).status_code, 405)

    def test_the_sitemap_lists_only_the_dateless_address(self) -> None:
        body = self.client.get("/sitemaps/books.xml").content.decode()
        self.assertIn(f"https://datatalks.club{NEW}", body)
        self.assertNotIn(OLD, body)

    def test_the_books_archive_links_the_dateless_address(self) -> None:
        response = self.client.get("/books")
        self.assertContains(response, f'href="{NEW}"')
        self.assertNotContains(response, OLD)
