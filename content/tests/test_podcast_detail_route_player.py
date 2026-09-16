from __future__ import annotations

from typing import Any

from django.test import TestCase
from django.urls import reverse

from content import catalogue
from content.podcast_content import _spotify_creator_embed, episode_view


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


class PodcastDetailRoutePlayerTests(TestCase):
    slug = "s24e05-ai-adoption-in-enterprise-beyond-writing-code"

    def setUp(self) -> None:
        self.record = _episode(self.slug)

    def test_canonical_route_renders_the_episode_page(self) -> None:
        canonical = self.record["public_path"]

        self.assertEqual(canonical, "/podcast/s24e05/ai-adoption-in-enterprise-beyond-writing-code")
        response = self.client.get(canonical, follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{self.record["public_path"]}">',
        )
        self.assertEqual(
            reverse(
                "public-podcast-by-id",
                kwargs={
                    "episode_id": "s24e05",
                    "slug": "ai-adoption-in-enterprise-beyond-writing-code",
                },
            ),
            self.record["public_path"],
        )

    def test_youtube_player_uses_the_stored_url_identity(self) -> None:
        view = episode_view(self.record)

        self.assertEqual(
            self.record["links"]["youtube"],
            "https://www.youtube.com/watch?v=XzokRd_IPSc",
        )
        self.assertIsNotNone(view.player)
        assert view.player is not None
        self.assertEqual(view.player.media_id, "XzokRd_IPSc")
        self.assertEqual(
            view.player.embed_url,
            "https://www.youtube-nocookie.com/embed/XzokRd_IPSc?enablejsapi=1&rel=0",
        )
        response = self.client.get(self.record["public_path"])
        self.assertContains(response, 'data-video-provider="youtube"')
        self.assertContains(response, 'data-video-id="XzokRd_IPSc"')
        self.assertContains(
            response,
            'src="https://www.youtube-nocookie.com/embed/XzokRd_IPSc?enablejsapi=1&amp;rel=0"',
        )

    def test_spotify_creator_player_is_derived_from_stored_url_safely(self) -> None:
        creator_url = self.record["links"].get("anchor") or self.record["links"].get(
            "spotify_for_creators"
        )
        self.assertIsInstance(creator_url, str)
        record = {**self.record, "links": {"anchor": creator_url}, "video": None}

        view = episode_view(record)

        self.assertIsNotNone(view.player)
        assert view.player is not None
        self.assertEqual(view.player.provider, "spotify")
        self.assertEqual(
            view.player.embed_url,
            "https://creators.spotify.com/pod/profile/datatalksclub/embed/episodes/"
            "AI-Adoption-in-Enterprise-Beyond-Writing-Code---Ivan-Bilan-e3l6h0m",
        )
        self.assertIsNone(
            _spotify_creator_embed("https://evil.example/pod/profile/datatalksclub/episodes/id")
        )
        self.assertIsNone(
            _spotify_creator_embed(
                "https://creators.spotify.com/pod/profile/datatalksclub/episodes/bad/https:evil"
            )
        )
