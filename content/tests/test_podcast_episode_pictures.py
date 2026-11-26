"""Generated podcast episode pictures: stored in the database, served, and preferred."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from content import catalogue, podcast_hub
from content.models import PodcastEpisodePicture

PNG = b"\x89PNG\r\n\x1a\n" + b"picture-bytes"
SLUG = "synthetic-episode-one"


def _episode() -> dict:
    record = catalogue.podcast(SLUG)
    assert record is not None
    return record


def _store(variant: str, slug: str = SLUG) -> None:
    PodcastEpisodePicture.objects.create(episode_slug=slug, variant=variant, image=PNG)


class EpisodePictureRouteTests(TestCase):
    def test_each_stored_variant_is_served_at_its_route(self) -> None:
        for variant, route in (("card", "picture"), ("poster", "poster"), ("social", "social")):
            _store(variant)
            response = self.client.get(f"/podcast/{SLUG}/{route}")
            with self.subTest(route=route):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "image/png")
                self.assertEqual(response.content, PNG)

    def test_a_missing_picture_is_a_not_found(self) -> None:
        self.assertEqual(self.client.get(f"/podcast/{SLUG}/picture").status_code, 404)

    def test_the_route_is_read_only(self) -> None:
        _store("card")
        self.assertEqual(self.client.post(f"/podcast/{SLUG}/picture").status_code, 405)


class EpisodeCardPictureTests(TestCase):
    def test_a_card_without_an_event_banner_uses_the_generated_picture(self) -> None:
        record = {**_episode(), "image_path": "/images/podcast/x.jpg", "media_available": True}
        _store("card")
        card = podcast_hub.hub_episode(record)
        self.assertEqual(card.picture_url, f"/podcast/{SLUG}/picture")

    def test_a_card_never_falls_back_to_the_guest_portrait(self) -> None:
        card = podcast_hub.hub_episode(_episode())
        guest_images = {guest.image_path for guest in card.episode.guests if guest.image_path}
        self.assertNotIn(card.picture_url, guest_images)
        self.assertEqual(card.picture_url, "")

    def test_the_dateless_card_wins_over_the_dated_event_banner(self) -> None:
        _store("card")
        record = _episode()
        event = {
            "type": "podcast",
            "season": record["season"],
            "episode": record["episode"],
            "title": record["title"],
            "starts_at": "2026-01-05T10:00:00+00:00",
            "has_uploaded_banner": True,
            "public_id": 7,
        }
        with patch("content.podcast_hub.published_event_records", return_value=[event]):
            card = podcast_hub.hub_episode(record)
        self.assertEqual(card.picture_url, f"/podcast/{SLUG}/picture")

    def test_without_a_card_the_event_banner_is_the_fallback(self) -> None:
        record = _episode()
        event = {
            "type": "podcast",
            "season": record["season"],
            "episode": record["episode"],
            "title": record["title"],
            "starts_at": "2026-01-05T10:00:00+00:00",
            "has_uploaded_banner": True,
            "public_id": 7,
        }
        with patch("content.podcast_hub.published_event_records", return_value=[event]):
            card = podcast_hub.hub_episode(record)
        self.assertEqual(card.picture_url, "/events/7/banner")


class EpisodePagePictureTests(TestCase):
    def test_the_video_poster_and_social_image_are_the_generated_pictures(self) -> None:
        record = {**_episode(), "image_path": "/images/podcast/old.jpg", "media_available": True}
        _store("card")
        _store("poster")
        _store("social")
        with patch("content.catalogue.podcasts", return_value=(record,)):
            body = self.client.get(record["public_path"]).content.decode()
        facade = re.search(r'<a\s+id="podcast-video-facade".*?</a>', body, re.S)
        assert facade is not None
        self.assertIn(f'src="/podcast/{SLUG}/poster"', facade.group(0))
        self.assertRegex(body, rf'property="og:image" content="[^"]*/podcast/{SLUG}/social"')
        self.assertRegex(body, rf'name="twitter:image" content="[^"]*/podcast/{SLUG}/social"')
        self.assertNotIn("/images/podcast/old.jpg", body)

    def test_the_poster_is_the_pages_only_episode_picture(self) -> None:
        # Player first (owner pick "B", 2026-11-26): no square card or old
        # artwork beside the title, so the guest's face is not drawn twice.
        record = {**_episode(), "image_path": "/images/podcast/old.jpg", "media_available": True}
        _store("card")
        _store("poster")
        with patch("content.catalogue.podcasts", return_value=(record,)):
            body = self.client.get(record["public_path"]).content.decode()
        hero = body[body.index("episode-hero") : body.index("episode-read")]
        for image in re.findall(r"<img\b[^>]*>", hero):
            self.assertIn("person-chip-portrait", image)
        self.assertNotIn(f"/podcast/{SLUG}/picture", hero)
        self.assertNotIn("/images/podcast/old.jpg", hero)
        facade = re.search(r'<a\s+id="podcast-video-facade".*?</a>', body, re.S)
        assert facade is not None
        self.assertIn(f'src="/podcast/{SLUG}/poster"', facade.group(0))


class ImportEpisodePicturesTests(TestCase):
    def test_import_stores_each_file_under_its_variant_and_replays_in_place(self) -> None:
        import tempfile

        from scripts.prod.import_podcast_episode_pictures import run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (f"{SLUG}.png", f"{SLUG}-yt.png", f"{SLUG}-og.png"):
                (root / name).write_bytes(PNG)
            first = run(path=root)
            second = run(path=root)
        self.assertEqual(first["by_variant"], {"card": 1, "poster": 1, "social": 1})
        self.assertEqual((first["created"], second["created"], second["updated"]), (3, 0, 3))
        self.assertEqual(
            set(PodcastEpisodePicture.objects.values_list("variant", flat=True)),
            {"card", "poster", "social"},
        )

    def test_import_refuses_a_slug_that_is_not_a_published_episode(self) -> None:
        import tempfile

        from scripts.prod.import_podcast_episode_pictures import EpisodePictureImportFailure, run

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "no-such-episode.png").write_bytes(PNG)
            with self.assertRaises(EpisodePictureImportFailure):
                run(path=Path(directory))
        self.assertFalse(PodcastEpisodePicture.objects.exists())
