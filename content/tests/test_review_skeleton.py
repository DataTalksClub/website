from __future__ import annotations

import re
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve

from content.public_data import event_groups, public_projection
from courses.models.cohort import Cohort
from events.queries import published_event_records
from scripts import build_public_projection as projection_builder
from test_support.content_state import requires_media_bytes, requires_published_events

from .pagination_support import catalogue_body


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.destinations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        attribute = "action" if tag == "form" else "href" if tag in {"a", "link"} else "src"
        if tag in {"a", "form", "link", "img", "script"} and values.get(attribute):
            self.destinations.append(values[attribute] or "")


class PublicProjectionTests(TestCase):
    projection: dict[str, Any]

    @classmethod
    def setUpTestData(cls) -> None:
        cls.projection = public_projection()

    def test_accepted_provenance(self) -> None:
        self.assertTrue(self.projection["manifest"]["sources"]["preferred_content"]["accepted"])
        self.assertFalse(self.projection["manifest"]["sources"]["fallback_selection"]["accepted"])
        self.assertEqual(
            self.projection["manifest"]["sources"]["preferred_content"]["revision"],
            "1375c506dbce85c7c0e5e61f83c753128c5a48d1",
        )
        self.assertEqual(
            self.projection["manifest"]["sources"]["preferred_content"]["editorial_overlay_sha256"],
            "b2e6f23da40b6afbc310340196101422ac5de466b89e409c0ce5f24f5bf20326",
        )
        self.assertEqual(
            self.projection["manifest"]["wiki_assets"],
            {
                "/wiki/assets/og-default.png": (
                    "afddea001f9cf846630cb7a8046352a52d4d6c2edacd0feaaecb0e8d9b27e8de"
                )
            },
        )
        self.assertEqual(
            self.projection["manifest"]["runtime_contract"]["source_execution"],
            "none",
        )
        for collection in (
            "articles",
            "podcasts",
            "books",
            "people",
            "wiki",
            "courses",
            "media",
        ):
            for record in self.projection[collection]:
                self.assertRegex(record["provenance"]["checksum"], r"^[0-9a-f]{64}$")
                self.assertTrue(record["provenance"]["source_path"])
                self.assertTrue(record["provenance"]["source_key"])
        # Events carry their provenance on the identity row rather than in the
        # catalogue, so they are checked from the records the pages read.
        for record in published_event_records():
            with self.subTest(event=record["slug"]):
                self.assertTrue(record["provenance"]["repository"])
                self.assertTrue(record["provenance"]["source_key"])

    def test_editorial_provenance_keeps_owner_approved_internal_sources(self) -> None:
        preferred_revision = self.projection["manifest"]["sources"]["preferred_content"]["revision"]
        legacy_revision = self.projection["manifest"]["sources"]["legacy_main"]["revision"]
        for collection, prefix in (
            ("articles", "articles/"),
            ("podcasts", "podcasts/"),
            ("books", "books/"),
        ):
            for record in self.projection[collection]:
                with self.subTest(slug=record["slug"]):
                    self.assertEqual(record["provenance"]["repository"], "DataTalksClub/content")
                    self.assertEqual(record["provenance"]["revision"], preferred_revision)
                    self.assertTrue(record["provenance"]["source_path"].startswith(prefix))
        for podcast in self.projection["podcasts"]:
            if podcast["transcript"]:
                self.assertEqual(
                    podcast["transcript_provenance"]["repository"],
                    "DataTalksClub/content",
                )
                self.assertEqual(
                    podcast["transcript_provenance"]["revision"],
                    preferred_revision,
                )
        for person in self.projection["people"]:
            self.assertEqual(
                person["provenance"]["repository"],
                "DataTalksClub/datatalksclub.github.io",
            )
            self.assertEqual(person["provenance"]["revision"], legacy_revision)
            self.assertTrue(person["provenance"]["source_path"].startswith("_people/"))
        for event in published_event_records():
            self.assertEqual(
                event["provenance"]["repository"],
                "DataTalksClub/datatalksclub.github.io",
            )
            self.assertEqual(event["provenance"]["revision"], legacy_revision)
            self.assertEqual(event["provenance"]["source_path"], "_data/events.yaml")

    def test_hubs_render_every_checked_record(self) -> None:
        """Every record is still reachable, now across the pages of a hub that pages.

        The books archive and the Wiki catalogue are paged (issues #174, #175), so a
        hub is read the way a visitor reads it: page one, then whatever page one links
        to.  Nothing may fall out of the catalogue between the pages.
        """

        for path, collection in (
            ("/blog", "articles"),
            ("/books", "books"),
            ("/wiki", "wiki"),
        ):
            with self.subTest(path=path):
                body = catalogue_body(self.client, path)
                for record in self.projection[collection]:
                    self.assertIn(f'href="{record["public_path"]}"', body)

        events = self.client.get("/events").content.decode()
        archive = catalogue_body(self.client, "/events/past")
        for event in published_event_records():
            self.assertIn(f'href="{event["public_path"]}"', events + archive)

    def test_book_details_render_source_backed_questions_and_answers(self) -> None:
        book = self.projection["books_by_slug"]["20201214-ml-bookcamp"]
        first_thread = book["archive"][0]
        self.assertEqual(first_thread["name"], "Vladimir Finkelshtein")
        self.assertIn("timeseries", first_thread["text"])
        self.assertEqual(first_thread["replies"][0]["name"], "Alexey Grigorev")

        response = self.client.get(book["public_path"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Questions and answers")
        self.assertContains(response, "Vladimir Finkelshtein")
        self.assertContains(response, "timeseries")
        self.assertContains(response, "Alexey Grigorev")
        self.assertContains(response, "data-book-question")
        self.assertContains(response, "data-book-answer")
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertNotContains(response, "<script>alert")

        current_book = self.projection["books_by_slug"]["20250922-how-software-fails"]
        self.assertEqual(current_book["archive"], [])
        current_response = self.client.get(current_book["public_path"])
        self.assertNotContains(current_response, "Questions and answers")

    def test_a_markdown_summary_renders_as_real_markup_not_literal_syntax(self) -> None:
        """A book summary is source Markdown, not plain text (issue: raw Markdown).

        The record's own summary carries a bold heading and a bullet list written
        in Markdown.  Before the fix the page printed the source characters
        literally; it must now print the emphasis and the list they describe.
        """

        book = self.projection["books_by_slug"]["20250908-machine-learning-algorithms-in-depth"]
        self.assertIn("**Algorithms You'll Explore**", book["summary"])
        self.assertIn("* Monte Carlo Stock Price Simulation", book["summary"])

        response = self.client.get(book["public_path"])
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertContains(response, "<strong>Algorithms You'll Explore</strong>")
        self.assertContains(response, "<li>Monte Carlo Stock Price Simulation</li>")
        self.assertNotIn("**Algorithms You'll Explore**", body)
        self.assertNotIn("* Monte Carlo Stock Price Simulation", body)

    def test_the_book_detail_page_drops_the_redundant_promotional_flyer(self) -> None:
        """The "Book of the Week" flyer image duplicated the page's own heading,

        byline and cover credit, so it was removed from the visible body (issue:
        redundant promotional image).  The author's own person-chip portrait is a
        different image and stays; the Open Graph/Twitter share metadata, which
        legitimately reuses the flyer image, must still carry it.
        """

        book = self.projection["books_by_slug"]["20250908-machine-learning-algorithms-in-depth"]
        self.assertTrue(book["media_available"])
        self.assertTrue(book["image_path"])

        response = self.client.get(book["public_path"])
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertNotIn(f'src="{book["image_path"]}"', body)
        self.assertNotIn("book-cover", body)
        self.assertNotIn(f'alt="Artwork for {book["title"]}"', body)
        self.assertIn(book["image_path"], body)
        self.assertContains(response, 'property="og:image"')
        self.assertContains(response, 'name="twitter:image"')

    def test_every_selected_detail_is_safe_and_canonical(self) -> None:
        catalogue = [
            record
            for collection in ("articles", "podcasts", "books", "people", "wiki")
            for record in self.projection[collection]
        ]
        for record in [*catalogue, *published_event_records()]:
            with self.subTest(slug=record["slug"]):
                response = self.client.get(record["public_path"])
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertIn(escape(record["title"]), body)
                self.assertContains(
                    response,
                    f'<link rel="canonical" href="https://datatalks.club{record["public_path"]}">',
                )
                provenance = record["provenance"]
                blocked_values = {
                    provenance["repository"],
                    provenance["revision"],
                    provenance["checksum"],
                    provenance["source_url"],
                    "Checked source",
                    "View source on GitHub",
                    "This page is maintained on",
                }
                transcript_provenance = record.get("transcript_provenance")
                if transcript_provenance:
                    blocked_values.update(
                        {
                            transcript_provenance["repository"],
                            transcript_provenance["revision"],
                            transcript_provenance["checksum"],
                            transcript_provenance["source_url"],
                        }
                    )
                for value in blocked_values:
                    self.assertNotIn(value, body)
                self.assertEqual(self.client.head(record["public_path"]).status_code, 200)
                self.assertEqual(self.client.post(record["public_path"]).status_code, 405)

    @requires_published_events
    def test_people_relationships_use_exact_book_ids_and_collapse_recording_lineage(self) -> None:
        people = self.projection["people_by_slug"]
        book_paths = self.projection["books_by_path"]
        expected_book_relationships = {
            (author, book["public_path"])
            for book in self.projection["books"]
            for author in book["authors"]
            if author in people
        }
        actual_book_relationships = {
            (person["slug"], relationship["public_path"])
            for person in self.projection["people"]
            for relationship in person["relationships"]
            if relationship["role"] == "author" and relationship["public_path"] in book_paths
        }
        self.assertEqual(actual_book_relationships, expected_book_relationships)
        self.assertIn(
            {
                "role": "author",
                "label": "Designing Machine Learning Systems",
                "public_path": "/books/20220627-designing-machine-learning-systems.html",
            },
            people["chiphuyen"]["relationships"],
        )

        lineage = projection_builder._podcast_event_lineage(
            list(self.projection["podcasts"]),
            list(published_event_records()),
        )
        podcasts = self.projection["podcasts_by_slug"]
        events = {record["slug"]: record for record in published_event_records()}
        for event_slug, podcast_slug in lineage.items():
            event = events[event_slug]
            podcast = podcasts[podcast_slug]
            shared_people = {speaker["key"] for speaker in event["speakers"]} & set(
                podcast["guests"]
            )
            for person_slug in shared_people:
                with self.subTest(event=event_slug, podcast=podcast_slug, person=person_slug):
                    relationships = people[person_slug]["relationships"]
                    self.assertIn(
                        {
                            "role": "guest",
                            "label": podcast["title"],
                            "public_path": podcast["public_path"],
                        },
                        relationships,
                    )
                    self.assertNotIn(
                        {
                            "role": "speaker",
                            "label": event["title"],
                            "public_path": event["public_path"],
                        },
                        relationships,
                    )

        bela_relationships = people["belawiertz"]["relationships"]
        early_stage_podcast_path = next(
            record["public_path"]
            for record in self.projection["podcasts"]
            if record["title"]
            == (
                "Early-Stage Investing in Open Source Developer Tools: Deal Sourcing, Due "
                "Diligence & Commercialization Models"
            )
        )
        self.assertIn(
            {
                "role": "guest",
                "label": (
                    "Early-Stage Investing in Open Source Developer Tools: Deal Sourcing, Due "
                    "Diligence & Commercialization Models"
                ),
                "public_path": early_stage_podcast_path,
            },
            bela_relationships,
        )
        self.assertNotIn(
            {
                "role": "speaker",
                "label": "Investing in Open-Source Data Tools",
                "public_path": events["investing-in-open-source-data-tools"]["public_path"],
            },
            bela_relationships,
        )
        self.assertIn("investing-in-open-source-data-tools", events)

    @requires_published_events
    def test_event_boundaries_are_timezone_aware(self) -> None:
        before = event_groups(datetime.fromisoformat("2026-08-30T12:00:00+02:00"))
        after = event_groups(datetime.fromisoformat("2026-09-01T12:00:00+02:00"))
        self.assertTrue(all(event["starts_at_value"].tzinfo for event in before.upcoming))
        self.assertEqual(
            before.upcoming[-1]["slug"],
            "ai-dev-tools-zoomcamp-2026-course-launch",
        )
        self.assertFalse(after.upcoming)

    def test_wiki_fragments_and_corpus_targets_resolve(self) -> None:
        for page in self.projection["wiki"]:
            response = self.client.get(page["public_path"])
            body = response.content.decode()
            for fragment in page["fragment_ids"]:
                self.assertRegex(body, rf'id="{re.escape(fragment)}"')

        person_relations = [
            relation
            for page in self.projection["wiki"]
            for relation in page["relations"]
            if relation["type"] == "person"
        ]
        self.assertTrue(
            all(relation["href"].startswith("/people/") for relation in person_relations)
        )

    def test_wiki_machine_and_server_rendered_routes_use_only_wiki_mount(self) -> None:
        for path in (
            "/wiki?q=machine+learning",
            "/wiki?q=%3Cscript%3E",
            "/wiki/graph",
            "/wiki/special-pages",
            "/wiki/special-pages/guides",
            "/wiki/feed.xml",
            "/wiki/sitemap.xml",
            "/wiki/robots.txt",
            "/wiki/graph/graph.json",
            "/wiki/search-corpus.json",
            "/wiki/assets/og-default.png",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                if not path.endswith((".png", ".json")):
                    self.assertNotIn("/podwiki/", response.content.decode())

        graph = self.client.get("/wiki/graph/graph.json").json()
        search = self.client.get("/wiki/search-corpus.json").json()
        for document in graph["nodes"] + search["docs"]:
            url = document.get("url", "")
            self.assertFalse(url.startswith("/podwiki/"))
            if url.startswith("/wiki/"):
                self.assertNotIn("/wiki/wiki/", url)

    def test_podwiki_family_is_an_ordinary_real_404(self) -> None:
        for path in ("/podwiki", "/podwiki/", "/podwiki/wiki/a-a-testing/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("Location", response.headers)
            self.assertContains(response, "Page not found", status_code=404)

    def test_a_media_path_outside_the_catalogue_is_not_served(self) -> None:
        self.assertEqual(self.client.get("/images/../../manage.py").status_code, 404)

    @requires_media_bytes
    def test_media_routes_are_local_and_checked(self) -> None:
        """Every recorded object is served, with the content type its record names.

        The records are database rows, but the bytes come from the configured
        media store, so this needs a checkout whose local store has been
        hydrated. Without one every request is a fail-closed 502 -- which is the
        store behaving correctly, not the route being wrong.
        """

        for record in self.projection["media"]:
            with self.subTest(path=record["public_path"]):
                response = self.client.get(record["public_path"])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Content-Type"], record["content_type"])

    def test_public_projection_requests_do_not_mutate_the_database(self) -> None:
        paths = [
            "/",
            "/events",
            "/blog",
            "/wiki",
            "/wiki?q=data",
            "/courses",
        ]
        with CaptureQueriesContext(connection) as captured:
            for path in paths:
                self.assertEqual(self.client.get(path).status_code, 200)
        mutations = [
            query["sql"]
            for query in captured
            if re.match(r"\s*(INSERT|UPDATE|DELETE)", query["sql"], re.I)
        ]
        self.assertEqual(mutations, [])

    def test_existing_cmp_course_row_preserves_its_detail_view(self) -> None:
        record = self.projection["courses"][0]
        Cohort.objects.create(
            title=record["title"],
            slug=record["slug"],
            description="Database-backed course marker",
            visible=True,
        )
        response = self.client.get(record["public_path"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Database-backed course marker")

    def test_existing_cmp_course_rows_preserve_their_list_view(self) -> None:
        Cohort.objects.create(
            title="Database-backed catalog marker",
            slug="database-backed-catalog-marker",
            visible=True,
        )
        response = self.client.get("/courses")
        self.assertEqual(response.status_code, 200)
        self.assertIn("active_courses", response.context)
        self.assertContains(response, "Database-backed catalog marker")

    def test_rendered_projection_links_never_use_the_removed_mount(self) -> None:
        paths = (
            "/",
            "/blog",
            "/podcast",
            "/books",
            "/events",
            "/courses",
            "/wiki",
            "/wiki/graph",
        )
        for path in paths:
            parser = LinkParser()
            parser.feed(self.client.get(path).content.decode())
            self.assertFalse(any("/podwiki/" in destination for destination in parser.destinations))
            for destination in parser.destinations:
                parsed = urlsplit(destination)
                if parsed.scheme or parsed.netloc:
                    self.assertIn(parsed.scheme, {"http", "https"})
                    continue
                if not parsed.path or parsed.path.startswith("/static/"):
                    continue
                try:
                    resolve(parsed.path)
                except Resolver404 as exc:
                    self.fail(f"{path} links to unresolved local path {destination}: {exc}")

    @override_settings(NOINDEX=True)
    def test_development_sitemaps_keep_the_checked_structure_under_noindex(self) -> None:
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<sitemapindex", response.content.decode())
        self.assertIn("https://datatalks.club/sitemaps/wiki.xml", response.content.decode())
        self.assertIn(
            "https://datatalks.club/wiki/a-a-testing",
            self.client.get("/wiki/sitemap.xml").content.decode(),
        )

    @override_settings(NOINDEX=False)
    def test_production_sitemap_contains_projected_wiki_paths(self) -> None:
        body = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("https://datatalks.club/sitemaps/wiki.xml", body)
        self.assertNotIn("/podwiki/", body)
        wiki_body = self.client.get("/wiki/sitemap.xml").content.decode()
        self.assertIn("https://datatalks.club/wiki/a-a-testing", wiki_body)
