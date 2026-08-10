from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.test import Client, SimpleTestCase, TestCase

from content.public_data import ordered_podcasts, podcast_seasons, public_projection
from core.seo import validated_canonical_url


def cache_directives(response) -> set[str]:
    return {
        directive.strip().casefold()
        for directive in response.headers.get("Cache-Control", "").split(",")
        if directive.strip()
    }


class PodcastOrderingTests(SimpleTestCase):
    def test_catalogue_orders_complete_seasons_and_preserves_projection_order(self) -> None:
        projection = public_projection()["podcasts"]
        ordered = ordered_podcasts(projection)
        seasons = podcast_seasons(projection)

        self.assertEqual(len(ordered), 205)
        self.assertEqual(len({episode["slug"] for episode in ordered}), 205)
        self.assertEqual([season.number for season in seasons], list(range(24, 0, -1)))
        self.assertEqual(
            [episode["slug"] for episode in projection[:4]],
            [
                "practical-llm-engineering-and-rag",
                "bioinformatics-worflows-tools-and-data-science",
                "from-semiconductor-data-to-applied-machine-learning",
                "from-computer-vision-research-to-autonomous-driving-ai",
            ],
        )
        self.assertEqual(
            [episode["slug"] for episode in ordered[:3]],
            [
                "s24e06-how-to-build-ai-that-actually-ships-in-production",
                "s24e05-ai-adoption-in-enterprise-beyond-writing-code",
                "s24e04-from-genai-pilots-to-production",
            ],
        )
        for season in seasons:
            self.assertEqual(
                [episode["episode"] for episode in season.episodes],
                sorted(
                    [episode["episode"] for episode in season.episodes],
                    reverse=True,
                ),
            )

    def test_duplicate_episode_uses_published_descending_then_slug_ascending(self) -> None:
        duplicate = [
            episode
            for episode in ordered_podcasts()
            if episode["season"] == 3 and episode["episode"] == 4
        ]
        self.assertEqual(
            [(episode["published"], episode["slug"]) for episode in duplicate],
            [
                ("2021-05-07", "data-science-interview-and-cv-guide"),
                ("2021-05-01", "data-translator-role-and-data-strategy"),
            ],
        )
        same_date = tuple(
            {
                "season": 1,
                "episode": 1,
                "published": "2026-01-01",
                "slug": slug,
            }
            for slug in ("z-last", "a-first")
        )
        self.assertEqual(
            [episode["slug"] for episode in ordered_podcasts(same_date)],
            ["a-first", "z-last"],
        )

    def test_numbering_gaps_are_not_filled_or_renumbered(self) -> None:
        episodes_by_season = {
            season.number: [episode["episode"] for episode in season.episodes]
            for season in podcast_seasons()
        }
        self.assertEqual(episodes_by_season[24], [6, 5, 4, 3, 1])
        self.assertEqual(episodes_by_season[23], [9, 7, 6, 5, 4, 3, 2, 1])

    def test_malformed_numeric_metadata_fails_closed(self) -> None:
        valid = {
            "season": 1,
            "episode": 1,
            "published": "2026-01-01",
            "slug": "valid",
        }
        for field in ("season", "episode"):
            for value in (None, False, True, "1", 0, -1, 1.0):
                with self.subTest(field=field, value=value):
                    record = {**valid, field: value}
                    with self.assertRaisesRegex(ImproperlyConfigured, "positive integer"):
                        ordered_podcasts((record,))

    def test_only_normalized_later_podcast_pages_are_valid_canonical_queries(self) -> None:
        canonical = "https://datatalks.club/podcast?page=2"
        self.assertEqual(validated_canonical_url(canonical), canonical)
        for value in (
            "https://datatalks.club/podcast?page=1",
            "https://datatalks.club/podcast?page=02",
            "https://datatalks.club/podcast?page=%32",
            "https://datatalks.club/podcast?other=2",
            "https://datatalks.club/podcast/example.html?page=2",
            "https://datatalks.club/blog?page=2",
        ):
            with self.subTest(value=value):
                self.assertEqual(validated_canonical_url(value), "")


class PodcastPaginationTests(TestCase):
    def test_pages_contain_three_complete_seasons_and_all_episodes_once(self) -> None:
        expected_seasons = (
            (24, 23, 22),
            (21, 20, 19),
            (18, 17, 16),
            (15, 14, 13),
            (12, 11, 10),
            (9, 8, 7),
            (6, 5, 4),
            (3, 2, 1),
        )
        seen_paths: list[str] = []
        for page_number, season_numbers in enumerate(expected_seasons, start=1):
            path = "/podcast" if page_number == 1 else f"/podcast?page={page_number}"
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                tuple(season.number for season in response.context["seasons"]),
                season_numbers,
            )
            page_paths = [
                episode["public_path"]
                for season in response.context["seasons"]
                for episode in season.episodes
            ]
            self.assertEqual(
                response.content.decode().count("data-podcast-episode"), len(page_paths)
            )
            seen_paths.extend(page_paths)

        expected_paths = {episode["public_path"] for episode in public_projection()["podcasts"]}
        self.assertEqual(len(seen_paths), 205)
        self.assertEqual(len(set(seen_paths)), 205)
        self.assertEqual(set(seen_paths), expected_paths)

    def test_first_middle_and_last_pages_emit_normalized_seo_and_navigation(self) -> None:
        first = self.client.get("/podcast")
        self.assertContains(
            first,
            '<link rel="canonical" href="https://datatalks.club/podcast">',
            count=1,
        )
        self.assertNotContains(first, 'rel="prev"')
        self.assertContains(
            first,
            '<link rel="next" href="https://datatalks.club/podcast?page=2">',
            count=1,
        )
        self.assertContains(
            first, "<title>DataTalks.Club Podcast — DataTalks.Club</title>", html=True
        )

        middle = self.client.get("/podcast?page=4")
        self.assertContains(
            middle,
            '<link rel="canonical" href="https://datatalks.club/podcast?page=4">',
            count=1,
        )
        self.assertContains(
            middle,
            '<link rel="prev" href="https://datatalks.club/podcast?page=3">',
            count=1,
        )
        self.assertContains(
            middle,
            '<link rel="next" href="https://datatalks.club/podcast?page=5">',
            count=1,
        )
        self.assertContains(
            middle,
            "<title>DataTalks.Club Podcast — Page 4 — DataTalks.Club</title>",
            html=True,
        )

        last = self.client.get("/podcast?page=8")
        self.assertContains(
            last,
            '<link rel="canonical" href="https://datatalks.club/podcast?page=8">',
            count=1,
        )
        self.assertContains(
            last,
            '<link rel="prev" href="https://datatalks.club/podcast?page=7">',
            count=1,
        )
        self.assertNotContains(last, 'rel="next"')

        for response, current_page in ((first, 1), (middle, 4), (last, 8)):
            self.assertContains(response, 'aria-label="Podcast pagination"', count=1)
            self.assertContains(response, 'aria-current="page"', count=1)
            self.assertContains(
                response,
                f'aria-label="Podcast page {current_page}, current page"',
                count=1,
            )
            self.assertContains(response, 'href="/podcast"', count=2 if current_page != 1 else 1)
            self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")

    def test_page_one_query_is_rendered_with_the_clean_canonical(self) -> None:
        response = self.client.get("/podcast?page=1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/podcast">',
            count=1,
        )
        self.assertNotContains(response, "podcast?page=1", status_code=200)

    def test_invalid_queries_are_400_no_store(self) -> None:
        invalid_queries = (
            "page=",
            "page=0",
            "page=+1",
            "page=-1",
            "page=01",
            "page=1&page=2",
            "page=1&",
            "page=%32",
            "Page=2",
            "page=%D9%A2",
            "other=2",
            "page=9999999999",
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                for method in (self.client.get, self.client.head):
                    response = method(f"/podcast?{query}")
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("no-store", cache_directives(response))
                    self.assertNotIn('rel="canonical"', response.content.decode())

    def test_canonical_positive_page_beyond_last_is_404_no_store(self) -> None:
        for query in ("page=9", "page=999999999"):
            with self.subTest(query=query):
                response = self.client.get(f"/podcast?{query}")
                self.assertEqual(response.status_code, 404)
                self.assertIn("no-store", cache_directives(response))
                self.assertNotIn('rel="canonical"', response.content.decode())

    def test_aliases_preserve_valid_and_invalid_queries_in_one_hop(self) -> None:
        for alias in ("/podcast.html", "/podcast/"):
            for query in ("page=2", "page=%32&other=1"):
                with self.subTest(alias=alias, query=query):
                    response = self.client.get(f"{alias}?{query}", follow=False)
                    self.assertEqual(response.status_code, 301)
                    self.assertEqual(response.headers["Location"], f"/podcast?{query}")

    def test_catalogue_renders_episode_numbers_and_source_descriptions(self) -> None:
        page_one = self.client.get("/podcast")
        self.assertContains(page_one, "Season 24 · Episode 6")
        self.assertContains(page_one, "Season 24", count=6)

        last_page = self.client.get("/podcast?page=8")
        description = public_projection()["podcasts_by_slug"]["data-team-roles"]["description"]
        self.assertTrue(description)
        self.assertContains(last_page, description)
        self.assertFalse(
            any(not episode["description"] for episode in public_projection()["podcasts"])
        )

    def test_homepage_uses_the_same_latest_episode(self) -> None:
        latest = ordered_podcasts()[0]
        response = self.client.get("/")
        self.assertContains(response, latest["title"])
        self.assertContains(response, f'href="{latest["public_path"]}"', count=1)
        self.assertNotContains(
            response,
            public_projection()["podcasts"][0]["title"],
        )

    def test_get_and_head_are_supported_but_post_is_rejected_before_csrf(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)

        self.assertEqual(csrf_client.get("/podcast?page=2").status_code, 200)
        self.assertEqual(csrf_client.head("/podcast?page=2").status_code, 200)

        response = csrf_client.post("/podcast?page=2")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET, HEAD")
