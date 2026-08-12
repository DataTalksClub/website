from __future__ import annotations

import json
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

from django.test import TestCase

from content.public_data import public_paths, public_projection
from content.review_projection import review_projection
from content.sitemap_contract import EXPECTED_SITEMAP_LOCATIONS

SITEMAP_NAMESPACE = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class PublicRouteAndSeoTests(TestCase):
    maxDiff = None

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

    def test_editorial_detail_aliases_redirect_directly_to_html_canonicals(self) -> None:
        projection = public_projection()
        migration = projection["editorial_route_migration"]
        self.assertEqual(migration["counts"], {"finals": 796, "aliases": 1_592})
        self.assertEqual(len(migration["finals"]), 796)
        self.assertEqual(len(migration["aliases"]), 1_592)
        canonical_paths = {item["final_path"] for item in migration["finals"]}
        alias_map = {item["source_path"]: item["final_path"] for item in migration["aliases"]}
        runtime_canonical_paths = set()
        for item in migration["finals"]:
            runtime_path = item["final_path"]
            if item["collection"] == "podcasts":
                podcast = next(
                    record
                    for record in projection["podcasts"]
                    if record["provenance"]["source_key"] == item["record_key"]
                )
                runtime_path = podcast["public_path"]
            runtime_canonical_paths.add(runtime_path)
        self.assertEqual(len(alias_map), 1_592)
        self.assertEqual(set(alias_map.values()), canonical_paths)
        self.assertTrue(set(alias_map).isdisjoint(canonical_paths))
        self.assertTrue(all(path.endswith(".html") for path in canonical_paths))
        self.assertEqual(
            set(alias_map),
            {
                alias
                for path in canonical_paths
                for alias in (path.removesuffix(".html"), f"{path.removesuffix('.html')}/")
            },
        )

        query = "x=%2F&x=&q=A+B&q=A%20B"
        for source, target in alias_map.items():
            with self.subTest(source=source):
                response = self.client.get(f"{source}?{query}", follow=False)
                self.assertEqual(response.status_code, 301)
                migration_entry = next(
                    item for item in migration["aliases"] if item["source_path"] == source
                )
                expected_target = target
                if migration_entry["collection"] == "podcasts":
                    podcast = next(
                        record
                        for record in projection["podcasts"]
                        if record["provenance"]["source_key"] == migration_entry["record_key"]
                    )
                    expected_target = podcast["public_path"]
                self.assertEqual(response.headers["Location"], f"{expected_target}?{query}")
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                head = self.client.head(f"{source}?{query}", follow=False)
                self.assertEqual(head.status_code, 301)
                self.assertEqual(head.headers["Location"], f"{expected_target}?{query}")
                self.assertEqual(self.client.post(source).status_code, 405)

        for target in runtime_canonical_paths:
            with self.subTest(target=target):
                final = self.client.get(target, follow=False)
                self.assertEqual(final.status_code, 200)
                self.assertNotIn("Location", final.headers)
                self.assertEqual(final.headers["X-Robots-Tag"], "noindex, nofollow")
                self.assertEqual(self.client.post(target).status_code, 405)
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
        archive = self.client.get("/events/past").content.decode()
        page_match = re.search(r"Page 1 of (\d+)", archive)
        if page_match is not None:
            archive_pages = [archive]
            for page in range(2, int(page_match.group(1)) + 1):
                response = self.client.get(f"/events/past?page={page}")
                self.assertEqual(response.status_code, 200)
                archive_pages.append(response.content.decode())
            archive = "".join(archive_pages)
        for body in (home, hub):
            self.assertNotRegex(body, r'href="https://(?:luma\.com|lu\.ma)')
            self.assertNotIn(" · workshop", body.casefold())
        self.assertNotRegex(archive, r'href="https://(?:luma\.com|lu\.ma)')
        self.assertNotIn(" · workshop", archive.casefold())
        people_paths = {person["public_path"] for person in projection["people"]}
        self.assertEqual(len(projection["events"]), 421)
        self.assertEqual(sum(len(event["speakers"]) for event in projection["events"]), 456)
        self.assertEqual(
            len(
                {
                    speaker["public_path"]
                    for event in projection["events"]
                    for speaker in event["speakers"]
                }
            ),
            322,
        )
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
        linked_guests = [guest for guest in guest_profiles if guest["public_path"]]
        unresolved_guests = [guest for guest in guest_profiles if not guest["public_path"]]
        self.assertEqual(len(guest_profiles), 208)
        self.assertEqual(len(linked_guests), 207)
        self.assertEqual(
            unresolved_guests,
            [{"key": "abouzarabbaspour", "name": "abouzarabbaspour", "public_path": ""}],
        )
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
        self.assertEqual(len(projection["people"]), 438)
        self.assertNotIn("_template", projection["people_by_slug"])
        for person in projection["people"]:
            response = self.client.get(person["public_path"])
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'alt="Portrait of ', count=1)
            self.assertNotContains(response, 'href="/people"')

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
        projection = review_projection()
        blocked = {
            "Checked source",
            "View source on GitHub",
            "This page is maintained on",
            *(
                source["repository"].removeprefix("https://github.com/")
                for source in projection["sources"].values()
            ),
            *(source["revision"] for source in projection["sources"].values()),
        }
        for path in (
            "/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
            "/faq/ai-dev-tools-zoomcamp.html",
            "/slack",
            "/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                for value in blocked:
                    self.assertNotIn(value, body)

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
