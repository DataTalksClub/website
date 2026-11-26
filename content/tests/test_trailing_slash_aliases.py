"""Trailing-slash spellings of rendered public pages redirect once to their canonical.

Links carrying a CDN-added trailing slash exist in the wild.  Spec 02 keeps the canonicals
exact and slashless; the slashed spelling of a page this site renders is a one-hop permanent
``GET``/``HEAD`` redirect that preserves the raw query, and nothing else is guessed.  Event
detail coverage lives with the other Event route tests in ``events/tests/test_models.py``;
course pages in ``courses/tests/test_course_slash_aliases.py``.
"""

from __future__ import annotations

from django.test import TestCase

from content import catalogue, wiki_reader

RAW_QUERY = "utm_source=route-test&x=%2F&q=A+B"


class SlashAliasAssertions(TestCase):
    def assert_one_hop(self, source: str, target: str) -> None:
        for method in (self.client.get, self.client.head):
            for query in ("", f"?{RAW_QUERY}"):
                with self.subTest(method=method.__name__, source=source, query=query):
                    response = method(f"{source}{query}", follow=False)
                    self.assertEqual(response.status_code, 301)
                    self.assertEqual(response.headers["Location"], f"{target}{query}")
                    self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")
        terminal = self.client.get(target, follow=False)
        self.assertEqual(terminal.status_code, 200)
        self.assertNotIn("Location", terminal.headers)

    def assert_not_found(self, path: str) -> None:
        with self.subTest(path=path):
            response = self.client.get(path, follow=False)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("Location", response.headers)

    def assert_refuses_unsafe_and_credentials(self, source: str) -> None:
        with self.subTest(source=source):
            posted = self.client.post(source)
            self.assertEqual(posted.status_code, 405)
            self.assertEqual(posted.headers["Allow"], "GET, HEAD")
            refused = self.client.get(f"{source}?token=secret", follow=False)
            self.assertEqual(refused.status_code, 400)
            self.assertNotIn("Location", refused.headers)


class PodcastEpisodeSlashAliasTests(SlashAliasAssertions):
    def setUp(self) -> None:
        super().setUp()
        self.episode = next(
            episode
            for episode in catalogue.podcasts()
            if not episode["public_path"].endswith(".html")
        )

    def test_episode_slash_spellings_redirect_once(self) -> None:
        canonical = self.episode["public_path"]
        self.assertFalse(canonical.endswith("/"))
        self.assert_one_hop(f"{canonical}/", canonical)
        season, episode = canonical.removeprefix("/podcast/").split("-", 1)[0].split("/")
        self.assert_one_hop(f"/podcast/{season}/{episode}/", canonical)
        # A stale title slug plus slash still reaches the canonical in one hop.
        self.assert_one_hop(f"/podcast/{season}/{episode}-stale-title/", canonical)

    def test_unknown_episode_and_doubled_slash_are_404s(self) -> None:
        self.assert_not_found("/podcast/s999/e999/")
        self.assert_not_found("/podcast/s999/e999-unknown/")
        self.assert_not_found(f"{self.episode['public_path']}//")

    def test_unsafe_methods_and_credential_queries_are_refused(self) -> None:
        self.assert_refuses_unsafe_and_credentials(f"{self.episode['public_path']}/")


class WikiSlashAliasTests(SlashAliasAssertions):
    def test_wiki_page_and_fixed_page_slash_spellings_redirect_once(self) -> None:
        page = wiki_reader.wiki_pages()[0]
        canonical = page["public_path"]
        self.assertEqual(canonical, f"/wiki/{page['slug']}")
        self.assert_one_hop(f"{canonical}/", canonical)
        for target in (
            "/wiki/graph",
            "/wiki/special-pages",
            "/wiki/special-pages/guides",
        ):
            self.assert_one_hop(f"{target}/", target)

    def test_unknown_or_non_page_slash_spellings_are_404s(self) -> None:
        page = wiki_reader.wiki_pages()[0]
        self.assert_not_found("/wiki/no-such-wiki-page/")
        self.assert_not_found("/wiki/special-pages/no-such-category/")
        self.assert_not_found(f"{page['public_path']}//")

    def test_wiki_hub_slash_alias_is_unchanged(self) -> None:
        response = self.client.get("/wiki/", follow=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/wiki")

    def test_unsafe_methods_and_credential_queries_are_refused(self) -> None:
        self.assert_refuses_unsafe_and_credentials("/wiki/graph/")


class StaticPageSlashAliasTests(SlashAliasAssertions):
    def test_static_page_slash_spellings_redirect_once(self) -> None:
        for canonical in ("/slack", "/sponsors", "/about", "/courses/projects"):
            self.assert_one_hop(f"{canonical}/", canonical)
