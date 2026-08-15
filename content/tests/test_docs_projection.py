from __future__ import annotations

import hashlib
import json
import re

from django.test import TestCase

from content.docs_projection import (
    DOCS_PROJECTION_PATH,
    DOCS_ROOT_PATH,
    DOCS_SOURCE_REVISION,
    _prepare_markdown,
    docs_asset_path,
    docs_breadcrumbs,
    docs_page,
    docs_projection,
    render_docs_markdown,
)


class DocsProjectionTests(TestCase):
    def test_projection_is_complete_and_pinned(self) -> None:
        projection = docs_projection()
        self.assertEqual(projection["source"]["revision"], DOCS_SOURCE_REVISION)
        self.assertEqual(projection["root_path"], DOCS_ROOT_PATH)
        self.assertEqual(len(projection["pages"]), 106)
        self.assertEqual(len(projection["assets"]), 39)
        self.assertEqual(
            len({page["public_path"] for page in projection["pages"]}),
            len(projection["pages"]),
        )
        page = docs_page("/docs/courses/ai-dev-tools-zoomcamp/getting-started/")
        if page is None:
            self.fail("getting-started projection page is missing")
        self.assertEqual(page["parent_path"], "/docs/courses/ai-dev-tools-zoomcamp/")

    def test_docs_home_and_detail_emit_source_content_and_canonicals(self) -> None:
        home = self.client.get("/docs/")
        self.assertEqual(home.status_code, 200)
        self.assertNotIn("Location", home.headers)
        self.assertContains(home, '<link rel="canonical" href="https://datatalks.club/docs/">')
        self.assertContains(home, '<meta property="og:url" content="https://datatalks.club/docs/">')
        self.assertContains(home, "DataTalks.Club Zoomcamps Notes and Resources")
        self.assertContains(home, "/docs/courses/")

        detail_path = "/docs/courses/ai-dev-tools-zoomcamp/getting-started/"
        detail = self.client.get(detail_path)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(
            detail,
            f'<link rel="canonical" href="https://datatalks.club{detail_path}">',
        )
        self.assertContains(detail, "For the cross-course onboarding")
        self.assertContains(detail, 'id="getting-started"')
        self.assertContains(detail, 'id="star-the-github-repository"')
        self.assertContains(detail, 'href="/docs/courses/zoomcamp-logistics/joining/"')
        self.assertNotContains(detail, "3f23e006ffdaa498bbc69697408853b6f5eb37dc")
        self.assertNotContains(detail, "DataTalksClub/docs")

    def test_every_projected_page_is_a_trailing_slash_public_page(self) -> None:
        for page in docs_projection()["pages"]:
            public_path = page["public_path"]
            with self.subTest(public_path=public_path):
                response = self.client.get(public_path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    f'<link rel="canonical" href="https://datatalks.club{public_path}">',
                )

    def test_navigation_breadcrumbs_follow_source_hierarchy(self) -> None:
        page = docs_page("/docs/general/guidelines/ai-usage/")
        self.assertIsNotNone(page)
        breadcrumbs = docs_breadcrumbs(page or {})
        self.assertEqual(
            [(item["title"], item["public_path"]) for item in breadcrumbs],
            [
                ("Documentation", "/docs/"),
                ("General", "/docs/general/"),
                ("Community Guidelines", "/docs/general/guidelines/"),
            ],
        )

    def test_markdown_renderer_rewrites_liquid_and_sanitizes_html(self) -> None:
        html, headings = render_docs_markdown(
            {
                "body": (
                    "# One heading\n\n"
                    "## One heading\n\n"
                    "[guide]({{ '/courses/ml-zoomcamp/' | relative_url }})\n\n"
                    "<script>alert('no')</script><a href=\"javascript:alert(1)\">bad</a>"
                )
            }
        )
        self.assertEqual([item["id"] for item in headings], ["one-heading", "one-heading-1"])
        self.assertIn('href="/docs/courses/ml-zoomcamp/"', html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("javascript:", html.lower())
        self.assertNotRegex(html, re.compile(r"on\w+\s*=", re.IGNORECASE))

    def test_render_time_link_allowlist_preserves_fragments_and_external_links(self) -> None:
        source = (
            "[events](/events.html?utm_source=docs#upcoming) "
            "[events](https://datatalks.club/events.html?utm_source=docs#upcoming) "
            "[empty](/events.html?#) "
            "[podcast](/podcast.html#episodes) "
            "[podcast](https://datatalks.club/podcast.html?season=3#latest) "
            "[books](/books.html?sort=recent) "
            "[books](https://datatalks.club/books.html#archive) "
            "[slack](/slack.html?source=docs#join) "
            "[guidelines](https://datatalks.club/slack/guidelines.html#rules) "
            "[newsletter](/newsletter.html?utm_source=docs#weekly) "
            "[newsletter](https://datatalks.club/newsletter.html#weekly) "
            "[Luma](https://luma.com/dtc-events) "
            "[channel](https://app.slack.com/client/T01ATQK62F8/C0288NJ5XSA)"
        )
        prepared = _prepare_markdown(source)
        self.assertEqual(
            prepared,
            (
                "[events](/events?utm_source=docs#upcoming) "
                "[events](/events?utm_source=docs#upcoming) "
                "[empty](/events?#) "
                "[podcast](/podcast#episodes) "
                "[podcast](/podcast?season=3#latest) "
                "[books](/books?sort=recent) "
                "[books](/books#archive) "
                "[slack](/slack?source=docs#join) "
                "[guidelines](/slack#rules) "
                "newsletter newsletter "
                "[our events page](/events) "
                "[channel](https://app.slack.com/client/T01ATQK62F8/C0288NJ5XSA)"
            ),
        )
        self.assertNotIn("newsletter.html", prepared)
        self.assertNotIn("luma.com", prepared)

    def test_link_rewrites_do_not_touch_markdown_outside_intended_spans(self) -> None:
        source = (
            "Literal /events.html and https://datatalks.club/slack.html stay unchanged.\n\n"
            "`[events](/events.html)` and `[Luma](https://luma.com/dtc-events)` stay unchanged.\n\n"
            "[external](https://example.com/events.html) and "
            "[other host](https://www.datatalks.club/events.html) stay unchanged."
        )
        self.assertEqual(_prepare_markdown(source), source)

    def test_rendered_activity_pages_use_canonical_hubs_and_targeted_events_cta(self) -> None:
        expected_hubs = {
            "/docs/activities/": ("/events",),
            "/docs/activities/podcast/": ("/podcast", "/events"),
            "/docs/activities/webinars/": ("/events",),
            "/docs/activities/workshops/": ("/events",),
            "/docs/activities/book-of-the-week/": ("/books", "/slack"),
        }
        for public_path, destinations in expected_hubs.items():
            with self.subTest(public_path=public_path):
                page = docs_page(public_path)
                self.assertIsNotNone(page)
                rendered, _ = render_docs_markdown(page or {})
                for destination in destinations:
                    self.assertIn(f'href="{destination}', rendered)
                self.assertNotRegex(
                    rendered,
                    r'href="(?:https://datatalks\.club)?/(?:events|podcast|books)\.html',
                )
        activities, _ = render_docs_markdown(docs_page("/docs/activities/") or {})
        self.assertIn('href="/events"', activities)
        self.assertNotIn("luma.com", activities.lower())
        self.assertNotIn(">Luma<", activities)
        workshops, _ = render_docs_markdown(docs_page("/docs/activities/workshops/") or {})
        self.assertIn("via Luma", workshops)

    def test_all_affected_docs_pages_apply_slack_and_newsletter_exceptions(self) -> None:
        newsletter_pages = {
            "/docs/courses/llm-zoomcamp/resources/": "DataTalks.Club newsletter",
            "/docs/courses/ml-zoomcamp/resources/": "DataTalks.Club Newsletter",
            "/docs/courses/zoomcamp-logistics/email/": "DataTalks.Club newsletter",
        }
        for public_path, heading_copy in newsletter_pages.items():
            with self.subTest(public_path=public_path):
                page = docs_page(public_path)
                self.assertIsNotNone(page)
                rendered, _ = render_docs_markdown(page or {})
                self.assertIn(heading_copy, rendered)
                self.assertNotRegex(rendered, r'href="[^" ]*newsletter\.html')
        ml_resources, _ = render_docs_markdown(
            docs_page("/docs/courses/ml-zoomcamp/resources/") or {}
        )
        self.assertIn('href="https://us19.campaign-archive.com/home/', ml_resources)

        for page in docs_projection()["pages"]:
            if not re.search(r"/slack(?:/guidelines)?\.html", str(page["body"])):
                continue
            with self.subTest(public_path=page["public_path"]):
                rendered, _ = render_docs_markdown(page)
                self.assertNotRegex(
                    rendered,
                    r'href="(?:https://datatalks\.club)?/slack(?:/guidelines)?\.html',
                )
                self.assertIn('href="/slack', rendered)

    def test_rendering_keeps_projection_source_and_metadata_immutable(self) -> None:
        before_bytes = DOCS_PROJECTION_PATH.read_bytes()
        before_projection = json.loads(before_bytes)
        before_digest = hashlib.sha256(before_bytes).hexdigest()

        for page in docs_projection()["pages"]:
            render_docs_markdown(page)

        after_bytes = DOCS_PROJECTION_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(after_bytes).hexdigest(), before_digest)
        self.assertEqual(json.loads(after_bytes), before_projection)

    def test_referenced_assets_are_served_only_from_the_pinned_allowlist(self) -> None:
        asset = docs_projection()["assets"][0]
        public_path = asset["public_path"]
        response = self.client.get(public_path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], asset["content_type"])
        payload = b"".join(response.streaming_content)  # type: ignore[attr-defined]
        self.assertEqual(len(payload), asset["size"])
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=86400")
        resolved = docs_asset_path(public_path.removeprefix("/docs/assets/"))
        if resolved is None:
            self.fail("projected asset did not resolve")
        self.assertEqual(resolved[1], asset["content_type"])

        for path in (
            "/docs/assets/images/not-referenced.png",
            "/docs/assets/../docs_projection.json",
            "/docs/assets/images/brand-assets/../../../../content/docs_projection.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
