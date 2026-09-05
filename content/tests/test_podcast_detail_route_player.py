from __future__ import annotations

from typing import Any

from django.test import TestCase
from django.urls import reverse

from content import catalogue
from content.podcast_content import _spotify_creator_embed, episode_view
from content.podcast_routes import PODCAST_ROUTE_MIGRATION_PATH, podcast_legacy_path
from content.public_data import public_projection


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


class PodcastDetailRoutePlayerTests(TestCase):
    slug = "s24e05-ai-adoption-in-enterprise-beyond-writing-code"

    def setUp(self) -> None:
        self.projection = public_projection()
        self.record = _episode(self.slug)

    def test_canonical_route_and_legacy_redirect_are_consistent(self) -> None:
        legacy = podcast_legacy_path(self.slug)
        query = "utm_source=podcast-test"

        self.assertEqual(self.record["public_path"], PODCAST_ROUTE_MIGRATION_PATH)
        self.assertEqual(reverse("podcast-ai-adoption"), PODCAST_ROUTE_MIGRATION_PATH)
        self.assertEqual(reverse("podcast-ai-adoption-legacy"), legacy)
        response = self.client.get(f"{PODCAST_ROUTE_MIGRATION_PATH}?{query}", follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{PODCAST_ROUTE_MIGRATION_PATH}">',
        )
        redirect = self.client.get(f"{legacy}?{query}", follow=False)
        self.assertEqual(redirect.status_code, 301)
        self.assertEqual(redirect.headers["Location"], f"{PODCAST_ROUTE_MIGRATION_PATH}?{query}")

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
        response = self.client.get(PODCAST_ROUTE_MIGRATION_PATH)
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
