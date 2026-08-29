from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from content.event_description_bridge import validate_description_html
from content.event_speaker_bio_normalization import (
    EventSpeakerBioNormalizationError,
    apply_event_speaker_bio_normalization,
    load_normalization_plan,
    normalize_description_html,
)

PROJECTION_ROOT = Path(__file__).parents[1] / "public_projection"


def _projection() -> tuple[list[dict], list[dict]]:
    events = json.loads((PROJECTION_ROOT / "events.json").read_text(encoding="utf-8"))
    people = json.loads((PROJECTION_ROOT / "people.json").read_text(encoding="utf-8"))
    return events, people


def test_platform_footer_variants_are_removed_without_removing_event_copy() -> None:
    source = (
        '<p class="mt-4 leading-7">Event details.</p>'
        '<p class="mt-4 leading-7"><a href="https://datatalks.club/">'
        "DataTalks.Club</a>\n is the place\tto talk about data. !</p>"
        '<p class="mt-4 leading-7">DataTalks.club is a place to talk about data.</p>'
        '<p class="mt-4 leading-7">Keep this event copy.</p>'
    )

    result = normalize_description_html(source)

    assert result.removed_speaker_bio is False
    assert result.removed_platform_boilerplate == 2
    assert result.normalized_internal_links == 1
    assert result.html == (
        '<p class="mt-4 leading-7">Event details.</p>'
        '<p class="mt-4 leading-7">Keep this event copy.</p>'
    )
    assert result.text == "Event details. Keep this event copy."


def test_bio_cleanup_preserves_content_warning_and_sponsor_copy() -> None:
    source = (
        '<p class="mt-4 leading-7">About the Speaker:</p>'
        '<p class="mt-4 leading-7">Canonical speaker biography.</p>'
        '<p class="mt-4 leading-7">Content Warning: sensitive event topic.</p>'
        '<p class="mt-4 leading-7">DataTalks.Club is the place to talk about data.</p>'
        '<p class="mt-4 leading-7">This podcast is sponsored by Example.</p>'
    )

    result = normalize_description_html(source)

    assert result.removed_speaker_bio is True
    assert result.removed_platform_boilerplate == 1
    assert "Canonical speaker biography" not in result.text
    assert "Content Warning: sensitive event topic." in result.text
    assert "This podcast is sponsored by Example." in result.text


def test_absolute_internal_links_become_root_relative_with_query_and_fragment() -> None:
    source = (
        '<p class="mt-4 leading-7">Read the '
        '<a href="https://datatalks.club/events/example?ref=event#details">recording</a>.</p>'
    )

    result = normalize_description_html(source)

    assert result.normalized_internal_links == 1
    assert (
        result.html == '<p class="mt-4 leading-7">Read the '
        '<a href="/events/example?ref=event#details">recording</a>.</p>'
    )


def test_bridge_accepts_root_relative_internal_links() -> None:
    validate_description_html(
        '<p class="mt-4 leading-7"><a class="app-link" href="/events">Events</a></p>'
    )


def test_checked_plan_replays_all_events_and_preserves_bridge_provenance() -> None:
    events, people = _projection()
    result = apply_event_speaker_bio_normalization(events, people)

    assert result == {
        "events": 421,
        "described_events": 159,
        "speaker_bio_events": 155,
        "platform_boilerplate_blocks": 136,
        "people_changed": 17,
        "conflicts": 2,
        "internal_links_normalized": 154,
    }
    assert all(
        "datatalks.club is the place to talk about data" not in event["description_text"].casefold()
        and "about the speaker" not in event["description_text"].casefold()
        and "about the guest" not in event["description_text"].casefold()
        and "speaker bio" not in event["description_text"].casefold()
        and "biography" not in event["description_text"].casefold()
        and 'href="https://datatalks.club' not in event["description_html"].casefold()
        for event in events
    )
    normalized = [event for event in events if event["description_provenance"]]
    assert len(normalized) == 159
    assert all(
        event["description_provenance"]["bridge_content_sha256"]
        == "64c54ffa59b4cb57538ec3bba1c1a0577481523323db9f76edd55cf0ca2340e3"
        and "source_description_sha256" in event["description_provenance"]
        and "normalization_original_description_sha256" in event["description_provenance"]
        and "normalized_internal_links" in event["description_provenance"]
        for event in normalized
    )


def test_checked_plan_fails_closed_on_description_source_drift() -> None:
    events, people = _projection()
    events[0] = copy.deepcopy(events[0])
    events[0]["description_html"] = "<p>unexpected source change</p>"

    with pytest.raises(EventSpeakerBioNormalizationError, match="source description drift"):
        apply_event_speaker_bio_normalization(events, people)


def test_plan_digest_and_coverage_are_checked() -> None:
    plan = load_normalization_plan()

    assert plan["counts"]["events"] == len(plan["events"]) == 421
    assert len({row["identity_id"] for row in plan["events"]}) == 421
    assert not any(row["outcome"] == "processed_unreviewed" for row in plan["events"])
