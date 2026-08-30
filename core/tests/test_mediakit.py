from __future__ import annotations

import re
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import resolve, reverse

from core import mediakit


class MediaKitPageTests(TestCase):
    def test_canonical_route_renders_the_recovered_media_kit(self) -> None:
        self.assertEqual(reverse("media-kit"), "/mediakit/")
        self.assertIs(resolve("/mediakit/").func, mediakit.media_kit)

        response = self.client.get("/mediakit/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DataTalks.Club Media Kit")
        self.assertContains(response, "120K+")
        self.assertContains(response, "Sponsorship Formats")
        self.assertContains(response, "Previous and Current Sponsors")
        self.assertContains(response, "alexey@datatalks.club")
        self.assertNotContains(response, "valeriia@datatalks.club")
        self.assertContains(response, "<h1>Media kit</h1>", html=True)
        self.assertNotContains(response, "core/mediakit/logo.svg")
        self.assertContains(response, 'class="media-kit shell-breakout"')
        self.assertTemplateUsed(response, "core/content_page.html")
        self.assertTemplateUsed(response, "core/mediakit.html")

    def test_slashless_legacy_route_redirects_permanently_to_canonical(self) -> None:
        response = self.client.get("/mediakit?from=legacy")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/mediakit/?from=legacy")

    @override_settings(NOINDEX=False)
    def test_share_by_link_page_stays_noindex_in_production(self) -> None:
        response = self.client.get("/mediakit/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, '<meta name="robots" content="noindex,nofollow">')
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/mediakit/">',
        )

    def test_media_kit_is_absent_from_public_discovery_surfaces(self) -> None:
        home = self.client.get("/").content.decode()
        root_sitemap = self.client.get("/sitemap.xml").content.decode()

        self.assertNotIn('href="/mediakit/', home)
        self.assertNotIn("https://datatalks.club/mediakit/", root_sitemap)

        for section in (
            "main",
            "blog",
            "books",
            "events",
            "people",
            "podcast",
            "wiki",
        ):
            with self.subTest(section=section):
                body = self.client.get(f"/sitemaps/{section}.xml").content.decode()
                self.assertNotIn("https://datatalks.club/mediakit/", body)

    def test_showcase_images_open_their_full_size_local_assets(self) -> None:
        response = self.client.get("/mediakit/")
        body = response.content.decode()
        image_links = re.findall(
            r'<a class="media-kit-image-link" href="([^"]+)"[^>]*>'
            r'<img[^>]+src="([^"]+)"',
            body,
        )

        self.assertContains(response, "/static/core/mediakit/newsletter-primary.png")
        self.assertContains(response, "/static/core/mediakit/workshop-community.png")
        self.assertContains(response, 'class="media-kit-image-link"', count=13)
        self.assertEqual(len(image_links), 13)
        for href, src in image_links:
            with self.subTest(src=src):
                self.assertEqual(href, src)
                self.assertTrue(src.startswith("/static/core/mediakit/"))

    def test_internal_links_are_root_relative_and_borders_use_neutral_tokens(self) -> None:
        response = self.client.get("/mediakit/")
        body = response.content.decode().split("<body", maxsplit=1)[1]
        template = Path("templates/core/mediakit.html").read_text()

        self.assertNotIn('href="https://datatalks.club/', body)
        self.assertContains(response, 'href="/podcast/building-production-search-systems.html"')
        self.assertContains(
            response,
            'href="/blog/open-source-free-ai-agent-evaluation-tools.html"',
        )
        self.assertIsNone(
            re.search(r"border(?:-color)?\s*:[^;]*(?:indigo|green|clay|gold)", template)
        )
        self.assertContains(response, 'target="_blank" rel="noopener noreferrer"')
