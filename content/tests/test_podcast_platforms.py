from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

from django.test import TestCase

from content import catalogue
from content import projection_helpers as builder
from content.podcast_content import podcast_platform_links


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


class PodcastPlatformDataTests(TestCase):
    def test_show_platforms_use_legacy_destinations_and_canonical_provider(self) -> None:
        platforms = podcast_platform_links(catalogue.podcast_platforms())

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
            "<span>Follow the show on</span>",
        )
        self.assertContains(response, '<p class="podcast-follow">', count=1)
        listener_links = [
            link
            for link in podcast_platform_links(catalogue.podcast_platforms())
            if link.provider != "spotify_for_creators"
        ]
        for link in listener_links:
            marker = body.index(f'data-podcast-platform="{link.provider}"')
            link_start = body.rindex("<a ", 0, marker)
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

        # The hub's follow line and every card's "Listen on" row draw the mark
        # through the shared icon partial; the episode page names its other
        # platforms as one line of text links under the player.
        icon = '{% include "public/_podcast_platform_icon.html" with provider=link.provider'
        self.assertIn(icon, hub)
        self.assertIn(
            icon,
            (
                Path(__file__).resolve().parents[2] / "templates/public/_podcast_episode_card.html"
            ).read_text(encoding="utf-8"),
        )
        self.assertIn("{% for link in episode.listener_platform_links %}", detail)
        self.assertNotIn("pill-button", detail)
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

    def test_episode_keeps_youtube_and_links_open_spotify_without_creator_link(
        self,
    ) -> None:
        """The poster plays YouTube; Spotify is a link, never a second player.

        Under a YouTube poster a Spotify frame is one more slab that shows
        nothing until it loads (owner pick "B", 2026-11-26), so the page links
        Spotify instead.  Where Spotify *is* the player, the
        ``open.spotify.com`` embed is preferred over the creator dashboard's:

        ``creators.spotify.com`` declares ``frame-ancestors https://creators.spotify.com``
        on the internal auth frame its player bootstraps, so a real browser refuses to
        load it from any third-party parent -- including this one -- and the audio
        player renders as a blank box.  This episode has both a ``spotify`` (open)
        link and a ``spotify_for_creators`` link; the open one is the link shown,
        and nothing from the creator dashboard is.
        """

        episode = _episode("s24e06-how-to-build-ai-that-actually-ships-in-production")
        response = self.client.get(episode["public_path"])
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # The YouTube player is a click-to-play poster: the embed address rides on
        # the frame for the script to build, and no YouTube frame loads with the page.
        self.assertContains(response, 'id="podcast-video-facade"')
        self.assertContains(
            response,
            'data-video-embed="https://www.youtube-nocookie.com/embed/PosCx_4fwt0'
            '?enablejsapi=1&amp;rel=0"',
        )
        self.assertNotContains(response, 'src="https://www.youtube-nocookie.com/')
        self.assertNotContains(response, 'id="podcast-audio-player"')
        self.assertNotContains(response, "open.spotify.com/embed/")
        self.assertNotContains(response, "Spotify for Creators")
        self.assertNotContains(
            response,
            'href="https://creators.spotify.com/pod/profile/datatalksclub/episodes/',
        )
        self.assertNotContains(response, "creators.spotify.com/pod/profile/datatalksclub/embed/")
        line_start = body.index("data-platform-links")
        line = body[line_start : body.index("</p>", line_start)]
        for provider in ("apple", "spotify", "youtube"):
            marker_start = line.index(f'data-podcast-platform="{provider}"')
            link_start = line.rfind("<a", 0, marker_start)
            link_markup = line[link_start : line.index("</a>", link_start)]
            self.assertIn(f'href="{episode["links"][provider]}"', link_markup)
            self.assertIn('target="_blank"', link_markup)
            self.assertIn('rel="noopener noreferrer"', link_markup)
            self.assertIn(
                '<span class="sr-only"> (opens in a new tab)</span>',
                link_markup,
            )

    @unittest.skipUnless(
        (Path.home() / "prod" / "dtc-data" / "content-staging" / "public_projection").exists(),
        "the reviewed public projection lives outside this checkout, at "
        "~/prod/dtc-data/content-staging/ (see "
        "_docs/architecture/database-only-content.md)",
    )
    def test_platform_artifact_is_manifest_bound_and_provider_keys_cannot_drift(self) -> None:
        """Checked against the reviewed files on disk, not the Django test DB.

        Django tests always seed from the small synthetic fixture now (see
        `test_support/reference_data.py`), regardless of whether the real
        reviewed projection happens to exist on this machine, so this can no
        longer read `catalogue.podcast_platforms()`/`catalogue.podcasts()` --
        that would just check the synthetic fixture against itself. This
        reads the real projection files directly instead, which is what this
        check actually cares about.
        """

        root = Path.home() / "prod" / "dtc-data" / "content-staging" / "public_projection"
        path = root / "podcast_platforms.json"
        manifest = json.loads((root / "manifest.json").read_text())
        platforms = json.loads(path.read_text())
        podcasts = json.loads((root / "podcasts.json").read_text())

        self.assertEqual(
            manifest["artifacts"]["podcast_platforms.json"],
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            {item["provider"] for item in platforms},
            {"apple", "spotify", "youtube", "spotify_for_creators"},
        )
        self.assertEqual(
            [item["key"] for item in platforms],
            [item["provider"] for item in platforms],
        )
        self.assertTrue(all(item["title"] == item["label"] for item in platforms))
        self.assertTrue(all("anchor" not in record.get("links", {}) for record in podcasts))

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
