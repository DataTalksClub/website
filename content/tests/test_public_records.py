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

from content.public_records import person_record, person_relationships
from scripts.build_public_projection import ProjectionBuildError

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
