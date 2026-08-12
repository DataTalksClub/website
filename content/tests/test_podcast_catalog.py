from __future__ import annotations

import json
import re
from unittest.mock import patch
from xml.etree import ElementTree

from django.core.exceptions import ImproperlyConfigured
from django.test import Client, SimpleTestCase, TestCase
from django.utils.html import conditional_escape

from content.public_data import ordered_podcasts, podcast_seasons, public_projection
from core.seo import validated_canonical_url

SITEMAP_NAMESPACE = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def cache_directives(response) -> set[str]:
    return {
        directive.strip().casefold()
        for directive in response.headers.get("Cache-Control", "").split(",")
        if directive.strip()
    }


class PodcastOrderingTests(SimpleTestCase):
    def test_catalogue_orders_complete_seasons_without_mutating_projection_order(self) -> None:
        projection = public_projection()["podcasts"]
        source_order = tuple(episode["slug"] for episode in projection)
        ordered = ordered_podcasts(projection)
        seasons = podcast_seasons(projection)

        self.assertEqual(len(ordered), 205)
        self.assertEqual(len({episode["slug"] for episode in ordered}), 205)
        self.assertEqual([season.number for season in seasons], list(range(24, 0, -1)))
        self.assertEqual(
            source_order[:4],
            (
                "practical-llm-engineering-and-rag",
                "bioinformatics-worflows-tools-and-data-science",
                "from-semiconductor-data-to-applied-machine-learning",
                "from-computer-vision-research-to-autonomous-driving-ai",
            ),
        )
        self.assertEqual(tuple(episode["slug"] for episode in projection), source_order)
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

    def test_malformed_numeric_metadata_and_empty_catalogue_fail_closed(self) -> None:
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
            missing = dict(valid)
            del missing[field]
            with self.subTest(field=field, value="missing"):
                with self.assertRaisesRegex(ImproperlyConfigured, "positive integer"):
                    ordered_podcasts((missing,))
        with self.assertRaisesRegex(ImproperlyConfigured, "must not be empty"):
            podcast_seasons(())

    def test_only_normalized_podcast_seasons_are_valid_canonical_queries(self) -> None:
        for season in (1, 24, 999_999_999):
            canonical = f"https://datatalks.club/podcast?season={season}"
            self.assertEqual(validated_canonical_url(canonical), canonical)
        for value in (
            "https://datatalks.club/podcast?page=1",
            "https://datatalks.club/podcast?page=2",
            "https://datatalks.club/podcast?season=01",
            "https://datatalks.club/podcast?season=%32",
            "https://datatalks.club/podcast?other=2",
            "https://datatalks.club/podcast?season=2&other=1",
            "https://datatalks.club/podcast/example.html?season=2",
            "https://datatalks.club/blog?season=2",
            "https://datatalks.club/events?season=2",
            "https://datatalks.club/books?season=2",
            "https://datatalks.club/wiki?season=2",
        ):
            with self.subTest(value=value):
                self.assertEqual(validated_canonical_url(value), "")


class PodcastSeasonNavigationTests(TestCase):
    def test_each_actual_season_contains_one_complete_season_and_all_details_once(self) -> None:
        projection = public_projection()
        seasons = podcast_seasons()
        seen_paths: list[str] = []
        self.assertEqual(tuple(season.number for season in seasons), tuple(range(24, 0, -1)))

        for season in seasons:
            path = "/podcast" if season.number == 24 else f"/podcast?season={season.number}"
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["season"].number, season.number)
            self.assertNotIn("seasons", response.context)
            body = response.content.decode()
            page_paths = [episode["public_path"] for episode in season.episodes]
            self.assertEqual(body.count("data-podcast-season="), 1)
            self.assertEqual(body.count("data-podcast-episode"), len(page_paths))
            for episode in season.episodes:
                self.assertIn(f'href="{episode["public_path"]}"', body)
                self.assertIn(str(conditional_escape(episode["description"])), body)
                for guest in episode["guest_profiles"]:
                    if guest["public_path"]:
                        self.assertIn(f'href="{guest["public_path"]}"', body)
            seen_paths.extend(page_paths)

        expected_paths = {episode["public_path"] for episode in projection["podcasts"]}
        self.assertEqual(len(seen_paths), 205)
        self.assertEqual(len(set(seen_paths)), 205)
        self.assertEqual(set(seen_paths), expected_paths)

        for detail_path in seen_paths:
            detail = self.client.get(detail_path)
            self.assertEqual(detail.status_code, 200)
            self.assertContains(
                detail,
                f'<link rel="canonical" href="https://datatalks.club{detail_path}">',
                count=1,
            )
            payload_match = re.search(
                r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
                detail.content.decode(),
                re.DOTALL,
            )
            self.assertIsNotNone(payload_match)
            assert payload_match is not None
            types = {item.get("@type") for item in json.loads(payload_match.group(1))["@graph"]}
            self.assertIn("PodcastEpisode", types)

    def test_latest_middle_and_oldest_emit_exact_seo_and_navigation(self) -> None:
        scenarios = (
            ("/podcast", 24, "/podcast", "DataTalks.Club Podcast — DataTalks.Club", None, 23),
            (
                "/podcast?season=12",
                12,
                "/podcast?season=12",
                "DataTalks.Club Podcast — Season 12 — DataTalks.Club",
                13,
                11,
            ),
            (
                "/podcast?season=1",
                1,
                "/podcast?season=1",
                "DataTalks.Club Podcast — Season 1 — DataTalks.Club",
                2,
                None,
            ),
        )
        for path, season, canonical, title, newer, older in scenarios:
            with self.subTest(season=season):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    (f'<link rel="canonical" href="https://datatalks.club{canonical}">'),
                    count=1,
                )
                self.assertContains(response, f"<title>{title}</title>", html=True)
                self.assertContains(response, 'aria-label="Podcast seasons"', count=1)
                self.assertContains(
                    response,
                    f'aria-label="Season {season}, current season"',
                    count=1,
                )
                self.assertContains(response, 'aria-current="page"', count=2)
                self.assertEqual(
                    tuple(link["number"] for link in response.context["season_links"]),
                    tuple(range(24, 0, -1)),
                )
                self.assertEqual(
                    len(
                        {
                            link["path"]
                            for link in response.context["season_links"]
                            if link["number"] != season
                        }
                    ),
                    23,
                )
                self.assertEqual(
                    response.context["season_links"][0]["path"],
                    "/podcast",
                )
                self.assertNotContains(response, "/podcast?season=24")
                self.assertNotContains(response, "Podcast pagination")
                self.assertNotContains(response, "Previous")
                self.assertNotContains(response, "Next")
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                self.assertEqual(
                    cache_directives(response),
                    {"max-age=0", "must-revalidate"},
                )

                if newer is None:
                    self.assertNotContains(response, "Newer season —")
                    self.assertNotContains(response, 'rel="prev"')
                else:
                    newer_path = "/podcast" if newer == 24 else f"/podcast?season={newer}"
                    self.assertContains(response, f"Newer season — Season {newer}", count=1)
                    self.assertContains(
                        response,
                        f'<link rel="prev" href="https://datatalks.club{newer_path}">',
                        count=1,
                    )

                if older is None:
                    self.assertNotContains(response, "Older season —")
                    self.assertNotContains(response, 'rel="next"')
                else:
                    older_path = f"/podcast?season={older}"
                    self.assertContains(response, f"Older season — Season {older}", count=1)
                    self.assertContains(
                        response,
                        f'<link rel="next" href="https://datatalks.club{older_path}">',
                        count=1,
                    )

    def test_explicit_latest_season_uses_clean_metadata_and_is_not_linked(self) -> None:
        response = self.client.get("/podcast?season=24")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/podcast">',
            count=1,
        )
        self.assertContains(
            response,
            "<title>DataTalks.Club Podcast — DataTalks.Club</title>",
            html=True,
        )
        self.assertNotContains(response, "/podcast?season=24")

    def test_higher_season_becomes_clean_default_and_real_adjacency_skips_gaps(self) -> None:
        projection = public_projection()
        synthetic = {
            **ordered_podcasts()[0],
            "season": 30,
            "episode": 2,
            "published": "2026-08-12",
            "slug": "synthetic-future-season",
            "public_path": "/podcast/synthetic-future-season.html",
            "title": "Synthetic future episode",
            "description": "A synthetic ordering fixture.",
            "guest_profiles": (),
        }
        records = (synthetic, *projection["podcasts"])
        synthetic_seasons = podcast_seasons(records)

        with patch("content.public_views.podcast_seasons", return_value=synthetic_seasons):
            latest = self.client.get("/podcast")
            self.assertEqual(latest.context["season"].number, 30)
            self.assertContains(latest, "Synthetic future episode")
            self.assertContains(latest, "Older season — Season 24", count=1)
            self.assertNotContains(latest, "Season 29")
            self.assertContains(
                latest,
                '<link rel="canonical" href="https://datatalks.club/podcast">',
                count=1,
            )

            former_latest = self.client.get("/podcast?season=24")
            self.assertContains(
                former_latest,
                '<link rel="canonical" href="https://datatalks.club/podcast?season=24">',
                count=1,
            )
            self.assertContains(former_latest, "Newer season — Season 30", count=1)
            self.assertContains(
                former_latest,
                '<link rel="prev" href="https://datatalks.club/podcast">',
                count=1,
            )
            absent = self.client.get("/podcast?season=29")
            self.assertEqual(absent.status_code, 404)
            self.assertIn("no-store", cache_directives(absent))

        synthetic_projection = dict(projection)
        synthetic_projection["podcasts"] = records
        with patch("core.views.public_projection", return_value=synthetic_projection):
            homepage = self.client.get("/")
        self.assertContains(homepage, "Synthetic future episode")
        self.assertContains(homepage, 'href="/podcast/synthetic-future-season.html"', count=1)

    def test_strict_invalid_queries_are_bounded_400_no_store(self) -> None:
        invalid_queries = (
            "season=",
            "season=0",
            "season=+1",
            "season=-1",
            "season=01",
            "season=1.0",
            "season=1&season=2",
            "season=1&",
            "season=%32",
            "season=%D9%A2",
            "season=٢",
            "Season=2",
            "other=2",
            "season=2&other=1",
            "season=9999999999",
            "page=1",
            "page=2",
            "page=999999999",
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                for method in ("GET", "HEAD"):
                    response = self.client.generic(method, "/podcast", QUERY_STRING=query)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(
                        cache_directives(response),
                        {"no-store", "max-age=0"},
                    )
                    body = response.content.decode()
                    self.assertNotIn(query, body)
                    self.assertNotIn('rel="canonical"', body)
                    self.assertNotIn('rel="prev"', body)
                    self.assertNotIn('rel="next"', body)
                    self.assertNotIn("data-podcast-episode", body)

    def test_normalized_absent_seasons_are_404_no_store_without_fallback(self) -> None:
        for season in (25, 999_999_999):
            with self.subTest(season=season):
                for method in ("GET", "HEAD"):
                    response = self.client.generic(
                        method,
                        "/podcast",
                        QUERY_STRING=f"season={season}",
                    )
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(
                        cache_directives(response),
                        {"no-store", "max-age=0"},
                    )
                    body = response.content.decode()
                    self.assertNotIn(str(season), body)
                    self.assertNotIn('rel="canonical"', body)
                    self.assertNotIn('rel="prev"', body)
                    self.assertNotIn('rel="next"', body)
                    self.assertNotIn("data-podcast-episode", body)

    def test_aliases_preserve_valid_and_invalid_raw_queries_in_one_hop(self) -> None:
        for alias in ("/podcast.html", "/podcast/"):
            for query in ("season=12", "season=%32&other=1", "page=2"):
                with self.subTest(alias=alias, query=query):
                    response = self.client.generic(
                        "GET",
                        alias,
                        QUERY_STRING=query,
                        follow=False,
                    )
                    self.assertEqual(response.status_code, 301)
                    self.assertEqual(response.headers["Location"], f"/podcast?{query}")

    def test_catalogue_renders_episode_numbers_descriptions_dates_and_guests(self) -> None:
        latest = self.client.get("/podcast")
        self.assertContains(latest, "Season 24 · Episode 6")

        oldest = self.client.get("/podcast?season=1")
        episode = public_projection()["podcasts_by_slug"]["data-team-roles"]
        self.assertTrue(episode["description"])
        self.assertContains(oldest, episode["description"])
        self.assertContains(oldest, 'datetime="2021-02-23"')
        for guest in episode["guest_profiles"]:
            if guest["public_path"]:
                self.assertContains(oldest, f'href="{guest["public_path"]}"')
        self.assertFalse(any(not item["description"] for item in public_projection()["podcasts"]))

    def test_homepage_uses_the_same_latest_episode(self) -> None:
        latest = ordered_podcasts()[0]
        response = self.client.get("/")
        self.assertContains(response, latest["title"])
        self.assertContains(response, f'href="{latest["public_path"]}"', count=1)
        self.assertNotContains(
            response,
            public_projection()["podcasts"][0]["title"],
        )

    def test_get_head_post_and_credential_cache_boundaries(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        get_response = csrf_client.get("/podcast?season=12")
        head_response = csrf_client.head("/podcast?season=12")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(head_response.status_code, 200)
        self.assertEqual(head_response.content, b"")
        self.assertEqual(
            head_response.headers["Cache-Control"],
            get_response.headers["Cache-Control"],
        )
        self.assertEqual(
            head_response.context["canonical_url"],
            "https://datatalks.club/podcast?season=12",
        )

        with patch(
            "content.public_views.podcast_seasons",
            side_effect=AssertionError("POST reached catalogue work"),
        ):
            post_response = csrf_client.post("/podcast?season=12")
        self.assertEqual(post_response.status_code, 405)
        self.assertEqual(post_response.headers["Allow"], "GET, HEAD")
        self.assertEqual(
            cache_directives(post_response),
            {"no-store", "max-age=0"},
        )

        credentialed = self.client.get(
            "/podcast?season=12",
            HTTP_AUTHORIZATION="Bearer synthetic-not-a-secret",
        )
        self.assertEqual(credentialed.status_code, 200)
        self.assertIn("private", cache_directives(credentialed))
        self.assertIn("no-store", cache_directives(credentialed))

    def test_podcast_sitemap_is_clean_hub_plus_205_unchanged_details(self) -> None:
        response = self.client.get("/sitemaps/podcast.xml")
        self.assertEqual(response.status_code, 200)
        document = ElementTree.fromstring(response.content)
        locations = [node.text or "" for node in document.findall("s:url/s:loc", SITEMAP_NAMESPACE)]
        expected = {
            "https://datatalks.club/podcast",
            *(
                f"https://datatalks.club{episode['public_path']}"
                for episode in public_projection()["podcasts"]
            ),
        }
        self.assertEqual(len(locations), 206)
        self.assertEqual(len(set(locations)), 206)
        self.assertEqual(set(locations), expected)
        self.assertFalse(any("?" in location for location in locations))
        self.assertFalse(any(location.endswith("/podcast.html") for location in locations))
