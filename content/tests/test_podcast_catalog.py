from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase
from django.utils.html import conditional_escape, escape

from content.podcast_content import (
    episode_view,
    listening_platform_phrase,
    published_display,
    season_episodes,
)
from content.public_data import ordered_podcasts, podcast_seasons, public_projection
from core.seo import validated_canonical_url

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
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


class PodcastPageCompositionTests(SimpleTestCase):
    """The design 5a pages (issue #179) read every fact, or fail loudly."""

    def test_every_catalogue_record_composes_without_invention(self) -> None:
        records = public_projection()["podcasts"]
        views = tuple(episode_view(record) for record in records)

        self.assertEqual(len(views), len(records))
        for view, record in zip(views, records, strict=True):
            self.assertEqual(view.public_path, record["public_path"])
            self.assertEqual(view.season_episode, f"Season {view.season} · Episode {view.episode}")
            self.assertEqual(
                [guest.name for guest in view.guests],
                [guest["name"] for guest in record["guest_profiles"]],
            )
            self.assertEqual(
                {link.url for link in view.platform_links}, set(record["links"].values())
            )
            self.assertIn(view.watch_url, set(record["links"].values()))
        # Seventeen entries carry no publication date, and the pages simply omit it.
        self.assertEqual(sum(1 for view in views if not view.published_display), 17)

    def test_the_subscribe_sentence_names_only_platforms_every_episode_carries(self) -> None:
        """There is no show feed to link, so the copy points at what does exist."""

        season = podcast_seasons()[0]
        episodes = season_episodes(season.episodes)

        phrase = listening_platform_phrase(episodes)

        for label in phrase.replace(" and ", ", ").split(", "):
            for episode in episodes:
                self.assertIn(label, [link.label for link in episode.platform_links])
        self.assertNotIn("RSS", phrase)
        self.assertNotIn("feed", phrase.casefold())

    def test_the_subscribe_sentence_disappears_rather_than_naming_a_guess(self) -> None:
        record = dict(ordered_podcasts()[0])
        record["links"] = {}

        self.assertEqual(listening_platform_phrase(()), "")
        self.assertEqual(listening_platform_phrase((episode_view(record),)), "")

    def test_the_subscribe_sentence_reads_as_a_sentence(self) -> None:
        first, second = (dict(record) for record in ordered_podcasts()[:2])
        first["links"] = {"spotify": "https://open.spotify.com/episode/one"}
        second["links"] = {
            "spotify": "https://open.spotify.com/episode/two",
            "youtube": "https://www.youtube.com/watch?v=two",
        }

        self.assertEqual(listening_platform_phrase((episode_view(first),)), "Spotify")
        self.assertEqual(
            listening_platform_phrase((episode_view(second),)),
            "Spotify and YouTube",
        )
        # A platform only one of the two carries is not offered to either.
        self.assertEqual(
            listening_platform_phrase((episode_view(first), episode_view(second))),
            "Spotify",
        )

    def test_episode_artwork_is_named_and_never_depends_on_a_listening_link(self) -> None:
        """Artwork and a listening link are independent facts (issue #179)."""

        record = dict(ordered_podcasts()[0])
        record["links"] = {}
        unplayable = episode_view(record)
        self.assertEqual(unplayable.watch_url, "")
        self.assertTrue(unplayable.media_available)

        artwork = render_to_string("public/_episode_artwork.html", {"episode": unplayable})
        self.assertIn(f'alt="Artwork for {conditional_escape(unplayable.title)}"', artwork)
        self.assertIn(f'src="{unplayable.image_path}"', artwork)

        record["media_available"] = False
        record["image_path"] = ""
        missing = render_to_string(
            "public/_episode_artwork.html", {"episode": episode_view(record)}
        )
        self.assertIn("Artwork unavailable.", missing)
        self.assertNotIn("<img", missing)

        # Both frames — the linked one and the plain one — draw the same artwork.
        source = (REPOSITORY_ROOT / "templates/public/podcast_detail.html").read_text(
            encoding="utf-8"
        )
        self.assertEqual(source.count('{% include "public/_episode_artwork.html" %}'), 2)

    def test_publication_dates_are_read_and_never_guessed(self) -> None:
        self.assertEqual(published_display("2021-02-23"), "Feb 23, 2021")
        self.assertEqual(published_display(""), "")
        with self.assertRaisesRegex(ImproperlyConfigured, "publication date is invalid"):
            published_display("23 February 2021")

    def test_missing_or_invented_identity_fails_closed(self) -> None:
        record = dict(ordered_podcasts()[0])
        for field, value in (
            ("title", ""),
            ("description", "  "),
            ("season", 0),
            ("episode", "6"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ImproperlyConfigured):
                    episode_view({**record, field: value})
        with self.assertRaisesRegex(ImproperlyConfigured, "canonical path is invalid"):
            episode_view({**record, "public_path": "/podcast/renamed-by-hand"})
        with self.assertRaisesRegex(ImproperlyConfigured, "https address"):
            episode_view({**record, "links": {"youtube": "http://example.invalid/insecure"}})
        with self.assertRaisesRegex(ImproperlyConfigured, "must not be empty"):
            season_episodes(())


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

    def test_detail_routes_keep_html_finals_and_reject_competing_season_paths(self) -> None:
        projection = public_projection()
        podcasts = projection["podcasts"]
        migration = projection["editorial_route_migration"]
        podcast_finals = {
            item["final_path"] for item in migration["finals"] if item["collection"] == "podcasts"
        }
        podcast_aliases = [
            item for item in migration["aliases"] if item["collection"] == "podcasts"
        ]

        self.assertEqual(len(podcasts), 205)
        self.assertEqual({episode["public_path"] for episode in podcasts}, podcast_finals)
        self.assertEqual(len(podcast_finals), 205)
        self.assertTrue(
            all(path.startswith("/podcast/") and path.endswith(".html") for path in podcast_finals)
        )
        self.assertEqual(len(podcast_aliases), 410)
        self.assertEqual({item["final_path"] for item in podcast_aliases}, podcast_finals)

        episode = podcasts[0]
        final_path = episode["public_path"]
        query = "utm_source=oncall%2Btest&x=a%2Fb&blank="
        for method in ("GET", "HEAD"):
            response = self.client.generic(method, f"{final_path}?{query}", follow=False)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("Location", response.headers)
        self.assertEqual(self.client.post(final_path).status_code, 405)

        aliases = (final_path.removesuffix(".html"), f"{final_path.removesuffix('.html')}/")
        for alias_path in aliases:
            for method in ("GET", "HEAD"):
                response = self.client.generic(method, f"{alias_path}?{query}", follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], f"{final_path}?{query}")
            self.assertEqual(self.client.post(alias_path).status_code, 405)

        competing_path = (
            f"/podcast/s{episode['season']:02d}e{episode['episode']:02d}/competing-title"
        )
        for method in ("GET", "HEAD"):
            response = self.client.generic(method, competing_path, follow=False)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("Location", response.headers)
            self.assertNotContains(response, 'rel="canonical"', status_code=404)
        self.assertEqual(self.client.post(competing_path).status_code, 405)

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
                # The page carries its own stylesheet (design 5a, issue #179), so the
                # attribute also appears inside CSS selectors; only the two markup
                # markers count: the navigation's own link and the current season.
                self.assertEqual(
                    len(re.findall(r'\saria-current="page"', response.content.decode())),
                    2,
                )
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
            "season=%32",
            "season=%D9%A2",
            "season=٢",
            "season=9999999999",
            "season=2&season=3",
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

    def test_campaign_tags_ride_along_without_changing_the_season(self) -> None:
        """A tagged podcast link selects no season and is not an error (issue #174 follow-up)."""

        latest = self.client.get("/podcast").content.decode()
        for query in (
            "utm_source=newsletter&utm_medium=email",
            "fbclid=IwAR0synthetic",
            "Season=2",
            "other=2",
            "season=1&",
        ):
            with self.subTest(query=query):
                response = self.client.generic("GET", "/podcast", QUERY_STRING=query)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertNotIn(query, body)
                self.assertIn('<link rel="canonical" href="https://datatalks.club/podcast', body)
                if not query.startswith("season="):
                    self.assertEqual(body, latest)

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
        self.assertContains(oldest, str(conditional_escape(episode["description"])))
        self.assertContains(oldest, 'datetime="2021-02-23"')
        for guest in episode["guest_profiles"]:
            if guest["public_path"]:
                self.assertContains(oldest, f'href="{guest["public_path"]}"')
        self.assertFalse(any(not item["description"] for item in public_projection()["podcasts"]))
        special_description = next(
            item for item in public_projection()["podcasts"] if "&" in item["description"]
        )
        season_path = (
            "/podcast"
            if special_description["season"] == 24
            else f"/podcast?season={special_description['season']}"
        )
        escaped_description = str(conditional_escape(special_description["description"]))
        self.assertNotEqual(escaped_description, special_description["description"])
        self.assertContains(self.client.get(season_path), escaped_description)

    def test_design_system_pages_carry_one_inline_stylesheet_and_no_legacy_css(self) -> None:
        """Mockup 6d (issue #179) rebuilt both surfaces on the shared design system."""

        episode = ordered_podcasts()[0]
        for path in ("/podcast", "/podcast?season=12", episode["public_path"]):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                self.assertIn("<style>", body)
                self.assertIn("--bubble:", body)
                self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', body), [])
                for retired in (
                    "/static/courses.css",
                    "/static/core/site_shell.css",
                    "/static/core/accessibility.css",
                    "tailwindcss",
                    "fontawesome",
                ):
                    self.assertNotIn(retired, body)
                for leak in ("{#", "#}", "{%", "%}", "{{", "}}"):
                    self.assertNotIn(leak, body)

    def test_index_rows_use_the_shared_play_disc_and_row_list(self) -> None:
        season = podcast_seasons()[0]
        body = self.client.get("/podcast").content.decode()

        self.assertEqual(body.count('class="row-list"'), 1)
        self.assertEqual(body.count('class="play-disc"'), len(season.episodes))
        # An episode row is the site's shared archive row with the play disc as
        # its leading mark, and an episode with no publication date gives its
        # rail back to the card rather than leaving an empty column.
        undated = [episode for episode in season.episodes if not episode["published"]]
        self.assertEqual(body.count('class="list-row archive-row'), len(season.episodes))
        self.assertEqual(body.count("archive-row archive-row-undated"), len(undated))
        self.assertIn(f"podcast · season {season.number}", body)
        # The catalogue has no duration and no global episode number; the design's
        # "58 min" and "#214" therefore have no stand-in on the page.
        self.assertNotIn(" min<", body)
        self.assertNotIn("#214", body)

    def test_episode_page_plays_and_lists_only_real_destinations(self) -> None:
        episode = public_projection()["podcasts_by_slug"]["practical-llm-engineering-and-rag"]
        response = self.client.get(episode["public_path"])
        body = response.content.decode()

        self.assertContains(response, 'class="status-pill status-pill-mint"')
        self.assertContains(response, f"Season {episode['season']} · Episode {episode['episode']}")
        self.assertContains(response, 'class="player-frame episode-player"')
        self.assertContains(response, f'href="{episode["links"]["youtube"]}"')
        self.assertContains(response, f'src="{episode["image_path"]}"')
        for platform, label in (
            ("apple", "Apple Podcasts"),
            ("spotify", "Spotify"),
            ("youtube", "YouTube"),
            ("anchor", "Anchor"),
        ):
            self.assertContains(response, f'href="{episode["links"][platform]}"')
            self.assertContains(response, label)
        for guest in episode["guest_profiles"]:
            self.assertContains(response, escape(guest["name"]))
            self.assertContains(response, f'href="{guest["public_path"]}"')
        self.assertContains(response, 'id="transcript-heading"')
        self.assertEqual(
            body.count('class="timestamp-row"'),
            sum(1 for entry in episode["transcript"] if not entry.get("header")),
        )
        # Transcript provenance stays out of the reader's page.
        self.assertNotContains(response, episode["transcript_provenance"]["source_url"])

    def test_episode_without_a_transcript_renders_without_the_section(self) -> None:
        silent = next(
            item for item in public_projection()["podcasts"] if not item.get("transcript")
        )
        response = self.client.get(silent["public_path"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, escape(silent["title"]))
        self.assertNotContains(response, 'id="transcript-heading"')

    def test_an_ampersand_in_an_episode_title_is_escaped_on_the_page(self) -> None:
        episode = next(item for item in public_projection()["podcasts"] if "&" in item["title"])
        self.assertNotEqual(escape(episode["title"]), episode["title"])
        response = self.client.get(episode["public_path"])

        self.assertContains(response, f'<h1 id="episode-heading">{escape(episode["title"])}</h1>')

    def test_an_apostrophe_in_a_guest_name_is_escaped_on_the_page(self) -> None:
        episode = public_projection()["podcasts_by_slug"]["devrel-data-science-open-source-tools"]
        guest = next(item for item in episode["guest_profiles"] if "'" in item["name"])
        self.assertNotEqual(escape(guest["name"]), guest["name"])
        response = self.client.get(episode["public_path"])

        self.assertContains(response, escape(guest["name"]))

    def test_homepage_uses_the_same_latest_episode(self) -> None:
        latest = ordered_podcasts()[0]
        amp = next(
            episode
            for episode in ordered_podcasts()
            if "&" in episode["title"] and episode["public_path"] != latest["public_path"]
        )
        response = self.client.get("/")
        self.assertContains(response, escape(latest["title"]))
        self.assertContains(response, f'href="{latest["public_path"]}"', count=1)
        self.assertNotEqual(escape(amp["title"]), amp["title"])
        self.assertNotContains(response, escape(amp["title"]))

    def test_homepage_escapes_an_ampersand_in_the_latest_episode_title(self) -> None:
        amp = next(episode for episode in ordered_podcasts() if "&" in episode["title"])
        rest = tuple(
            episode
            for episode in ordered_podcasts()
            if episode["public_path"] != amp["public_path"]
        )
        self.assertNotEqual(escape(amp["title"]), amp["title"])
        with patch("core.views.ordered_podcasts", return_value=(amp, *rest)):
            response = self.client.get("/")
        self.assertContains(
            response,
            f'<a class="band-link" href="{amp["public_path"]}">{escape(amp["title"])}</a>',
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
