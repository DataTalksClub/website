from __future__ import annotations

import hashlib
from pathlib import Path

from django.test import TestCase

from content.podcast_content import podcast_platform_links
from content.public_data import public_projection
from scripts import build_public_projection as builder


class PodcastPlatformDataTests(TestCase):
    def test_show_platforms_use_legacy_destinations_and_canonical_provider(self) -> None:
        platforms = podcast_platform_links(public_projection()["podcast_platforms"])

        self.assertEqual(
            [(link.provider, link.label, link.url) for link in platforms],
            [
                (
                    "apple",
                    "Apple Podcasts",
                    "https://podcasts.apple.com/us/podcast/id1541710331",
                ),
                (
                    "spotify",
                    "Spotify",
                    "https://open.spotify.com/show/0pck8zuiXdI0OrCg86DAPy",
                ),
                ("youtube", "YouTube", "https://www.youtube.com/c/DataTalksClub"),
                (
                    "spotify_for_creators",
                    "Spotify for Creators",
                    "https://creators.spotify.com/pod/profile/datatalksclub/",
                ),
            ],
        )
        self.assertNotIn("Anchor", {link.label for link in platforms})

    def test_podcast_hub_renders_listener_platforms_with_decorative_icons(self) -> None:
        response = self.client.get("/podcast")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<div class="podcast-platforms" role="group" aria-label="Podcast platforms">',
        )
        listener_links = [
            link
            for link in podcast_platform_links(public_projection()["podcast_platforms"])
            if link.provider != "spotify_for_creators"
        ]
        for link in listener_links:
            link_start = body.index(f'data-podcast-platform="{link.provider}"')
            link_markup = body[link_start : body.index("</a>", link_start)]
            self.assertIn(f'href="{link.url}"', link_markup)
            self.assertIn('target="_blank"', link_markup)
            self.assertIn('rel="noopener noreferrer"', link_markup)
            self.assertIn(link.label, link_markup)
            self.assertIn(
                '<span class="sr-only"> (opens in a new tab)</span>',
                link_markup,
            )
        for link in listener_links:
            self.assertContains(
                response,
                f'class="podcast-platform-icon" data-podcast-platform-icon="{link.provider}" '
                'viewBox="0 0 24 24" aria-hidden="true" focusable="false"',
            )

    def test_platform_buttons_share_one_rendered_partial(self) -> None:
        hub = (Path(__file__).resolve().parents[2] / "templates/public/podcast_hub.html").read_text(
            encoding="utf-8"
        )
        detail = (
            Path(__file__).resolve().parents[2] / "templates/public/podcast_detail.html"
        ).read_text(encoding="utf-8")

        include = '{% include "public/_podcast_platform_button.html" with link=link only %}'
        self.assertIn(include, hub)
        self.assertIn(include, detail)
        self.assertNotIn("data-podcast-platform-icon=", hub)
        self.assertNotIn("data-podcast-platform-icon=", detail)

    def test_podcast_hub_omits_spotify_for_creators(self) -> None:
        response = self.client.get("/podcast")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-podcast-platform="spotify_for_creators"')
        self.assertNotContains(response, "Spotify for Creators")
        self.assertNotContains(
            response,
            "https://creators.spotify.com/pod/profile/datatalksclub/",
        )

    def test_episode_keeps_youtube_and_restores_creator_audio_without_creator_pill(
        self,
    ) -> None:
        episode = public_projection()["podcasts_by_slug"][
            "s24e06-how-to-build-ai-that-actually-ships-in-production"
        ]
        response = self.client.get(episode["public_path"])
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="podcast-video-player"')
        self.assertContains(
            response,
            'src="https://www.youtube-nocookie.com/embed/PosCx_4fwt0?enablejsapi=1&amp;rel=0"',
        )
        creator_embed = (
            "https://creators.spotify.com/pod/profile/datatalksclub/embed/episodes/"
            "How-to-Build-AI-that-actually-Ships-in-Production---Aleksandr-Kim-e3l6hme"
        )
        self.assertContains(response, 'id="podcast-audio-player"')
        self.assertContains(response, f'src="{creator_embed}"')
        self.assertNotContains(response, "Spotify for Creators")
        self.assertNotContains(
            response,
            'href="https://creators.spotify.com/pod/profile/datatalksclub/episodes/',
        )
        for provider in ("apple", "spotify", "youtube"):
            marker_start = body.index(f'data-podcast-platform="{provider}"')
            link_start = body.rfind("<a", 0, marker_start)
            link_markup = body[link_start : body.index("</a>", link_start)]
            self.assertIn(f'href="{episode["links"][provider]}"', link_markup)
            self.assertIn(f'data-podcast-platform="{provider}"', link_markup)
            self.assertIn(
                f'class="podcast-platform-icon" data-podcast-platform-icon="{provider}" '
                'viewBox="0 0 24 24" aria-hidden="true" focusable="false"',
                link_markup,
            )
            self.assertNotIn('class="dot ', link_markup)
            self.assertIn('target="_blank"', link_markup)
            self.assertIn('rel="noopener noreferrer"', link_markup)
            self.assertIn(
                '<span class="sr-only"> (opens in a new tab)</span>',
                link_markup,
            )

    def test_platform_artifact_is_manifest_bound_and_provider_keys_cannot_drift(self) -> None:
        root = Path(__file__).resolve().parents[1] / "public_projection"
        path = root / "podcast_platforms.json"
        projection = public_projection()

        self.assertEqual(
            projection["manifest"]["artifacts"]["podcast_platforms.json"],
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            {item["provider"] for item in projection["podcast_platforms"]},
            {"apple", "spotify", "youtube", "spotify_for_creators"},
        )
        self.assertEqual(
            [item["key"] for item in projection["podcast_platforms"]],
            [item["provider"] for item in projection["podcast_platforms"]],
        )
        self.assertTrue(
            all(item["title"] == item["label"] for item in projection["podcast_platforms"])
        )
        self.assertTrue(
            all("anchor" not in record.get("links", {}) for record in projection["podcasts"])
        )

    def test_pinned_source_anchor_links_are_canonicalized_at_projection_boundary(self) -> None:
        self.assertEqual(
            builder._canonical_podcast_platform_key("anchor"),
            "spotify_for_creators",
        )
        self.assertEqual(
            builder._canonical_podcast_platform_url(
                "spotify_for_creators",
                "https://anchor.fm/datatalksclub/episodes/example-e1",
            ),
            "https://creators.spotify.com/pod/profile/datatalksclub/episodes/example-e1",
        )
