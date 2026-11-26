"""Podcast routes: ``/podcast/s18/`` seasons and ``/podcast/s18/e09-<slug>`` episodes.

The season and episode numbers are an episode's identity and the slug is
cosmetic: the short ``/podcast/s18/e09`` form and any other slug redirect
permanently, in one hop, to the canonical.  No older URL shape is routed.
"""

from __future__ import annotations

from typing import Any

from django.test import TestCase
from django.urls import reverse

from content import catalogue
from content.podcast_routes import podcast_canonical_path, podcast_season_canonical_path
from content.wiki_content import episode_graph

QUERY = "utm_source=route%2Btest&x=a%2Fb&blank=&q=A+B&q=A%20B"


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


class PodcastRoutePathTests(TestCase):
    def test_canonical_path_shape(self) -> None:
        self.assertEqual(
            podcast_canonical_path(season=18, episode=9, slug="dataops-for-data-engineering"),
            "/podcast/s18/e09-dataops-for-data-engineering",
        )
        self.assertEqual(podcast_season_canonical_path(18), "/podcast/s18/")
        self.assertEqual(podcast_season_canonical_path(3), "/podcast/s03/")

    def test_own_stable_id_prefix_is_not_repeated(self) -> None:
        self.assertEqual(
            podcast_canonical_path(season=24, episode=4, slug="s24e04-from-genai-pilots"),
            "/podcast/s24/e04-from-genai-pilots",
        )
        # A slug naming a different episode keeps its whole tail.
        self.assertEqual(
            podcast_canonical_path(season=20, episode=1, slug="s24e01-other"),
            "/podcast/s20/e01-s24e01-other",
        )


class PodcastEpisodeRouteTests(TestCase):
    slug = "s24e06-how-to-build-ai-that-actually-ships-in-production"
    canonical = "/podcast/s24/e06-how-to-build-ai-that-actually-ships-in-production"

    def test_every_episode_answers_at_its_canonical_path(self) -> None:
        episodes = catalogue.podcasts()
        self.assertTrue(episodes)
        for episode in episodes:
            with self.subTest(slug=episode["slug"]):
                self.assertRegex(episode["public_path"], r"\A/podcast/s[0-9]{2}/e[0-9]{2}-")
                self.assertEqual(self.client.get(episode["public_path"]).status_code, 200)

    def test_canonical_renders_with_self_canonical_tags(self) -> None:
        self.assertEqual(_episode(self.slug)["public_path"], self.canonical)
        self.assertEqual(
            reverse(
                "public-podcast",
                kwargs={
                    "season": "24",
                    "episode": "06",
                    "slug": "how-to-build-ai-that-actually-ships-in-production",
                },
            ),
            self.canonical,
        )
        response = self.client.get(self.canonical)
        self.assertEqual(response.status_code, 200)
        absolute = f"https://datatalks.club{self.canonical}"
        self.assertContains(response, f'<link rel="canonical" href="{absolute}">', count=1)
        self.assertContains(response, f'<meta property="og:url" content="{absolute}">', count=1)
        self.assertContains(response, f'"url": "{absolute}"')
        # The breadcrumb's season step is the season page.
        self.assertContains(response, "https://datatalks.club/podcast/s24/")

    def test_episode_graph_uses_the_canonical_path(self) -> None:
        episode = _episode("s24e04-from-genai-pilots-to-production")
        self.assertEqual(episode["public_path"], "/podcast/s24/e04-from-genai-pilots-to-production")
        self.assertEqual(episode_graph(episode).url, episode["public_path"])

    def test_short_form_redirects_once_to_the_canonical(self) -> None:
        for source in ("/podcast/s24/e06", "/podcast/s24/e06/"):
            for suffix in ("", f"?{QUERY}"):
                with self.subTest(source=source, suffix=suffix):
                    for method in (self.client.get, self.client.head):
                        response = method(f"{source}{suffix}", follow=False)
                        self.assertEqual(response.status_code, 301)
                        self.assertEqual(response["Location"], f"{self.canonical}{suffix}")
                        self.assertEqual(response["Cache-Control"], "public, max-age=300")
                    final = self.client.get(f"{source}{suffix}", follow=True)
                    self.assertEqual(final.status_code, 200)
                    self.assertEqual(final.redirect_chain, [(f"{self.canonical}{suffix}", 301)])

    def test_any_other_slug_redirects_once_to_the_canonical(self) -> None:
        canonical = self.canonical
        for source in (
            "/podcast/s24/e06-anything",
            "/podcast/s24/e06-how-to-build-ai",
            "/podcast/s24/e006-how-to-build-ai-that-actually-ships-in-production",
            f"{canonical}/",
            "/podcast/s24/e06-anything/",
        ):
            for suffix in ("", f"?{QUERY}"):
                with self.subTest(source=source, suffix=suffix):
                    response = self.client.get(f"{source}{suffix}", follow=False)
                    self.assertEqual(response.status_code, 301)
                    self.assertEqual(response["Location"], f"{self.canonical}{suffix}")
                    final = self.client.get(f"{source}{suffix}", follow=True)
                    self.assertEqual(final.status_code, 200)
                    self.assertEqual(final.redirect_chain, [(f"{self.canonical}{suffix}", 301)])

    def test_old_url_shapes_are_not_routed(self) -> None:
        for path in (
            "/podcast/s24e06/how-to-build-ai-that-actually-ships-in-production",
            "/podcast/s24e06/how-to-build-ai-that-actually-ships-in-production/",
            "/podcast/s24e06/",
            "/podcast/s24e06",
            "/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.html",
            "/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production",
            "/podcast/synthetic-episode-one.html",
            "/podcast/synthetic-episode-one",
            "/podcast/s/24/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)

    def test_unknown_episode_or_season_is_not_found(self) -> None:
        for path in (
            "/podcast/s99/e99-whatever",
            "/podcast/s99/e99",
            "/podcast/s24/e99-whatever",
            "/podcast/s24/e99/",
            "/podcast/s99/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)

    def test_unsafe_methods_are_refused(self) -> None:
        for path in (self.canonical, "/podcast/s24/e06", "/podcast/s24/e06-anything"):
            for method in (self.client.post, self.client.put, self.client.delete):
                with self.subTest(path=path, method=method.__name__):
                    response = method(path)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response["Allow"], "GET, HEAD")
                    self.assertNotIn("Location", response.headers)

    def test_credential_shaped_query_never_reaches_the_redirect_target(self) -> None:
        for query in ("token=synthetic", "utm_source=nl&Token=synthetic"):
            with self.subTest(query=query):
                response = self.client.get(f"/podcast/s24/e06-whatever?{query}", follow=False)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("Location", response.headers)


class PodcastSeasonRouteTests(TestCase):
    def test_season_page_renders_at_its_path(self) -> None:
        latest = max(record["season"] for record in catalogue.podcasts())
        older = min(record["season"] for record in catalogue.podcasts())
        self.assertLess(older, latest)
        path = podcast_season_canonical_path(older)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, f'<link rel="canonical" href="https://datatalks.club{path}">', count=1
        )
        # The newest season's page is the hub itself.
        response = self.client.get(podcast_season_canonical_path(latest))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, '<link rel="canonical" href="https://datatalks.club/podcast">', count=1
        )

    def test_hub_season_links_use_the_season_paths(self) -> None:
        older = min(record["season"] for record in catalogue.podcasts())
        response = self.client.get("/podcast")
        self.assertContains(response, f'href="{podcast_season_canonical_path(older)}"')
        self.assertNotContains(response, 'href="/podcast/s/')

    def test_non_canonical_season_spelling_is_not_found(self) -> None:
        older = min(record["season"] for record in catalogue.podcasts())
        for path in (f"/podcast/s{older:03d}/", f"/podcast/s/{older}/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class PodcastSitemapTests(TestCase):
    def test_sitemap_lists_canonical_episode_paths(self) -> None:
        response = self.client.get("/sitemaps/podcast.xml")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for episode in catalogue.podcasts():
            with self.subTest(slug=episode["slug"]):
                self.assertIn(f"https://datatalks.club{episode['public_path']}</loc>", body)
        self.assertNotRegex(body, r"/podcast/s[0-9]+e[0-9]+")
