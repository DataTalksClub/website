"""The reviewed projection pins its own legacy target-marker provenance.

The frozen corpus-wide count moved here from the request path (audit ARC-03):
whether the accepted bootstrap still contains its known legacy target-metadata
tokens is a property of the reviewed snapshot, so the build contract asserts it
and re-cutting the projection after cleaning a biography updates the pin here,
deliberately and reviewed, instead of a reader's request refusing to render.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from scripts.projection_build.public_projection_source import (
    REVIEWED_TARGET_MARKER_COUNTS,
    _validate_marker_provenance,
)

_TOKEN = 'Read the docs {: target="blank"} today.'
_CLEAN = "A biography with no legacy tokens."


def _record(text: str, slug: str = "person") -> dict[str, Any]:
    return {"slug": slug, "blocks": [{"kind": "paragraph", "text": text}]}


def _pinned_projection() -> dict[str, Any]:
    """A projection carrying exactly the pinned counts, people tokens included."""

    people = tuple(_record(_TOKEN, slug=f"person-{index}") for index in range(10))
    return {"articles": (_record(_CLEAN, slug="article"),), "people": people}


class MarkerProvenanceValidationTests(TestCase):
    def test_a_projection_matching_the_pin_validates(self) -> None:
        _validate_marker_provenance(_pinned_projection())

    def test_drift_in_either_pinned_collection_is_refused(self) -> None:
        for name, drifted in (
            ("articles", (_record(_TOKEN, slug="article"),)),
            (
                "people",
                tuple(_record(_CLEAN, slug=f"person-{index}") for index in range(10)),
            ),
        ):
            with self.subTest(collection=name):
                projection = _pinned_projection()
                projection[name] = drifted

                with self.assertRaises(ImproperlyConfigured):
                    _validate_marker_provenance(projection)

    def test_blocks_without_text_are_ignored(self) -> None:
        projection = _pinned_projection()
        projection["people"] = (
            *projection["people"],
            {"slug": "extra", "blocks": "not-a-list"},
        )
        projection["articles"] = ({"slug": "article", "blocks": [{"kind": "image"}]},)

        _validate_marker_provenance(projection)

    def test_unpinned_collections_are_not_counted(self) -> None:
        projection = _pinned_projection()
        projection["podcasts"] = (_record(_TOKEN, slug="episode"),)

        _validate_marker_provenance(projection)

    def test_the_pin_names_the_accepted_bootstrap_counts(self) -> None:
        self.assertEqual(REVIEWED_TARGET_MARKER_COUNTS, {"articles": 0, "people": 10})
