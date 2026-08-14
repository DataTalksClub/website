from __future__ import annotations

import re

from django.test import TestCase

from content.docs_projection import (
    DOCS_ROOT_PATH,
    DOCS_SOURCE_REVISION,
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
