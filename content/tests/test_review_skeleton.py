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
            "b9a40ba974fdef67ee3a2a70f114734f2581033c",
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

    def test_hubs_render_every_checked_record(self) -> None:
        for path, collection in (
            ("/blog", "articles"),
            ("/podcast", "podcasts"),
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

        people_pages = "".join(
            self.client.get("/people", {"page": page}).content.decode() for page in range(1, 11)
        )
        for record in self.projection["people"]:
            self.assertIn(f'href="{record["public_path"]}"', people_pages)

        events = self.client.get("/events").content.decode()
        for event in self.projection["events"]:
            self.assertIn(f'href="{event["public_path"]}"', events)

    def test_every_selected_detail_is_safe_and_canonical(self) -> None:
        for collection in ("articles", "podcasts", "books", "people", "events", "wiki", "courses"):
            for record in self.projection[collection]:
                with self.subTest(collection=collection, slug=record["slug"]):
                    response = self.client.get(record["public_path"])
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(escape(record["title"]), response.content.decode())
                    self.assertContains(
                        response,
                        f'<link rel="canonical" href="https://datatalks.club{record["public_path"]}">',
                    )
                    self.assertEqual(self.client.head(record["public_path"]).status_code, 200)
                    self.assertEqual(self.client.post(record["public_path"]).status_code, 405)

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
                response.close()
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
            "/people",
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
