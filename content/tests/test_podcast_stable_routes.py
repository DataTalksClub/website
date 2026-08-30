from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from content.podcast_routes import PODCAST_AI_PRODUCTION_PATH, podcast_legacy_path
from content.public_data import public_projection


class PodcastStableRouteTests(TestCase):
    slug = "s24e06-how-to-build-ai-that-actually-ships-in-production"

    def test_canonical_stable_id_route_renders_the_episode(self) -> None:
        response = self.client.get(PODCAST_AI_PRODUCTION_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(reverse("podcast-ai-production"), PODCAST_AI_PRODUCTION_PATH)
        self.assertEqual(
            public_projection()["podcasts_by_slug"][self.slug]["public_path"],
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
