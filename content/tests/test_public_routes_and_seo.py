from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

from django.conf import settings
from django.test import TestCase

from content.docs_projection import docs_page
from content.podcast_routes import (
    PODCAST_AI_PRODUCTION_PATH,
    PODCAST_GENAI_PILOTS_PATH,
    PODCAST_HIERARCHICAL_ONLY_SLUGS,
    PODCAST_ROUTE_MIGRATION_PATH,
    podcast_legacy_path,
)
from content.public_data import public_paths, public_projection
from content.sitemap_contract import EXPECTED_SITEMAP_LOCATIONS

from .pagination_support import catalogue_body

SITEMAP_NAMESPACE = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class PublicRouteAndSeoTests(TestCase):
    maxDiff = None

    def test_removed_podcast_records_are_absent_from_active_projections(self) -> None:
        removed_slugs = {
            "_s12e08",
            "_theme-park-crowd-modeling-to-tesla-full-stack-data-engineering",
        }
        removed_aliases = {slug.removeprefix("_") for slug in removed_slugs}
        projection = public_projection()

        self.assertTrue(
            removed_slugs.isdisjoint({episode["slug"] for episode in projection["podcasts"]})
        )
        migration = projection["editorial_route_migration"]
        route_records = migration["finals"] + migration["aliases"]
        self.assertFalse(
            any(
                record["collection"] == "podcasts"
                and any(
                    alias in str(record.get(field, ""))
                    for alias in removed_aliases
                    for field in ("record_key", "final_path", "source_path")
                )
                for record in route_records
            )
        )

        graph = projection["wiki_graph"]
        removed_node_ids = {f"podcast:{alias}" for alias in removed_aliases}
        self.assertFalse(removed_node_ids.intersection({node["id"] for node in graph["nodes"]}))
        self.assertFalse(
            any(
                link["source"] in removed_node_ids or link["target"] in removed_node_ids
                for link in graph["links"]
            )
        )

        search_documents = projection["wiki_search"]["docs"]
        self.assertFalse(
            any(
                alias in str(document.get(field, ""))
                for document in search_documents
                for alias in removed_aliases
                for field in ("episode_slug", "graph_id", "url", "related_terms")
            )
        )

    def test_docs_and_faq_roots_preserve_trailing_slash_contract(self) -> None:
        roots = (("/docs/", "/docs"), ("/faq/", "/faq"))
        query = "utm_source=oncall%2Btest&x=a%2Fb&blank="
        canonical_inventory = set(public_paths())

        for final_path, alias_path in roots:
            with self.subTest(final_path=final_path):
                self.assertIn(final_path, canonical_inventory)
                self.assertNotIn(alias_path, canonical_inventory)

                final = self.client.get(f"{final_path}?{query}", follow=False)
                self.assertEqual(final.status_code, 200)
                self.assertNotIn("Location", final.headers)
                self.assertEqual(final.headers["X-Robots-Tag"], "noindex, nofollow")
                self.assertContains(
                    final,
                    f'<link rel="canonical" href="https://datatalks.club{final_path}">',
                    count=1,
                )
                self.assertContains(
                    final,
                    f'<meta property="og:url" content="https://datatalks.club{final_path}">',
                    count=1,
                )
                self.assertNotRegex(
                    final.content.decode(), rf'href="https://datatalks.club{alias_path}(?:"|[?#])'
                )
                self.assertNotRegex(final.content.decode(), rf'href="{alias_path}(?:"|[?#])')
                self.assertEqual(self.client.head(final_path).status_code, 200)
                self.assertEqual(self.client.post(final_path).status_code, 405)

                alias = self.client.get(f"{alias_path}?{query}", follow=False)
                self.assertEqual(alias.status_code, 301)
                self.assertEqual(alias.headers["Location"], f"{final_path}?{query}")
                self.assertEqual(alias.headers["X-Robots-Tag"], "noindex, nofollow")
                head = self.client.head(f"{alias_path}?{query}", follow=False)
                self.assertEqual(head.status_code, 301)
                self.assertEqual(head.headers["Location"], f"{final_path}?{query}")
                self.assertEqual(self.client.post(alias_path).status_code, 405)

        faq_detail = self.client.get("/faq/ai-dev-tools-zoomcamp.html")
        match = re.search(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            faq_detail.content.decode(),
            re.DOTALL,
        )
        if match is None:
            self.fail("FAQ detail does not emit JSON-LD")
        graph = json.loads(match.group(1))["@graph"]
        breadcrumb = next(item for item in graph if item["@type"] == "BreadcrumbList")
        self.assertEqual(breadcrumb["itemListElement"][1]["item"], "https://datatalks.club/faq/")

        for section, root in (("docs", "/docs/"), ("faq", "/faq/")):
            with self.subTest(section=section):
                response = self.client.get(f"/sitemaps/{section}.xml")
                self.assertEqual(response.status_code, 200)
                document = ElementTree.fromstring(response.content)
                locations = [
                    node.text or "" for node in document.findall("s:url/s:loc", SITEMAP_NAMESPACE)
                ]
                self.assertEqual(locations.count(f"https://datatalks.club{root}"), 1)
                self.assertNotIn(f"https://datatalks.club{root.rstrip('/')}", locations)

    def test_explicit_hub_redirects_are_permanent_one_hop_and_query_preserving(self) -> None:
        redirects = {
            "/articles.html": "/blog",
            "/blog/": "/blog",
            "/podcast.html": "/podcast",
            "/podcast/": "/podcast",
            "/books.html": "/books",
            "/books/": "/books",
            "/events.html": "/events",
            "/events/": "/events",
            "/courses/": "/courses",
            "/wiki/": "/wiki",
        }
        for source, target in redirects.items():
            with self.subTest(source=source):
                query = "x=%2F&x=&q=A+B&q=A%20B"
                response = self.client.get(f"{source}?{query}", follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], f"{target}?{query}")
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                head = self.client.head(f"{source}?{query}", follow=False)
                self.assertEqual(head.status_code, 301)
                self.assertEqual(head.headers["Location"], f"{target}?{query}")
                self.assertEqual(self.client.post(source).status_code, 405)
                final = self.client.get(target, follow=False)
                self.assertEqual(final.status_code, 200)
                self.assertNotIn("Location", final.headers)

    def test_podwiki_and_unknown_paths_are_unredirected_404s(self) -> None:
        for path in (
            "/podwiki",
            "/podwiki/",
            "/podwiki/wiki/a-a-testing/?q=1",
            "/podwiki/%77iki/a-a-testing/",
            "/not-a-public-route",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)

    def test_people_catalogue_paths_are_unavailable_without_redirects_or_canonicals(self) -> None:
        for path in ("/people", "/people/", "/people.html"):
            with self.subTest(path=path):
                for method in (self.client.get, self.client.head):
                    response = method(path, follow=False)
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn("Location", response.headers)
                    self.assertNotContains(response, 'rel="canonical"', status_code=404)
        canonical_inventory = set(public_paths())
        self.assertNotIn("/people", canonical_inventory)
        self.assertTrue(set(public_projection()["people_by_path"]).issubset(canonical_inventory))

    def test_projected_courses_do_not_claim_database_backed_course_routes(self) -> None:
        projected_paths = {record["public_path"] for record in public_projection()["courses"]}
        self.assertTrue(projected_paths)
        self.assertTrue(projected_paths.isdisjoint(public_paths()))
        for path in projected_paths:
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)
                self.assertNotContains(response, 'rel="canonical"', status_code=404)

    def test_editorial_detail_aliases_redirect_directly_to_canonicals(self) -> None:
        projection = public_projection()
        migration = projection["editorial_route_migration"]
        canonical_paths = {item["final_path"] for item in migration["finals"]}
        alias_map = {item["source_path"]: item["final_path"] for item in migration["aliases"]}
        self.assertEqual(
            set(alias_map.values()),
            canonical_paths
            - {
                item["final_path"]
                for item in migration["finals"]
                if item["collection"] == "podcasts"
                and item["record_key"] in PODCAST_HIERARCHICAL_ONLY_SLUGS
            },
        )
        self.assertTrue(set(alias_map).isdisjoint(canonical_paths))
        self.assertEqual(
            {path for path in canonical_paths if not path.endswith(".html")},
            {
                PODCAST_GENAI_PILOTS_PATH,
                PODCAST_ROUTE_MIGRATION_PATH,
                PODCAST_AI_PRODUCTION_PATH,
            },
        )
        self.assertEqual(
            set(alias_map),
            {
                alias
                for item in migration["finals"]
                for alias in (
                    (
                        podcast_legacy_path(item["record_key"]).removesuffix(".html")
                        if item["collection"] == "podcasts"
                        else item["final_path"].removesuffix(".html")
                    ),
                    (
                        podcast_legacy_path(item["record_key"]).removesuffix(".html")
                        if item["collection"] == "podcasts"
                        else item["final_path"].removesuffix(".html")
                    )
                    + "/",
                )
                if not (
                    item["collection"] == "podcasts"
                    and item["record_key"] in PODCAST_HIERARCHICAL_ONLY_SLUGS
                )
            },
        )

        query = "x=%2F&x=&q=A+B&q=A%20B"
        for source, target in alias_map.items():
            with self.subTest(source=source):
                response = self.client.get(f"{source}?{query}", follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], f"{target}?{query}")
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                head = self.client.head(f"{source}?{query}", follow=False)
                self.assertEqual(head.status_code, 301)
                self.assertEqual(head.headers["Location"], f"{target}?{query}")
                self.assertEqual(self.client.post(source).status_code, 405)

        for target in canonical_paths:
            with self.subTest(target=target):
                final = self.client.get(target, follow=False)
                self.assertEqual(final.status_code, 200)
                self.assertNotIn("Location", final.headers)
                self.assertEqual(final.headers["X-Robots-Tag"], "noindex, nofollow")
                self.assertEqual(self.client.post(target).status_code, 405)
                head = self.client.head(target, follow=False)
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.content, b"")
                self.assertNotIn("Location", head.headers)
                canonical_url = f"https://datatalks.club{target}"
                self.assertContains(
                    final,
                    f'<link rel="canonical" href="{canonical_url}">',
                    count=1,
                )
                self.assertContains(
                    final,
                    f'<meta property="og:url" content="{canonical_url}">',
                    count=1,
                )
                match = re.search(
                    r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
                    final.content.decode(),
                    re.DOTALL,
                )
                if match is None:
                    self.fail(f"{target} does not emit JSON-LD")
                graph = json.loads(match.group(1))["@graph"]
                self.assertEqual(graph[0]["url"], canonical_url)
                breadcrumb = next(item for item in graph if item["@type"] == "BreadcrumbList")
                self.assertEqual(breadcrumb["itemListElement"][-1]["item"], canonical_url)

        guide_path = "/blog/guide-to-free-online-courses-at-datatalks-club.html"
        guide = self.client.get(guide_path, follow=False)
        self.assertEqual(guide.status_code, 200)
        self.assertContains(
            guide,
            f'<link rel="canonical" href="https://datatalks.club{guide_path}">',
            count=1,
        )
        for alias in (guide_path.removesuffix(".html"), f"{guide_path.removesuffix('.html')}/"):
            response = self.client.get(f"{alias}?source=contract", follow=False)
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response.headers["Location"], f"{guide_path}?source=contract")

        for collection in ("blog", "podcast", "books", "people"):
            for source in (
                f"/{collection}/missing-record",
                f"/{collection}/missing-record.html",
                f"/{collection}/missing-record/",
            ):
                with self.subTest(source=source):
                    response = self.client.get(source, follow=False)
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn("Location", response.headers)

    def test_all_events_have_internal_details_and_resolved_people(self) -> None:
        projection = public_projection()
        home = self.client.get("/").content.decode()
        hub = self.client.get("/events").content.decode()
        archive = catalogue_body(self.client, "/events/past")
        for body in (home, hub):
            self.assertNotRegex(body, r'href="https://(?:luma\.com|lu\.ma)')
            self.assertNotIn(" · workshop", body.casefold())
        self.assertNotRegex(archive, r'href="https://(?:luma\.com|lu\.ma)')
        self.assertNotIn(" · workshop", archive.casefold())
        people_paths = {person["public_path"] for person in projection["people"]}
        for event in projection["events"]:
            with self.subTest(event=event["slug"]):
                self.assertIn(f'href="{event["public_path"]}"', hub + archive)
                response = self.client.get(event["public_path"])
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                for speaker in event["speakers"]:
                    self.assertIn(speaker["public_path"], people_paths)
                    self.assertIn(f'href="{speaker["public_path"]}"', body)
                for link in event["links"]:
                    self.assertIn(f'href="{link["url"]}"', body)
                    self.assertIn("opens in a new tab", body)

    def test_all_podcast_guest_links_resolve_to_person_details(self) -> None:
        projection = public_projection()
        guest_profiles = [
            guest for podcast in projection["podcasts"] for guest in podcast["guest_profiles"]
        ]
        unresolved_guests = [guest for guest in guest_profiles if not guest["public_path"]]
        self.assertEqual(unresolved_guests, [])
        for podcast in projection["podcasts"]:
            response = self.client.get(podcast["public_path"])
            self.assertEqual(response.status_code, 200)
            body = response.content.decode()
            for guest in podcast["guest_profiles"]:
                if guest["public_path"]:
                    self.assertIn(f'href="{guest["public_path"]}"', body)
                    self.assertEqual(self.client.get(guest["public_path"]).status_code, 200)
                else:
                    self.assertNotIn(f'href="{guest["public_path"]}"', body)

    def test_all_person_details_remain_available_without_a_people_catalogue(self) -> None:
        projection = public_projection()
        self.assertNotIn("_template", projection["people_by_slug"])
        for person in projection["people"]:
            response = self.client.get(person["public_path"])
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'alt="Portrait of ', count=1)
            self.assertNotContains(response, 'href="/people"')

    def test_article_and_person_body_attributes_are_removed_without_copy_mutation(self) -> None:
        projection_root = Path(settings.BASE_DIR) / "content" / "public_projection"
        before = {
            path.relative_to(projection_root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(projection_root.rglob("*"))
            if path.is_file()
        }

        projection = public_projection()
        article = projection["articles_by_slug"]["ai-dev-tools-zoomcamp"]
        person = projection["people_by_slug"]["agnieszkamikolajczyk"]
        self.assertTrue(
            any("AI-Native Development" in block.get("text", "") for block in article["blocks"])
        )
        self.assertTrue(any("Omdena" in block.get("text", "") for block in person["blocks"]))
        for record in (*projection["articles"], *projection["people"]):
            body = " ".join(
                str(block.get(name, ""))
                for block in record["blocks"]
                for name in ("text", "markdown")
            )
            self.assertNotRegex(body, r"\{:[ \t]*target[ \t]*=")

        raw_article = json.loads((projection_root / "articles.json").read_text(encoding="utf-8"))
        raw_person = json.loads((projection_root / "people.json").read_text(encoding="utf-8"))
        # Article bodies are projected by the article block builder, which removes the legacy
        # directive at build time, so the checked file carries none of them.  Person bios still
        # take the older plain-text path, and their markers are still removed at runtime above.
        self.assertFalse(
            any(
                "{:target=" in str(block.get(name, ""))
                for record in raw_article
                for block in record.get("blocks", [])
                for name in ("text", "markdown")
            )
        )
        self.assertTrue(
            any(
                '{:target="blank"}' in block.get("text", "")
                for record in raw_person
                for block in record.get("blocks", [])
            )
        )

        after = {
            path.relative_to(projection_root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(projection_root.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_affected_article_person_and_control_bodies_render_cleanly(self) -> None:
        # The article copy is a link label.  Its trailing full stop belongs to the sentence,
        # not to the link, so it now sits outside the anchor the restored body draws.
        affected = (
            (
                "/blog/ai-dev-tools-zoomcamp.html",
                "AI-Native Development: Specifications, Loop Engineering, and Graph Engineering",
            ),
            ("/people/agnieszkamikolajczyk.html", "Omdena - AI for Good"),
        )
        for path, expected_copy in affected:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertIn(expected_copy, body)
                self.assertNotRegex(body, r"\{:[ \t]*target[ \t]*=")

        control = self.client.get("/blog/sponsor-datatalks-club.html")
        self.assertEqual(control.status_code, 200)
        self.assertNotRegex(control.content.decode(), r"\{:[ \t]*target[ \t]*=")

    def test_representative_details_emit_valid_type_specific_json_ld(self) -> None:
        paths_and_types = (
            (public_projection()["articles"][0]["public_path"], "BlogPosting"),
            (public_projection()["podcasts"][0]["public_path"], "PodcastEpisode"),
            (public_projection()["books"][0]["public_path"], "Book"),
            (public_projection()["people"][0]["public_path"], "Person"),
            (public_projection()["events"][0]["public_path"], "Event"),
            (public_projection()["wiki"][0]["public_path"], "Article"),
        )
        for path, expected_type in paths_and_types:
            with self.subTest(path=path):
                response = self.client.get(path)
                body = response.content.decode()
                match = re.search(
                    r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
                    body,
                    re.DOTALL,
                )
                if match is None:
                    self.fail(f"{path} does not emit JSON-LD")
                payload = json.loads(match.group(1))
                self.assertIn(expected_type, {item.get("@type") for item in payload["@graph"]})
                self.assertIn("BreadcrumbList", {item.get("@type") for item in payload["@graph"]})
                self.assertContains(
                    response,
                    f'<link rel="canonical" href="https://datatalks.club{path}">',
                    count=1,
                )
                self.assertContains(response, 'property="og:url"')
                self.assertContains(response, 'name="twitter:title"')

    def test_review_backed_public_details_do_not_render_source_provenance(self) -> None:
        """A public page says what it is, never where it was built from."""

        blocked = {
            "Checked source",
            "View source on GitHub",
            "This page is maintained on",
        }
        for path in (
            "/faq/ai-dev-tools-zoomcamp.html",
            "/slack",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                for value in blocked:
                    self.assertNotIn(value, body)

    def test_docs_details_do_not_expose_repository_utility_actions(self) -> None:
        page = docs_page("/docs/courses/ai-dev-tools-zoomcamp/getting-started/")
        self.assertIsNotNone(page)
        assert page is not None

        response = self.client.get(page["public_path"])

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'href="{page["edit_url"]}"')
        self.assertNotContains(response, "Search documentation on GitHub")
        self.assertNotContains(response, "Checked source")
        self.assertNotContains(response, "View source on GitHub")
        self.assertNotContains(response, "This page is maintained on")

    def test_every_section_sitemap_entry_is_a_unique_canonical_public_200(self) -> None:
        root = self.client.get("/sitemap.xml")
        self.assertEqual(root.status_code, 200)
        index = ElementTree.fromstring(root.content)
        sitemap_locations: list[str] = [
            node.text or "" for node in index.findall("s:sitemap/s:loc", SITEMAP_NAMESPACE)
        ]
        self.assertEqual(
            sitemap_locations,
            list(EXPECTED_SITEMAP_LOCATIONS),
        )
        seen: set[str] = set()
        for location in sitemap_locations:
            section_path = urlsplit(location).path
            section = self.client.get(section_path)
            self.assertEqual(section.status_code, 200)
            document = ElementTree.fromstring(section.content)
            for url_node in document.findall("s:url", SITEMAP_NAMESPACE):
                public_url = url_node.findtext("s:loc", namespaces=SITEMAP_NAMESPACE)
                self.assertIsNotNone(public_url)
                parsed = urlsplit(public_url or "")
                self.assertEqual((parsed.scheme, parsed.netloc), ("https", "datatalks.club"))
                self.assertFalse(parsed.query or parsed.fragment)
                self.assertNotIn("/podwiki", parsed.path)
                self.assertNotIn(parsed.path, seen)
                seen.add(parsed.path)
                response = self.client.get(parsed.path, follow=False)
                self.assertEqual(response.status_code, 200, parsed.path)
                self.assertNotIn("Location", response.headers)
                self.assertContains(
                    response,
                    f'<link rel="canonical" href="{public_url}">',
                    count=1,
                )
        expected = {path for path in public_projection()["events_by_path"]}
        expected.update(path for path in public_projection()["people_by_path"])
        self.assertTrue(expected.issubset(seen))
        self.assertNotIn("/people", seen)
