from __future__ import annotations

from typing import Any

from django.test import TestCase
from django.urls import reverse

from content import catalogue
from content.podcast_routes import (
    PODCAST_AI_PRODUCTION_PATH,
    PODCAST_GENAI_PILOTS_PATH,
    PODCAST_GENAI_PILOTS_SLUG,
    podcast_legacy_path,
)
from content.public_data import public_projection
from content.wiki_content import episode_graph


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


class PodcastStableRouteTests(TestCase):
    slug = "s24e06-how-to-build-ai-that-actually-ships-in-production"

    def test_genai_pilots_hierarchical_route_is_the_only_public_detail_form(self) -> None:
        response = self.client.get(PODCAST_GENAI_PILOTS_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(reverse("podcast-genai-pilots"), PODCAST_GENAI_PILOTS_PATH)
        projection = public_projection()
        episode = _episode(PODCAST_GENAI_PILOTS_SLUG)
        self.assertEqual(episode["public_path"], PODCAST_GENAI_PILOTS_PATH)
        self.assertEqual(
            episode_graph(episode, projection=projection).url,
            PODCAST_GENAI_PILOTS_PATH,
        )
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{PODCAST_GENAI_PILOTS_PATH}">',
            count=1,
        )
        self.assertContains(
            response,
            f'<meta property="og:url" content="https://datatalks.club{PODCAST_GENAI_PILOTS_PATH}">',
            count=1,
        )

        legacy = podcast_legacy_path(PODCAST_GENAI_PILOTS_SLUG)
        clean_legacy = legacy.removesuffix(".html")
        flat_forms = (legacy, clean_legacy, f"{clean_legacy}/")
        for path in flat_forms:
            with self.subTest(path=path):
                for method in (self.client.get, self.client.head):
                    unavailable = method(path, follow=False)
                    self.assertEqual(unavailable.status_code, 404)
                    self.assertNotIn("Location", unavailable.headers)
                    self.assertNotContains(unavailable, 'rel="canonical"', status_code=404)

    def test_genai_pilots_stale_hierarchical_title_redirects_to_the_canonical_route(self) -> None:
        response = self.client.get("/podcast/s24e04/stale-title", follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], PODCAST_GENAI_PILOTS_PATH)

    def test_canonical_stable_id_route_renders_the_episode(self) -> None:
        response = self.client.get(PODCAST_AI_PRODUCTION_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(reverse("podcast-ai-production"), PODCAST_AI_PRODUCTION_PATH)
        self.assertEqual(
            _episode(self.slug)["public_path"],
            PODCAST_AI_PRODUCTION_PATH,
        )
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{PODCAST_AI_PRODUCTION_PATH}">',
            count=1,
        )

    def test_wrong_title_slug_redirects_permanently_to_current_slug(self) -> None:
        response = self.client.get("/podcast/s24e06/whatever", follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], PODCAST_AI_PRODUCTION_PATH)

    def test_legacy_html_path_redirects_permanently(self) -> None:
        legacy = podcast_legacy_path(self.slug)

        response = self.client.get(legacy, follow=False)

        self.assertEqual(reverse("podcast-ai-production-legacy"), legacy)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], PODCAST_AI_PRODUCTION_PATH)

    def test_redirects_preserve_the_raw_query_string(self) -> None:
        query = "utm_source=route%2Btest&x=a%2Fb&blank="

        for path in ("/podcast/s24e06/whatever", podcast_legacy_path(self.slug)):
            with self.subTest(path=path):
                response = self.client.get(f"{path}?{query}", follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(
                    response.headers["Location"],
                    f"{PODCAST_AI_PRODUCTION_PATH}?{query}",
                )

    def test_unknown_stable_id_is_not_found(self) -> None:
        response = self.client.get("/podcast/s99e99/whatever", follow=False)

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Location", response.headers)
