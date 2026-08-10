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

from content.public_data import EXPECTED_COUNTS, event_groups, public_projection
from courses.models.course import Course
from scripts import build_public_projection as projection_builder


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

    def test_exact_counts_and_accepted_provenance(self) -> None:
        self.assertEqual(self.projection["manifest"]["counts"], EXPECTED_COUNTS)
        self.assertTrue(self.projection["manifest"]["sources"]["preferred_content"]["accepted"])
        self.assertFalse(self.projection["manifest"]["sources"]["fallback_selection"]["accepted"])
        self.assertEqual(
            self.projection["manifest"]["sources"]["preferred_content"]["revision"],
            "e29f56ce70bd997171a78a9f0facc9354797f421",
        )
        self.assertEqual(
            self.projection["manifest"]["sources"]["preferred_content"]["editorial_overlay_sha256"],
            "63969508134e8b2ef3c8471e9c8dbccc96842fcfc25225fe02e1ed5a4f5926f6",
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
            "events",
            "wiki",
            "courses",
            "media",
        ):
            for record in self.projection[collection]:
                self.assertRegex(record["provenance"]["checksum"], r"^[0-9a-f]{64}$")
                self.assertTrue(record["provenance"]["source_path"])
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
                with self.subTest(collection=collection, slug=record["slug"]):
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
        for event in self.projection["events"]:
            self.assertEqual(
                event["provenance"]["repository"],
                "DataTalksClub/datatalksclub.github.io",
            )
            self.assertEqual(event["provenance"]["revision"], legacy_revision)
            self.assertEqual(event["provenance"]["source_path"], "_data/events.yaml")

    def test_hubs_render_every_checked_record(self) -> None:
        for path, collection in (
            ("/blog", "articles"),
            ("/books", "books"),
            ("/courses", "courses"),
            ("/wiki", "wiki"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                for record in self.projection[collection]:
                    self.assertIn(f'href="{record["public_path"]}"', body)

        events = self.client.get("/events").content.decode()
        for event in self.projection["events"]:
            self.assertIn(f'href="{event["public_path"]}"', events)

    def test_every_selected_detail_is_safe_and_canonical(self) -> None:
        for collection in ("articles", "podcasts", "books", "people", "events", "wiki", "courses"):
            for record in self.projection[collection]:
                with self.subTest(collection=collection, slug=record["slug"]):
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
            list(self.projection["events"]),
        )
        podcasts = self.projection["podcasts_by_slug"]
        events = self.projection["events_by_slug"]
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
        self.assertIn(
            {
                "role": "guest",
                "label": (
                    "Early-Stage Investing in Open Source Developer Tools: Deal Sourcing, Due "
                    "Diligence & Commercialization Models"
                ),
                "public_path": "/podcast/investing-in-open-source-developer-tools.html",
            },
            bela_relationships,
        )
        self.assertNotIn(
            {
                "role": "speaker",
                "label": "Investing in Open-Source Data Tools",
                "public_path": "/events/2023-07-11-investing-in-open-source-data-tools",
            },
            bela_relationships,
        )
        self.assertIn("2023-07-11-investing-in-open-source-data-tools", events)

    def test_event_boundaries_are_timezone_aware(self) -> None:
        before = event_groups(datetime.fromisoformat("2026-08-30T12:00:00+02:00"))
        after = event_groups(datetime.fromisoformat("2026-09-01T12:00:00+02:00"))
        self.assertTrue(all(event["starts_at_value"].tzinfo for event in before.upcoming))
        self.assertEqual(
            before.upcoming[-1]["slug"],
            "2026-08-31-ai-dev-tools-zoomcamp-2026-course-launch",
        )
        self.assertFalse(after.upcoming)

    def test_wiki_fragments_and_corpus_targets_resolve(self) -> None:
        targets = set()
        distinct_ids = set()
        for document in self.projection["wiki_search"]["docs"]:
            parsed = urlsplit(document["url"])
            if parsed.fragment:
                targets.add((parsed.path, parsed.fragment))
                distinct_ids.add(parsed.fragment)
        self.assertEqual(len(targets), 1_974)
        self.assertEqual(len(distinct_ids), 1_894)
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
        self.assertEqual(len(person_relations), 501)
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
        self.assertEqual(len(graph["nodes"]), 1_072)
        self.assertEqual(len(graph["links"]), 13_006)
        self.assertEqual(len(search["docs"]), 2_998)
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

    def test_media_routes_are_local_and_checked(self) -> None:
        for record in self.projection["media"]:
            with self.subTest(path=record["public_path"]):
                response = self.client.get(record["public_path"])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Content-Type"], record["content_type"])
        self.assertEqual(self.client.get("/images/../../manage.py").status_code, 404)

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
        Course.objects.create(
            title=record["title"],
            slug=record["slug"],
            description="Database-backed course marker",
            visible=True,
        )
        response = self.client.get(record["public_path"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Database-backed course marker")

    def test_existing_cmp_course_rows_preserve_their_list_view(self) -> None:
        Course.objects.create(
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
