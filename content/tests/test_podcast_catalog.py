from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch
from xml.etree import ElementTree

from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.html import conditional_escape, escape

from content import catalogue
from content.podcast_content import (
    _spotify_creator_embed,
    episode_navigation,
    episode_view,
    listening_platform_phrase,
    ordered_podcasts,
    podcast_seasons,
    published_display,
    season_episodes,
)
from content.podcast_routes import (
    PODCAST_AI_PRODUCTION_PATH,
    PODCAST_GENAI_PILOTS_PATH,
    PODCAST_ROUTE_MIGRATION_PATH,
    podcast_legacy_path,
)
from content.public_data import public_projection
from core.seo import validated_canonical_url

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SITEMAP_NAMESPACE = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


def cache_directives(response) -> set[str]:
    return {
        directive.strip().casefold()
        for directive in response.headers.get("Cache-Control", "").split(",")
        if directive.strip()
    }


class PodcastOrderingTests(TestCase):
    def test_catalogue_orders_complete_seasons_without_mutating_projection_order(self) -> None:
        projection = catalogue.podcasts()
        source_order = tuple(episode["slug"] for episode in projection)
        ordered = ordered_podcasts(projection)
        seasons = podcast_seasons(projection)

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


class PodcastPageCompositionTests(TestCase):
    """The design system pages (issue #179) read every fact, or fail loudly."""

    def test_every_catalogue_record_composes_without_invention(self) -> None:
        records = catalogue.podcasts()
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
        self.assertEqual(sum(1 for view in views if not view.published_display), 16)

    def test_guest_public_paths_keep_only_safe_root_relative_links(self) -> None:
        record = dict(ordered_podcasts()[0])
        cases = (
            ("/people/safe-guest.html", "/people/safe-guest.html"),
            ("//external.example/guest", ""),
            ("https://external.example/guest", ""),
            ("/people/safe-guest.html?tab=bio", ""),
            ("/people/safe-guest.html#bio", ""),
            ("/people/safe-guest.html/extra", ""),
            ("/podcast/safe-guest.html", ""),
            ("/people/../admin", ""),
            ("/people/%2e%2e/admin", ""),
            ("/people/safe-guest.html\x00", ""),
        )
        for raw_path, expected_path in cases:
            with self.subTest(raw_path=repr(raw_path)):
                record["guest_profiles"] = [
                    {"key": "", "name": "Synthetic Guest", "public_path": raw_path}
                ]
                self.assertEqual(episode_view(record).guests[0].public_path, expected_path)

        record["guest_profiles"] = [
            {"key": "unsafe-person", "name": "Synthetic Guest", "public_path": ""}
        ]
        view = episode_view(
            record,
            people_by_slug={"unsafe-person": {"public_path": "//external.example/guest"}},
        )
        self.assertEqual(view.guests[0].public_path, "")

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
        # The current catalogue carries no episode artwork at all (every record's
        # ``image_path`` is empty), so this fact is asserted explicitly rather than
        # relied upon from the real projection -- the point under test is that
        # artwork availability is independent of a listening link, not of content.
        record["media_available"] = True
        record["image_path"] = "/images/podcast/example-episode.jpg"
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

        # The fallback frame still draws the shared artwork partial. A validated embed is
        # intentionally an iframe rather than an artwork link.
        source = (REPOSITORY_ROOT / "templates/public/podcast_detail.html").read_text(
            encoding="utf-8"
        )
        self.assertEqual(source.count('{% include "public/_episode_artwork.html" %}'), 1)

    def test_publication_dates_are_read_and_never_guessed(self) -> None:
        self.assertEqual(published_display("2021-02-23"), "Feb 23, 2021")
        self.assertEqual(published_display(""), "")
        with self.assertRaisesRegex(ImproperlyConfigured, "publication date is invalid"):
            published_display("23 February 2021")

    def test_timestamp_fallbacks_keep_non_youtube_destinations_and_skip_unseekable_rows(
        self,
    ) -> None:
        record = dict(ordered_podcasts()[0])
        record["links"] = {"spotify": "https://open.spotify.com/episode/synthetic"}
        record["video"] = None
        record["transcript"] = [
            {"header": "Unseekable context"},
            {"line": "Readable without a numeric seek point.", "time": "About noon"},
            {"header": "Native seek point"},
            {"line": "A native listening fallback.", "sec": 42, "time": "0:42"},
        ]

        view = episode_view(record)

        self.assertEqual(len(view.timestamp_entries), 1)
        self.assertEqual(view.timestamp_entries[0].fallback_url, record["links"]["spotify"])
        self.assertEqual(view.transcript[1].fallback_url, "")

    def test_timestamp_topics_use_the_first_timed_line_in_each_source_section(self) -> None:
        record = dict(ordered_podcasts()[0])
        record["links"] = {"youtube": "https://www.youtube.com/watch?v=Video_1"}
        record["video"] = {"provider": "youtube", "id": "Video_1"}
        record["transcript"] = [
            {"header": "A source-backed topic"},
            {"line": "An opening paragraph without a marker."},
            {"line": "The first paragraph with a marker.", "sec": 17, "time": "0:17"},
        ]

        view = episode_view(record)

        self.assertEqual(len(view.timestamp_entries), 1)
        self.assertEqual(view.timestamp_entries[0].label, "A source-backed topic")
        self.assertEqual(view.timestamp_entries[0].time, "0:17")
        self.assertEqual(view.timestamp_entries[0].seconds, 17)

    def test_spotify_creator_link_derives_a_safe_embed_without_inventing_an_id(self) -> None:
        target = _episode("s24e05-ai-adoption-in-enterprise-beyond-writing-code")
        creator_key = next(
            key for key in ("spotify_for_creators", "anchor") if key in target["links"]
        )
        record = {
            **target,
            "links": {creator_key: target["links"][creator_key]},
            "video": None,
        }

        view = episode_view(record)

        self.assertIsNotNone(view.spotify)
        assert view.spotify is not None
        self.assertEqual(view.spotify.provider, "spotify")
        self.assertEqual(
            view.spotify.media_id,
            "AI-Adoption-in-Enterprise-Beyond-Writing-Code---Ivan-Bilan-e3l6h0m",
        )
        self.assertEqual(
            view.spotify.embed_url,
            "https://creators.spotify.com/pod/profile/datatalksclub/embed/episodes/"
            "AI-Adoption-in-Enterprise-Beyond-Writing-Code---Ivan-Bilan-e3l6h0m",
        )
        self.assertIs(view.player, view.spotify)
        self.assertIsNone(
            _spotify_creator_embed(
                "https://evil.example/pod/profile/datatalksclub/episodes/not-a-source"
            )
        )
        self.assertIsNone(
            _spotify_creator_embed(
                "https://creators.spotify.com/pod/profile/datatalksclub/episodes/not-safe/https:bad"
            )
        )

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


class PodcastEpisodeParityTests(TestCase):
    representative_slug = "s24e06-how-to-build-ai-that-actually-ships-in-production"

    def representative(self) -> tuple[dict, dict]:
        projection = public_projection()
        return projection, _episode(self.representative_slug)

    def test_representative_composes_resources_video_timestamps_and_person_bio(self) -> None:
        projection, record = self.representative()
        view = episode_view(record, people_by_slug=projection["people_by_slug"])

        # The catalogue carries no episode artwork today: every record's `image_path`
        # is empty, this one included.
        self.assertEqual(record["image_path"], "")
        self.assertEqual(view.image_path, record["image_path"])
        self.assertEqual(
            [(resource.title, resource.url) for resource in view.resources],
            [
                ("Website", "https://alexkimds.github.io/"),
                ("LinkedIn", "https://www.linkedin.com/in/aleksandrkim/"),
            ],
        )
        self.assertIsNotNone(view.video)
        assert view.video is not None
        self.assertEqual(view.video.provider, "youtube")
        self.assertEqual(view.video.video_id, "PosCx_4fwt0")
        self.assertEqual(
            view.video.embed_url,
            "https://www.youtube-nocookie.com/embed/PosCx_4fwt0?enablejsapi=1&rel=0",
        )
        self.assertEqual(len(view.transcript), len(record["transcript"]))
        self.assertEqual(len(view.timestamp_entries), 10)
        self.assertEqual(
            view.timestamp_entries[0].label,
            "AI Engineering Production and Scalability",
        )
        self.assertEqual(
            view.timestamp_entries[0].fallback_url,
            "https://www.youtube.com/watch?v=PosCx_4fwt0&t=0",
        )
        guest = view.guests[0]
        self.assertEqual(guest.name, "Aleksandr Kim")
        self.assertEqual(guest.image_path, "/images/authors/aleksandrkim.jpg")
        self.assertIn("Senior Data Scientist at Intuit", guest.summary)
        self.assertEqual(
            [link.label for link in guest.profile_links],
            ["Website", "LinkedIn"],
        )

    def test_representative_page_is_server_rendered_and_undated_metadata_is_omitted(self) -> None:
        _, record = self.representative()
        response = self.client.get(record["public_path"])
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "max-age=0, must-revalidate")
        for heading_id in (
            "show-notes-heading",
            "timestamps-heading",
            "transcript-heading",
            "guest-bios-heading",
            "related-episodes-heading",
        ):
            self.assertContains(response, f'id="{heading_id}"')
        self.assertContains(response, 'id="podcast-video-player"')
        self.assertContains(response, 'href="https://www.youtube.com/watch?v=PosCx_4fwt0&amp;t=0"')
        self.assertNotIn('property="article:published_time"', body)
        match = re.search(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        graph = json.loads(match.group(1))["@graph"]
        entity = next(item for item in graph if item.get("@type") == "PodcastEpisode")
        self.assertNotIn("datePublished", entity)
        self.assertEqual(entity["episodeNumber"], record["episode"])
        self.assertEqual(entity["partOfSeason"]["seasonNumber"], record["season"])
        self.assertEqual(entity["url"], "https://datatalks.club" + record["public_path"])
        self.assertNotIn("wiki_graph", body)
        self.assertNotIn("list-manage.com", body)
        self.assertNotIn(record["transcript_provenance"]["source_url"], body)

    def test_hero_leads_with_guest_identity_and_the_standfirst_description(self) -> None:
        # Issue #234: the live production page leads with the guest's identity
        # and the episode standfirst before anything else. This page keeps the
        # h1 title-only (so the JSON-LD `name` above stays clean) and instead
        # surfaces the guest chip and the description inside the cream hero,
        # ahead of the player/platform band — not duplicated in both places.
        projection, record = self.representative()
        view = episode_view(record, people_by_slug=projection["people_by_slug"])
        response = self.client.get(record["public_path"])
        body = response.content.decode()

        hero_start = body.find('class="band band-cream episode-hero"')
        about_start = body.find('class="band band-lavender episode-read"')
        self.assertNotEqual(hero_start, -1)
        self.assertNotEqual(about_start, -1)
        about_end = body.find("<section", about_start + 1)
        self.assertNotEqual(about_end, -1)
        hero_html = body[hero_start:about_start]
        about_html = body[about_start:about_end]

        self.assertIn(escape(view.guests[0].name), hero_html)
        self.assertIn(escape(view.description), hero_html)
        self.assertNotIn(escape(view.guests[0].name), about_html)
        self.assertNotIn(escape(view.description), about_html)

    def test_about_band_names_the_listening_platforms_with_a_real_heading(self) -> None:
        _, record = self.representative()
        response = self.client.get(record["public_path"])

        self.assertContains(response, "Listen to or watch on your favorite platform")
        self.assertContains(response, 'id="platform-links-heading"')
        self.assertContains(response, 'aria-labelledby="platform-links-heading"')

    def test_reading_sections_render_without_baked_in_tab_roles_or_hidden_panels(self) -> None:
        # The tabs are progressive enhancement applied by client-side script: the
        # server-rendered page must never bake in ARIA tab roles or a `hidden`
        # panel, or a visitor without JavaScript loses reachable content.
        _, record = self.representative()
        response = self.client.get(record["public_path"])
        body = response.content.decode()

        self.assertNotIn('role="tablist"', body)
        self.assertNotIn('role="tab"', body)
        self.assertNotIn('role="tabpanel"', body)
        for section_id in ("show-notes", "timestamps", "transcript"):
            match = re.search(rf'<section id="{section_id}"[^>]*>', body)
            self.assertIsNotNone(match)
            assert match is not None
            self.assertNotIn("hidden", match.group(0))

    def test_timestamps_are_topic_markers_separate_from_transcript_body(self) -> None:
        projection, record = self.representative()
        view = episode_view(record, people_by_slug=projection["people_by_slug"])
        response = self.client.get(record["public_path"])
        body = response.content.decode()

        timestamp_text = " ".join(f"{entry.time} {entry.label}" for entry in view.timestamp_entries)
        transcript_body = " ".join(entry.line for entry in view.transcript if entry.line)
        self.assertNotEqual(timestamp_text, transcript_body)
        self.assertNotIn(transcript_body, timestamp_text)
        self.assertNotIn(view.transcript[2].line, timestamp_text)

        timestamp_start = body.index('<section id="timestamps"')
        timestamp_end = body.index("</section>", timestamp_start)
        timestamp_markup = body[timestamp_start:timestamp_end]
        self.assertEqual(
            timestamp_markup.count('class="timestamp-row timestamp-index-row"'),
            len(view.timestamp_entries),
        )
        self.assertNotIn(view.transcript[2].line, timestamp_markup)
        self.assertIn(view.transcript[2].line, body[body.index('<section id="transcript"') :])

    def test_previous_and_next_episode_cards_show_their_season_and_episode(self) -> None:
        projection, record = self.representative()
        previous_episode, next_episode, _ = episode_navigation(
            record, catalogue.podcasts(), people_by_slug=projection["people_by_slug"]
        )
        response = self.client.get(record["public_path"])
        body = response.content.decode()

        if previous_episode is not None:
            self.assertIn(previous_episode.season_episode, body)
        if next_episode is not None:
            self.assertIn(next_episode.season_episode, body)

    def test_checked_detail_and_adjacent_destinations_render_with_internal_resource(self) -> None:
        projection = public_projection()
        record = _episode("practical-devrel-demofirst-education-and-open-source")
        previous, following, _ = episode_navigation(
            record,
            catalogue.podcasts(),
            people_by_slug=projection["people_by_slug"],
        )
        self.assertIsNotNone(previous)
        self.assertIsNotNone(following)
        assert previous is not None and following is not None

        response = self.client.get(record["public_path"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, previous.public_path)
        self.assertContains(response, following.public_path)
        for destination in (previous.public_path, following.public_path):
            with self.subTest(destination=destination):
                self.assertEqual(self.client.get(destination).status_code, 200)

        internal_resource_record = _episode(
            "data-freelancing-career-strategy-market-demand-and-client-acquisition"
        )
        view = episode_view(
            internal_resource_record,
            people_by_slug=projection["people_by_slug"],
            resource_podcast_records=catalogue.podcasts(),
        )
        self.assertIn(
            "/podcast/s16e09/becoming-data-freelancer",
            [resource.url for resource in view.resources],
        )
        resource_response = self.client.get(internal_resource_record["public_path"])
        self.assertContains(
            resource_response,
            'href="/podcast/s16e09/becoming-data-freelancer"',
        )
        self.assertNotContains(
            resource_response,
            'href="/podcast/s16e09-become-data-freelancer.html"',
        )

    def test_resource_urls_fail_closed_without_taking_down_the_episode_page(self) -> None:
        projection, record = self.representative()
        for unsafe in (
            "http://example.invalid/resource",
            "javascript:alert(1)",
            "//example.invalid/resource",
            "/podcast/../admin.html",
            "/podcast/episode.html?next=/admin",
            "/podcast/not-in-catalog.html",
            "/blog/episode.html",
        ):
            with self.subTest(unsafe=unsafe):
                view = episode_view(
                    {
                        **record,
                        "resources": [{"title": "Unsafe", "url": unsafe}],
                    },
                    resource_podcast_records=catalogue.podcasts(),
                )
                self.assertEqual(view.resources, ())

    def test_related_episode_cards_show_the_guest_instead_of_the_description(self) -> None:
        projection, record = self.representative()
        _, _, related_episodes = episode_navigation(
            record, catalogue.podcasts(), people_by_slug=projection["people_by_slug"]
        )
        self.assertTrue(related_episodes)
        response = self.client.get(record["public_path"])
        body = response.content.decode()

        related_start = body.find('id="related-episodes-heading"')
        self.assertNotEqual(related_start, -1)
        related_html = body[related_start:]
        for related in related_episodes:
            if related.guest_names:
                self.assertIn(escape(related.guest_names), related_html)
            self.assertNotIn(escape(related.description), related_html)

    def test_a_player_present_episode_renders_no_redundant_artwork_figure(self) -> None:
        _, record = self.representative()
        response = self.client.get(record["public_path"])
        body = response.content.decode()

        self.assertNotIn('class="episode-artwork"', body)
        self.assertNotIn("episode-artwork-missing", body)

    def test_a_no_player_episode_keeps_its_only_artwork_in_the_fallback(self) -> None:
        # No catalogue record lacks a player today (verified against the full
        # projection), so this exercises the documented no-player state with a
        # synthetic record, the same way the Spotify-creator test above does.
        projection, record = self.representative()
        synthetic = {
            **record,
            "slug": "synthetic-no-player-episode",
            "public_path": "/podcast/synthetic-no-player-episode.html",
            "video": None,
            "links": {},
        }
        view = episode_view(synthetic, people_by_slug=projection["people_by_slug"])
        self.assertIsNone(view.player)

        with patch("content.catalogue.podcasts", return_value=(synthetic,)):
            response = self.client.get(synthetic["public_path"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="player-frame episode-player episode-fallback"')
        # The fallback's own artwork partial is the page's only artwork for a
        # no-player episode: either the real image or its named-missing state.
        body = response.content.decode()
        self.assertTrue(
            'class="player-art"' in body or 'class="player-art-missing mono-note"' in body
        )

    def test_episode_page_omits_unsafe_guest_paths_but_keeps_root_relative_links(self) -> None:
        projection, record = self.representative()
        safe_path = "/people/aleksandrkim.html"
        unsafe_paths = (
            "//external.example/guest",
            "/people/safe-guest.html?tab=bio",
            "/people/safe-guest.html#bio",
            "/people/safe-guest.html/extra",
            "/podcast/safe-guest.html",
            "/people/../admin",
            "/people/safe-guest.html\x00",
        )
        synthetic = {
            **record,
            "slug": "synthetic-guest-path-validation",
            "public_path": "/podcast/synthetic-guest-path-validation.html",
            "guest_profiles": [
                {"key": "", "name": "Safe Guest", "public_path": safe_path},
                *(
                    {"key": "", "name": f"Unsafe Guest {index}", "public_path": path}
                    for index, path in enumerate(unsafe_paths, start=1)
                ),
            ],
        }

        with patch("content.catalogue.podcasts", return_value=(synthetic,)):
            response = self.client.get(synthetic["public_path"])

        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{safe_path}"')
        for path in unsafe_paths:
            self.assertNotIn(f'href="{path}"', body)

    def test_spotify_creator_episode_renders_the_responsive_accessible_player(self) -> None:
        public_projection()
        source = _episode("s24e05-ai-adoption-in-enterprise-beyond-writing-code")
        creator_key = next(
            key for key in ("spotify_for_creators", "anchor") if key in source["links"]
        )
        synthetic = {
            **source,
            "slug": "synthetic-spotify-creator-player",
            "public_path": "/podcast/synthetic-spotify-creator-player.html",
            "links": {creator_key: source["links"][creator_key]},
            "video": None,
            "resources": [],
            "transcript": [],
            "guest_profiles": [],
            "guests": [],
        }

        with patch("content.catalogue.podcasts", return_value=(synthetic,)):
            response = self.client.get(synthetic["public_path"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-video-provider="spotify"')
        self.assertContains(
            response,
            'src="https://creators.spotify.com/pod/profile/datatalksclub/embed/episodes/'
            "AI-Adoption-in-Enterprise-Beyond-Writing-Code---Ivan-Bilan-e3l6h0m"
            '"',
        )
        self.assertContains(
            response,
            'title="Listen to AI Adoption in Enterprise Beyond Writing Code on Spotify"',
        )
        self.assertContains(response, "allowfullscreen")
        self.assertContains(response, "Audio unavailable.")

    def test_s24e05_youtube_player_uses_the_stored_watch_url_identity(self) -> None:
        projection = public_projection()
        source = _episode("s24e05-ai-adoption-in-enterprise-beyond-writing-code")
        view = episode_view(source, people_by_slug=projection["people_by_slug"])

        self.assertEqual(source["links"]["youtube"], "https://www.youtube.com/watch?v=XzokRd_IPSc")
        self.assertIsNotNone(view.video)
        assert view.video is not None
        self.assertEqual(view.video.video_id, "XzokRd_IPSc")
        self.assertEqual(
            view.video.embed_url,
            "https://www.youtube-nocookie.com/embed/XzokRd_IPSc?enablejsapi=1&rel=0",
        )
        response = self.client.get(source["public_path"])
        self.assertContains(response, 'data-video-provider="youtube"')
        self.assertContains(response, f'data-video-id="{view.video.video_id}"')
        self.assertContains(response, f'src="{view.video.embed_url.replace("&", "&amp;")}"')

    def test_dated_episode_keeps_exact_visible_and_structured_publication_date(self) -> None:
        projection, record = self.representative()
        dated = {
            **record,
            "slug": "synthetic-dated-episode",
            "public_path": "/podcast/synthetic-dated-episode.html",
            "published": "2026-02-03",
        }
        with patch("content.catalogue.podcasts", return_value=(dated,)):
            response = self.client.get(dated["public_path"])
        body = response.content.decode()
        self.assertContains(response, 'property="article:published_time" content="2026-02-03"')
        self.assertContains(response, 'datetime="2026-02-03"')
        match = re.search(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        entity = next(
            item
            for item in json.loads(match.group(1))["@graph"]
            if item.get("@type") == "PodcastEpisode"
        )
        self.assertEqual(entity["datePublished"], "2026-02-03")

    def test_episode_navigation_uses_real_adjacency_and_same_season_related_limit(self) -> None:
        projection, record = self.representative()
        next_record = {
            **record,
            "slug": "s24e07-synthetic-next",
            "public_path": "/podcast/s24e07-synthetic-next.html",
            "title": "Synthetic Season 24 Episode 7",
            "episode": 7,
            "published": "",
        }
        previous, following, related = episode_navigation(
            record,
            (next_record, *catalogue.podcasts()),
            people_by_slug=projection["people_by_slug"],
        )
        assert previous is not None
        assert following is not None
        self.assertEqual(previous.episode, 5)
        self.assertEqual(following.episode, 7)
        self.assertEqual(len(related), 3)
        self.assertEqual([item.episode for item in related], [7, 5, 4])
        self.assertNotIn(record["public_path"], [item.public_path for item in related])

    def test_query_detail_is_no_store_and_unsafe_methods_keep_allowlist(self) -> None:
        _, record = self.representative()
        response = self.client.get(record["public_path"] + "?utm_source=test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        post = self.client.post(record["public_path"])
        self.assertEqual(post.status_code, 405)
        self.assertEqual(post.headers["Allow"], "GET, HEAD")
        self.assertEqual(cache_directives(post), {"no-store", "max-age=0"})

    def test_optional_media_and_reading_fields_omit_empty_controls(self) -> None:
        projection, record = self.representative()
        synthetic = {
            **record,
            "slug": "synthetic-no-media",
            "public_path": "/podcast/synthetic-no-media.html",
            "title": "Synthetic episode without optional media",
            "links": {"spotify": "https://open.spotify.com/episode/synthetic"},
            "guest_profiles": [{"key": "unknown", "name": "Unknown Guest", "public_path": ""}],
            "guests": ["unknown"],
            "image_path": "/images/podcast/not-available.jpg",
            "media_available": False,
            "resources": [],
            "video": None,
            "transcript": [],
        }
        with patch("content.catalogue.podcasts", return_value=(synthetic,)):
            response = self.client.get(synthetic["public_path"])
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Video unavailable.")
        self.assertContains(response, 'href="https://open.spotify.com/episode/synthetic"')
        self.assertContains(response, 'id="show-notes-heading"')
        self.assertContains(response, "No show notes are available for this episode.")
        for heading_id in ("timestamps-heading", "transcript-heading"):
            self.assertNotIn(f'id="{heading_id}"', body)
        self.assertNotIn('id="podcast-video-player"', body)
        self.assertNotIn('href="#"', body)
        self.assertContains(response, "No portrait")
        self.assertNotIn("not-available.jpg", body)
        self.assertNotIn('property="og:image"', body)
        self.assertNotIn('name="twitter:image"', body)

    def test_valid_video_without_artwork_omits_the_redundant_artwork_and_social_image(
        self,
    ) -> None:
        projection, record = self.representative()
        synthetic = {
            **record,
            "slug": "synthetic-video-without-artwork",
            "public_path": "/podcast/synthetic-video-without-artwork.html",
            "links": {"youtube": record["links"]["youtube"]},
            "image_path": "",
            "media_available": False,
            "resources": [],
            "transcript": [],
            "guest_profiles": [],
            "guests": [],
        }
        with patch("content.catalogue.podcasts", return_value=(synthetic,)):
            response = self.client.get(synthetic["public_path"])

        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="podcast-video-player"')
        # The player already fills the visual slot artwork would take, so a
        # video-present episode with no image shows no separate artwork note.
        self.assertNotIn("Artwork unavailable.", body)
        self.assertNotIn('property="og:image"', body)
        self.assertNotIn('name="twitter:image"', body)


class PodcastSeasonNavigationTests(TestCase):
    def test_each_actual_season_contains_one_complete_season_and_all_details_once(self) -> None:
        public_projection()
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

        expected_paths = {episode["public_path"] for episode in catalogue.podcasts()}
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

    def test_detail_routes_keep_html_finals_except_reviewed_hierarchical_migrations(self) -> None:
        projection = public_projection()
        podcasts = catalogue.podcasts()
        migration = projection["editorial_route_migration"]
        podcast_finals = {
            item["final_path"] for item in migration["finals"] if item["collection"] == "podcasts"
        }
        podcast_aliases = [
            item for item in migration["aliases"] if item["collection"] == "podcasts"
        ]

        self.assertEqual({episode["public_path"] for episode in podcasts}, podcast_finals)
        self.assertTrue(all(path.startswith("/podcast/") for path in podcast_finals))
        self.assertEqual(
            {path for path in podcast_finals if not path.endswith(".html")},
            {
                PODCAST_GENAI_PILOTS_PATH,
                PODCAST_ROUTE_MIGRATION_PATH,
                PODCAST_AI_PRODUCTION_PATH,
            },
        )
        self.assertEqual(
            {item["final_path"] for item in podcast_aliases},
            podcast_finals - {PODCAST_GENAI_PILOTS_PATH},
        )

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

    def test_s24e05_uses_the_new_canonical_route_and_redirects_its_html_path(self) -> None:
        public_projection()
        episode = _episode("s24e05-ai-adoption-in-enterprise-beyond-writing-code")
        canonical = episode["public_path"]
        legacy = podcast_legacy_path(episode["slug"])
        query = "utm_source=route%2Btest&blank="

        self.assertEqual(canonical, PODCAST_ROUTE_MIGRATION_PATH)
        self.assertEqual(reverse("podcast-ai-adoption"), canonical)
        self.assertEqual(reverse("podcast-ai-adoption-legacy"), legacy)
        final = self.client.get(f"{canonical}?{query}", follow=False)
        self.assertEqual(final.status_code, 200)
        self.assertContains(
            final,
            f'<link rel="canonical" href="https://datatalks.club{canonical}">',
            count=1,
        )
        self.assertContains(
            final,
            f'<meta property="og:url" content="https://datatalks.club{canonical}">',
            count=1,
        )
        for method in ("GET", "HEAD"):
            response = self.client.generic(method, f"{legacy}?{query}", follow=False)
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response.headers["Location"], f"{canonical}?{query}")
        self.assertEqual(self.client.post(legacy).status_code, 405)

        competing_path = (
            f"/podcast/s{episode['season']:02d}e{episode['episode']:02d}/competing-title"
        )
        for method in ("GET", "HEAD"):
            response = self.client.generic(method, competing_path, follow=False)
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response.headers["Location"], canonical)
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
                # The page carries its own stylesheet (design system, issue #179), so the
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
        public_projection()
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
        records = (synthetic, *catalogue.podcasts())
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

        with patch("core.views.ordered_podcasts", return_value=ordered_podcasts(records)):
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

    def test_catalogue_renders_episode_numbers_descriptions_and_guests_without_dates(
        self,
    ) -> None:
        latest = self.client.get("/podcast")
        self.assertContains(latest, "Season 24 · Episode 6")

        oldest = self.client.get("/podcast?season=1")
        episode = _episode("data-team-roles")
        self.assertTrue(episode["description"])
        self.assertContains(oldest, str(conditional_escape(episode["description"])))
        self.assertNotContains(oldest, 'datetime="2021-02-23"')
        for guest in episode["guest_profiles"]:
            if guest["public_path"]:
                self.assertContains(oldest, f'href="{guest["public_path"]}"')
        self.assertFalse(any(not item["description"] for item in catalogue.podcasts()))
        special_description = next(
            item for item in catalogue.podcasts() if "&" in item["description"]
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
        season = next(
            season
            for season in podcast_seasons()
            if any(episode["published"] for episode in season.episodes)
        )
        body = self.client.get(f"/podcast?season={season.number}").content.decode()

        self.assertEqual(body.count('class="row-list"'), 1)
        self.assertEqual(body.count('class="play-disc"'), len(season.episodes))
        # The hub deliberately omits the optional date rail, including for a
        # season with dated episodes, so every card gets the full row width.
        self.assertEqual(body.count('class="list-row archive-row'), len(season.episodes))
        self.assertEqual(
            body.count("archive-row archive-row-undated"),
            len(season.episodes),
        )
        self.assertNotIn('class="mono-note archive-date date-rail"', body)
        self.assertNotIn(f"podcast · season {season.number}", body)
        # The catalogue has no duration and no global episode number; the design's
        # "58 min" and "#214" therefore have no stand-in on the page.
        self.assertNotIn(" min<", body)
        self.assertNotIn("#214", body)

    def test_episode_page_plays_and_lists_only_real_destinations(self) -> None:
        episode = _episode("practical-llm-engineering-and-rag")
        response = self.client.get(episode["public_path"])
        body = response.content.decode()

        self.assertContains(response, 'class="status-pill status-pill-mint"')
        self.assertContains(response, f"Season {episode['season']} · Episode {episode['episode']}")
        self.assertContains(response, 'class="player-frame episode-player episode-video"')
        self.assertContains(response, f'data-video-id="{episode["video"]["id"]}"')
        self.assertContains(response, f'href="{episode["links"]["youtube"]}"')
        # The player fills the artwork's visual slot, so a video-present episode
        # renders no separate artwork image alongside it.
        self.assertNotContains(response, f'src="{episode["image_path"]}"')
        for platform, label in (
            ("apple", "Apple Podcasts"),
            ("spotify", "Spotify"),
            ("youtube", "YouTube"),
        ):
            self.assertContains(response, f'href="{episode["links"][platform]}"')
            self.assertContains(response, label)
        self.assertNotContains(
            response,
            f'href="{episode["links"]["spotify_for_creators"]}"',
        )
        self.assertNotContains(response, "Spotify for Creators")
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
        silent = next(item for item in catalogue.podcasts() if not item.get("transcript"))
        response = self.client.get(silent["public_path"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, escape(silent["title"]))
        self.assertNotContains(response, 'id="transcript-heading"')

    def test_an_ampersand_in_an_episode_title_is_escaped_on_the_page(self) -> None:
        episode = next(item for item in catalogue.podcasts() if "&" in item["title"])
        self.assertNotEqual(escape(episode["title"]), episode["title"])
        response = self.client.get(episode["public_path"])

        self.assertContains(response, f'<h1 id="episode-heading">{escape(episode["title"])}</h1>')

    def test_an_apostrophe_in_a_guest_name_is_escaped_on_the_page(self) -> None:
        episode = _episode("devrel-data-science-open-source-tools")
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

    def test_podcast_sitemap_is_the_hub_plus_every_detail(self) -> None:
        response = self.client.get("/sitemaps/podcast.xml")
        self.assertEqual(response.status_code, 200)
        document = ElementTree.fromstring(response.content)
        locations = [node.text or "" for node in document.findall("s:url/s:loc", SITEMAP_NAMESPACE)]
        expected = {
            "https://datatalks.club/podcast",
            *(
                f"https://datatalks.club{episode['public_path']}"
                for episode in catalogue.podcasts()
            ),
        }
        self.assertEqual(set(locations), expected)
        self.assertFalse(any("?" in location for location in locations))
        self.assertFalse(any(location.endswith("/podcast.html") for location in locations))
