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

    def test_podcast_hub_renders_each_platform_from_the_projection(self) -> None:
        response = self.client.get("/podcast")

        self.assertEqual(response.status_code, 200)
        for link in podcast_platform_links(public_projection()["podcast_platforms"]):
            self.assertContains(response, f'data-podcast-platform="{link.provider}"')
            self.assertContains(response, f'href="{link.url}"')
            self.assertContains(response, link.label)
            self.assertContains(response, 'rel="noopener noreferrer"')

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
