from __future__ import annotations

from typing import Any
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from content import catalogue
from content.podcast_routes import podcast_public_id
from content.wiki_content import episode_graph


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


def _episode_id(slug: str) -> str:
    record = _episode(slug)
    return podcast_public_id(season=record["season"], episode=record["episode"])


def _canonical_path(slug: str) -> str:
    """The canonical route for an episode, by its catalogue record."""

    record = _episode(slug)
    return record["public_path"]


class PodcastStableRouteTests(TestCase):
    slug = "s24e06-how-to-build-ai-that-actually-ships-in-production"

    def test_every_legacy_html_alias_redirects_once_preserving_raw_query(self) -> None:
        query = "utm_source=legacy%2Btest&x=a%2Fb&x=&q=A+B&q=A%20B"
        episodes = catalogue.podcasts()
        self.assertTrue(episodes)
        for episode in episodes:
            source = f"/podcast/{episode['slug']}.html"
            target = episode["public_path"]
            with self.subTest(source=source):
                for suffix in ("", f"?{query}"):
                    for method in (self.client.get, self.client.head):
                        response = method(f"{source}{suffix}", follow=False)
                        self.assertEqual(response.status_code, 301)
                        self.assertEqual(response["Location"], f"{target}{suffix}")
                        self.assertEqual(response["Cache-Control"], "public, max-age=300")
                    final = self.client.get(f"{source}{suffix}", follow=True)
                    self.assertEqual(final.status_code, 200)
                    self.assertEqual(final.redirect_chain, [(f"{target}{suffix}", 301)])

    def test_legacy_html_alias_method_and_sensitive_query_boundaries(self) -> None:
        source = f"/podcast/{self.slug}.html"
        for method in (self.client.post, self.client.put, self.client.patch, self.client.delete):
            response = method(source)
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "GET, HEAD")
            self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
            self.assertNotIn("Location", response.headers)
        response = self.client.get(f"{source}?access_token=synthetic")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Location", response.headers)
        for path in ("/podcast/missing.html", f"{source}/", f"{source}.html"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)

    def test_imported_html_alias_uses_recorded_source_identity(self) -> None:
        episode = {
            "slug": "repaired-title",
            "provenance": {"source_key": "original-title.md"},
            "public_path": "/podcast/s01e05/repaired-title",
        }
        query = "x=%2F&x=&q=A+B&q=A%20B"
        with patch("content.public_views.catalogue.podcasts", return_value=(episode,)):
            for slug in ("original-title.md", "repaired-title"):
                for method in (self.client.get, self.client.head):
                    response = method(f"/podcast/{slug}.html?{query}")
                    self.assertEqual(response.status_code, 301)
                    self.assertEqual(response["Location"], f"{episode['public_path']}?{query}")
            # No invented spelling is accepted by removing historical suffixes.
            self.assertEqual(self.client.get("/podcast/original-title.html").status_code, 404)
            collision = {**episode, "slug": "other-title", "public_path": "/podcast/s01e06/other"}
            with patch(
                "content.public_views.catalogue.podcasts", return_value=(episode, collision)
            ):
                response = self.client.get("/podcast/original-title.md.html")
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)

    def test_genai_pilots_html_alias_redirects_to_hierarchical_detail(self) -> None:
        response = self.client.get("/podcast/s24e04/from-genai-pilots-to-production")

        self.assertEqual(response.status_code, 200)
        episode = _episode("s24e04-from-genai-pilots-to-production")
        self.assertEqual(episode["public_path"], "/podcast/s24e04/from-genai-pilots-to-production")
        self.assertEqual(
            episode_graph(episode).url,
            "/podcast/s24e04/from-genai-pilots-to-production",
        )
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/podcast/s24e04/'
            'from-genai-pilots-to-production">',
            count=1,
        )
        self.assertContains(
            response,
            '<meta property="og:url" content="https://datatalks.club/podcast/s24e04/'
            'from-genai-pilots-to-production">',
            count=1,
        )

        legacy = "/podcast/s24e04-from-genai-pilots-to-production.html"
        clean_legacy = legacy.removesuffix(".html")
        for method in (self.client.get, self.client.head):
            redirect = method(legacy, follow=False)
            self.assertEqual(redirect.status_code, 301)
            self.assertEqual(redirect["Location"], episode["public_path"])
        flat_forms = (clean_legacy, f"{clean_legacy}/")
        for path in flat_forms:
            with self.subTest(path=path):
                for method in (self.client.get, self.client.head):
                    unavailable = method(path, follow=False)
                    self.assertEqual(unavailable.status_code, 404)
                    self.assertNotIn("Location", unavailable.headers)
                    self.assertNotContains(unavailable, 'rel="canonical"', status_code=404)

    def test_plain_slug_episode_routes_through_its_own_stable_id(self) -> None:
        """Episodes without a stable-id slug derive the route from season/episode."""

        episode = _episode("synthetic-episode-one")
        canonical = _canonical_path("synthetic-episode-one")

        self.assertEqual(episode["public_path"], "/podcast/s20e06/synthetic-episode-one")
        self.assertEqual(canonical, "/podcast/s20e06/synthetic-episode-one")
        response = self.client.get(canonical)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/podcast/s20e06/'
            'synthetic-episode-one">',
            count=1,
        )
        for path in ("/podcast/synthetic-episode-one.html",):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], canonical)

    def test_a_lookalike_prefix_never_hides_the_real_stable_id(self) -> None:
        """A slug spelling a different episode id keeps its full tail."""

        episode = _episode("synthetic-episode-s24e01")

        self.assertEqual(episode["public_path"], "/podcast/s20e01/synthetic-episode-s24e01")
        self.assertEqual(self.client.get(episode["public_path"]).status_code, 200)

    def test_stale_hierarchical_title_redirects_to_the_canonical_route(self) -> None:
        response = self.client.get("/podcast/s24e04/stale-title", follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"], "/podcast/s24e04/from-genai-pilots-to-production"
        )

    def test_canonical_stable_id_route_renders_the_episode(self) -> None:
        canonical = _canonical_path(self.slug)
        response = self.client.get(canonical)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            canonical,
            reverse(
                "public-podcast-by-id",
                kwargs={
                    "episode_id": _episode_id(self.slug),
                    "slug": "how-to-build-ai-that-actually-ships-in-production",
                },
            ),
        )
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{canonical}">',
            count=1,
        )

    def test_wrong_title_slug_redirects_permanently_to_current_slug(self) -> None:
        response = self.client.get("/podcast/s24e06/whatever", follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], _canonical_path(self.slug))

    def test_redirects_preserve_the_raw_query_string(self) -> None:
        query = "utm_source=route%2Btest&x=a%2Fb&blank="

        response = self.client.get(f"/podcast/s24e06/whatever?{query}", follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"],
            f"{_canonical_path(self.slug)}?{query}",
        )

    def test_credential_shaped_query_never_reaches_the_redirect_target(self) -> None:
        for spelling_index, query in enumerate(
            ("token=synthetic", "utm_source=nl&Token=synthetic")
        ):
            with self.subTest(spelling=spelling_index):
                response = self.client.get(f"/podcast/s24e06/whatever?{query}", follow=False)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("Location", response.headers)
                directives = {
                    directive.strip().casefold()
                    for directive in response.headers.get("Cache-Control", "").split(",")
                    if directive.strip()
                }
                self.assertIn("no-store", directives)
                self.assertNotIn("public", directives)

    def test_unknown_stable_id_is_not_found(self) -> None:
        response = self.client.get("/podcast/s99e99/whatever", follow=False)

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Location", response.headers)
