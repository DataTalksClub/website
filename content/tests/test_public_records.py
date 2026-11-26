"""The read-model derivation the synced editorial records go through.

The synced parser records carry what their source authors; the published shape
-- the public image address, the credits -- is derived at request time. These
tests pin the derivation rules themselves, on plain records, where every input
is visible: above all the recording-lineage rule that keeps one appearance from
showing twice on a profile, which the seeded corpus is too small to exercise
end to end.
"""

from __future__ import annotations

from typing import Any

from django.test import TestCase

from content.projection_helpers import ProjectionBuildError
from content.public_records import article_record, person_record, person_relationships

_EPISODE_YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _person(slug: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "public_path": f"/people/{slug}.html",
        "title": slug.title(),
        "image_source": "images/authors/speaker.jpg",
    }


def _episode(slug: str, guests: list[str], links: dict[str, str]) -> dict[str, Any]:
    return {
        "slug": slug,
        "title": f"Episode: {slug}",
        "public_path": f"/podcast/{slug}.html",
        "guests": guests,
        "links": links,
    }


def _event(
    source_key: str,
    *,
    event_type: str = "podcast",
    links: list[dict[str, str]] | None = None,
    speakers: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "slug": source_key,
        "title": f"Event: {source_key}",
        "public_path": f"/events/1/{source_key}",
        "type": event_type,
        "links": links or [],
        "speakers": speakers or [],
        "provenance": {
            "repository": "DataTalksClub/datatalksclub.github.io",
            "revision": "0" * 40,
            "source_key": source_key,
            "checksum": "0" * 64,
        },
    }


def _relationships(
    *,
    podcasts: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    people_slugs: set[str] | None = None,
) -> dict[str, tuple[dict[str, str], ...]]:
    return person_relationships(
        (), (), tuple(podcasts or ()), tuple(events or ()), people_slugs or set()
    )


class PersonRelationshipTests(TestCase):
    def test_a_speaker_who_is_the_episodes_guest_is_credited_once(self) -> None:
        people = _relationships(
            podcasts=[_episode("the-episode", ["the-guest"], {"youtube": _EPISODE_YOUTUBE})],
            events=[
                _event(
                    "the-recording",
                    links=[{"label": "Watch recording", "url": _EPISODE_YOUTUBE}],
                    speakers=[{"key": "the-guest", "name": "The Guest"}],
                )
            ],
            people_slugs={"the-guest"},
        )

        # The recording is the episode, so the profile shows the guest credit
        # and not a second row for the same appearance.
        self.assertEqual(
            people["the-guest"],
            (
                {
                    "role": "guest",
                    "label": "Episode: the-episode",
                    "public_path": "/podcast/the-episode.html",
                },
            ),
        )

    def test_a_speaker_the_episode_does_not_credit_keeps_their_speaker_credit(self) -> None:
        people = _relationships(
            podcasts=[_episode("the-episode", ["the-guest"], {"youtube": _EPISODE_YOUTUBE})],
            events=[
                _event(
                    "the-recording",
                    links=[{"label": "Watch recording", "url": _EPISODE_YOUTUBE}],
                    speakers=[
                        {"key": "the-guest", "name": "The Guest"},
                        {"key": "the-host", "name": "The Host"},
                    ],
                )
            ],
            people_slugs={"the-guest", "the-host"},
        )

        self.assertEqual(
            people["the-host"],
            (
                {
                    "role": "speaker",
                    "label": "Event: the-recording",
                    "public_path": "/events/1/the-recording",
                },
            ),
        )

    def test_an_event_without_a_matching_recording_credits_its_speakers(self) -> None:
        people = _relationships(
            podcasts=[_episode("the-episode", [], {"youtube": _EPISODE_YOUTUBE})],
            events=[
                _event(
                    "the-webinar",
                    event_type="webinar",
                    links=[{"label": "Watch recording", "url": "https://example.invalid/other"}],
                    speakers=[{"key": "the-speaker", "name": "The Speaker"}],
                )
            ],
            people_slugs={"the-speaker"},
        )

        self.assertEqual(
            people["the-speaker"],
            (
                {
                    "role": "speaker",
                    "label": "Event: the-webinar",
                    "public_path": "/events/1/the-webinar",
                },
            ),
        )

    def test_a_recording_matching_two_episodes_is_a_refusal_not_a_guess(self) -> None:
        with self.assertRaises(ProjectionBuildError):
            _relationships(
                podcasts=[
                    _episode("episode-one", ["the-guest"], {"youtube": _EPISODE_YOUTUBE}),
                    _episode("episode-two", ["the-guest"], {"youtube": _EPISODE_YOUTUBE}),
                ],
                events=[
                    _event(
                        "the-recording",
                        links=[{"label": "Watch recording", "url": _EPISODE_YOUTUBE}],
                        speakers=[{"key": "the-guest", "name": "The Guest"}],
                    )
                ],
                people_slugs={"the-guest"},
            )

    def test_a_key_without_a_profile_credits_no_one(self) -> None:
        people = _relationships(
            events=[
                _event(
                    "the-webinar",
                    event_type="webinar",
                    speakers=[{"key": "the-unlisted", "name": "The Unlisted"}],
                )
            ],
            people_slugs={"someone-else"},
        )

        self.assertEqual(people, {"someone-else": ()})


class PersonRecordTests(TestCase):
    def test_the_portrait_is_derived_from_the_declared_image(self) -> None:
        record = person_record(_person("the-speaker"), ())

        self.assertEqual(record["image_path"], "/images/authors/speaker.jpg")
        self.assertTrue(record["media_available"])

    def test_a_profile_without_a_declared_image_publishes_none(self) -> None:
        person = _person("the-speaker")
        person["image_source"] = ""

        record = person_record(person, ())

        self.assertEqual(record["image_path"], "")
        self.assertFalse(record["media_available"])

    def test_the_roles_are_the_distinct_roles_the_credits_carry(self) -> None:
        credits = (
            {"role": "speaker", "label": "A", "public_path": "/events/1/a"},
            {"role": "guest", "label": "B", "public_path": "/podcast/b.html"},
            {"role": "speaker", "label": "C", "public_path": "/events/1/c"},
        )

        record = person_record(_person("the-speaker"), credits)

        self.assertEqual(record["roles"], ["guest", "speaker"])
        self.assertEqual(record["relationships"], credits)


class BookRowFactsTests(TestCase):
    """The two facts a book's archive row draws, derived from its own source."""

    def test_the_lead_is_the_first_paragraph_of_the_summary_as_plain_text(self) -> None:
        from content.public_records import book_lead

        summary = "Learn **tabular** ML with [XGBoost](https://x.example).\n\nSecond paragraph."
        self.assertEqual(book_lead(summary), "Learn tabular ML with XGBoost.")
        self.assertEqual(book_lead(""), "")
        self.assertEqual(book_lead(None), "")

    def test_only_threads_someone_else_replied_to_count_as_answered(self) -> None:
        from content.public_records import answered_question_count

        archive = [
            {"name": "A", "text": "q1", "replies": [{"name": "Author", "text": "a"}]},
            {"name": "B", "text": "q2", "replies": []},
            {"name": "C", "text": "q3"},
            {"name": "D", "text": "q4", "replies": [{"name": "D", "text": "follow-up"}]},
        ]
        self.assertEqual(answered_question_count(archive), 1)
        self.assertEqual(answered_question_count(None), 0)


class ArticlePreviewTests(TestCase):
    def test_preview_sources_become_public_paths(self) -> None:
        record = article_record(
            {
                "image_source": "images/posts/a/cover.png",
                "authors": [],
                "preview_source": "images/posts/a/preview.png",
                "preview_og_source": "images/posts/a/preview-og.png",
            },
            {},
        )
        self.assertEqual(record["preview_path"], "/images/posts/a/preview.png")
        self.assertEqual(record["preview_og_path"], "/images/posts/a/preview-og.png")
        self.assertEqual(record["image_path"], "/images/posts/a/cover.png")

    def test_an_article_without_a_preview_has_empty_paths(self) -> None:
        record = article_record({"image_source": "images/posts/a/cover.png", "authors": []}, {})
        self.assertEqual((record["preview_path"], record["preview_og_path"]), ("", ""))

    def test_a_generated_preview_is_the_square_thumbnail_and_its_social_card(self) -> None:
        record = article_record(
            {
                "image_source": "images/posts/a/cover.png",
                "authors": [],
                "preview_source": "images/posts/a/preview.png",
                "preview_og_source": "images/posts/a/preview-og.png",
            },
            {},
        )
        self.assertEqual(
            (record["thumbnail_path"], record["thumbnail_fit"]),
            ("/images/posts/a/preview.png", "square"),
        )
        self.assertEqual(record["social_image_path"], "/images/posts/a/preview-og.png")

    def test_the_articles_picture_is_the_thumbnail_whole_unless_square(self) -> None:
        for (width, height), fit in (
            ((1600, 900), "contain"),
            ((1000, 1000), "square"),
            ((None, None), "contain"),
        ):
            record = article_record(
                {
                    "image_source": "images/posts/a/cover.png",
                    "authors": [],
                    "card_source": "images/posts/a/first.png",
                    "card_width": width,
                    "card_height": height,
                },
                {},
            )
            with self.subTest(size=(width, height)):
                self.assertEqual(
                    (record["thumbnail_path"], record["thumbnail_fit"]),
                    ("/images/posts/a/first.png", fit),
                )
                self.assertEqual(record["social_image_path"], "/images/posts/a/first.png")
