"""Cleaning scraped Eventbrite content, and letting it win over the Jekyll description.

Two halves: the pure text transforms (:func:`clean_description_markdown` and
its renderers), which need no database, and :func:`apply_eventbrite_descriptions`,
which does. The product owner's ruling is the contract these pin: "eventbrite
wins over jekyll. but we remove 'about the speaker' part and about dtc footer
too."
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from django.test import TestCase

from events.eventbrite_content import (
    DESCRIPTION_RECORD_SCHEMA_VERSION,
    EventbriteDescriptionError,
    apply_eventbrite_descriptions,
    clean_description_markdown,
    parse_eventbrite_description_records,
    render_description_html,
    render_description_text,
)
from events.models import Event, EventContent

STARTS_AT = datetime(2021, 6, 1, 17, 0, tzinfo=UTC)


class CleanDescriptionMarkdownTests(TestCase):
    def test_strips_speaker_bio_and_footer(self) -> None:
        markdown = (
            "We will talk about deploying models.\n\n"
            "About the speaker:\n\n"
            "Dmitry is a Lead Data Scientist.\n\n"
            "[DataTalks.Club](https://DataTalks.Club) is a place to talk about data. "
            "Join our slack community!"
        )
        self.assertEqual(
            clean_description_markdown(markdown), "We will talk about deploying models."
        )

    def test_strips_guest_and_host_variants(self) -> None:
        markdown = (
            "Outline:\n\n"
            "- Alexey and his career\n\n"
            "About the host:\n\n"
            "Eugene bio paragraph.\n\n"
            "About the guest:\n\n"
            "Alexey bio paragraph.\n\n"
            "[DataTalks.Club](https://DataTalks.Club) is the place to talk about data. "
            "[Join our slack community](https://datatalks.club/slack.html)!"
        )
        cleaned = clean_description_markdown(markdown)
        self.assertEqual(cleaned, "Outline:\n\n- Alexey and his career")
        self.assertNotIn("About the host", cleaned)
        self.assertNotIn("About the guest", cleaned)

    def test_keeps_real_content_after_the_footer(self) -> None:
        markdown = (
            "Workshop details.\n\n"
            "About the speaker:\n\n"
            "Bio.\n\n"
            "[DataTalks.Club](https://DataTalks.Club) is the place to talk about data. "
            "[Join our slack community](https://datatalks.club/slack.html)!\n\n"
            "This event is sponsored by [Iterative.ai](https://iterative.ai)."
        )
        cleaned = clean_description_markdown(markdown)
        self.assertEqual(
            cleaned,
            "Workshop details.\n\nThis event is sponsored by [Iterative.ai](https://iterative.ai).",
        )

    def test_keeps_real_mentions_of_datatalks_club_that_are_not_the_footer(self) -> None:
        markdown = (
            "Outline:\n\n"
            "- DataTalks.Club and how it started\n\n"
            "- Machine Learning Bookcamp"
        )
        self.assertEqual(clean_description_markdown(markdown), markdown)

    def test_handles_footer_variants(self) -> None:
        variants = [
            "[DataTalks.Club](https://DataTalks.Club) is a place to talk about data. "
            "Join our slack community!",
            "[DataTalks.Club](https://DataTalks.Club) is the place to talk about data. "
            "[Join our slack community](https://datatalks.club/slack.html)",
            "[DataTalks.Club](https://datatalks.club/) is the place to talk about data. "
            "[Join our slack community](https://datatalks.club/slack.html)!",
        ]
        for footer in variants:
            markdown = f"Real content.\n\n{footer}"
            self.assertEqual(clean_description_markdown(markdown), "Real content.")

    def test_zero_width_characters_do_not_defeat_the_heading_match(self) -> None:
        markdown = (
            "Workshop content.\n\n"
            "A﻿bout the speaker\n\n"
            "Bio paragraph.\n\n"
            "[DataTalks.Club](https://datatalks.club/) is the place to talk about data. "
            "[Join our slack community](https://datatalks.club/slack.html)!"
        )
        self.assertEqual(clean_description_markdown(markdown), "Workshop content.")

    def test_no_about_section_leaves_footer_only_stripped(self) -> None:
        markdown = (
            "Day 2 of the marathon.\n\n"
            "[DataTalks.Club](https://DataTalks.Club) is the place to talk about data. "
            "[Join our slack community](https://datatalks.club/slack.html)!"
        )
        self.assertEqual(clean_description_markdown(markdown), "Day 2 of the marathon.")

    def test_no_footer_strips_to_end_of_text(self) -> None:
        markdown = "Real content.\n\nAbout the speaker:\n\nBio paragraph with no footer."
        self.assertEqual(clean_description_markdown(markdown), "Real content.")

    def test_empty_input_is_returned_unchanged(self) -> None:
        self.assertEqual(clean_description_markdown(""), "")


class RenderDescriptionTests(TestCase):
    def test_text_and_html_derive_from_the_same_cleaned_markdown(self) -> None:
        markdown = "We'll cover:\n\n- Item one\n- Item two\n\n[a link](https://example.com)."
        text = render_description_text(markdown)
        html = render_description_html(markdown)
        self.assertIn("Item one; Item two.", text)
        self.assertNotIn("[", text)
        self.assertNotIn("](", text)
        self.assertIn('<ul class="mt-4 list-disc pl-6">', html)
        self.assertIn('<a class="app-link" href="https://example.com"', html)

    def test_empty_markdown_renders_empty(self) -> None:
        self.assertEqual(render_description_text(""), "")
        self.assertEqual(render_description_html(""), "")


class ParseEventbriteDescriptionRecordsTests(TestCase):
    def _payload(self, **overrides: object) -> dict[str, object]:
        payload = {
            "schema_version": DESCRIPTION_RECORD_SCHEMA_VERSION,
            "generated_at": "2026-09-11T00:00:00+00:00",
            "source": {},
            "events": [
                {
                    "eventbrite_event_id": "127017208891",
                    "canonical_repository": "DataTalksClub/datatalksclub.github.io",
                    "canonical_revision": "a" * 40,
                    "canonical_source_key": "2020-11-10-example",
                    "description_html": "<p>Cleaned.</p>",
                    "description_text": "Cleaned.",
                }
            ],
        }
        payload.update(overrides)
        return payload

    def test_valid_payload_parses(self) -> None:
        records = parse_eventbrite_description_records(self._payload())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].eventbrite_event_id, "127017208891")

    def test_wrong_schema_version_is_refused(self) -> None:
        with self.assertRaises(EventbriteDescriptionError):
            parse_eventbrite_description_records(self._payload(schema_version=999))

    def test_empty_description_is_allowed(self) -> None:
        payload = self._payload()
        payload["events"][0]["description_html"] = ""
        payload["events"][0]["description_text"] = ""
        records = parse_eventbrite_description_records(payload)
        self.assertEqual(records[0].description_text, "")


def _event(*, source_key: str, repository: str = "DataTalksClub/datatalksclub.github.io") -> Event:
    event = Event(
        id=uuid.uuid4(),
        title="Example event",
        slug="example-event",
        source_repository=repository,
        source_revision="a" * 40,
        source_key=source_key,
    )
    event._allow_public_id_assignment = True
    event.public_id = 5_001
    event.save()
    return event


class ApplyEventbriteDescriptionsTests(TestCase):
    def _write_artifact(self, tmp_path: Path, events: list[dict[str, object]]) -> Path:
        path = tmp_path / "eventbrite_descriptions.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": DESCRIPTION_RECORD_SCHEMA_VERSION,
                    "generated_at": "2026-09-11T00:00:00+00:00",
                    "source": {},
                    "events": events,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _record(self, event: Event, **overrides: object) -> dict[str, object]:
        record = {
            "eventbrite_event_id": "127017208891",
            "canonical_repository": event.source_repository,
            "canonical_revision": event.source_revision,
            "canonical_source_key": event.source_key,
            "description_html": "<p>Cleaned Eventbrite text.</p>",
            "description_text": "Cleaned Eventbrite text.",
        }
        record.update(overrides)
        return record

    def test_replaces_an_existing_jekyll_description_outright(self) -> None:
        import tempfile

        event = _event(source_key="2020-11-10-example")
        EventContent.objects.create(
            event=event,
            type=EventContent.Type.WEBINAR,
            starts_at=STARTS_AT,
            description_html="<p>Old Jekyll text.</p>",
            description_text="Old Jekyll text.",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_artifact(Path(directory), [self._record(event)])
            report = apply_eventbrite_descriptions(path=path)

        self.assertEqual(report.applied, 1)
        self.assertEqual(report.unchanged, 0)
        content = EventContent.objects.get(event=event)
        self.assertEqual(content.description_text, "Cleaned Eventbrite text.")
        self.assertEqual(content.description_html, "<p>Cleaned Eventbrite text.</p>")

    def test_dry_run_reports_without_writing(self) -> None:
        import tempfile

        event = _event(source_key="2020-11-10-example")
        EventContent.objects.create(
            event=event,
            type=EventContent.Type.WEBINAR,
            starts_at=STARTS_AT,
            description_html="<p>Old Jekyll text.</p>",
            description_text="Old Jekyll text.",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_artifact(Path(directory), [self._record(event)])
            report = apply_eventbrite_descriptions(path=path, dry_run=True)

        self.assertEqual(report.applied, 1)
        content = EventContent.objects.get(event=event)
        self.assertEqual(content.description_text, "Old Jekyll text.")

    def test_unresolved_eventbrite_id_is_reported_not_guessed(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            record = {
                "eventbrite_event_id": "999999999999",
                "canonical_repository": "DataTalksClub/datatalksclub.github.io",
                "canonical_revision": "a" * 40,
                "canonical_source_key": "no-such-event",
                "description_html": "<p>x</p>",
                "description_text": "x",
            }
            path = self._write_artifact(Path(directory), [record])
            report = apply_eventbrite_descriptions(path=path)

        self.assertEqual(report.no_identity, 1)
        self.assertEqual(report.applied, 0)

    def test_event_with_no_content_row_yet_is_reported_not_created(self) -> None:
        import tempfile

        event = _event(source_key="2020-11-10-example")
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_artifact(Path(directory), [self._record(event)])
            report = apply_eventbrite_descriptions(path=path)

        self.assertEqual(report.no_content_yet, 1)
        self.assertFalse(EventContent.objects.filter(event=event).exists())

    def test_empty_eventbrite_description_does_not_blank_a_real_jekyll_one(self) -> None:
        import tempfile

        event = _event(source_key="2020-11-10-example")
        EventContent.objects.create(
            event=event,
            type=EventContent.Type.WEBINAR,
            starts_at=STARTS_AT,
            description_html="<p>Real Jekyll text.</p>",
            description_text="Real Jekyll text.",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_artifact(
                Path(directory),
                [self._record(event, description_html="", description_text="")],
            )
            report = apply_eventbrite_descriptions(path=path)

        self.assertEqual(report.no_eventbrite_description, 1)
        self.assertEqual(report.applied, 0)
        content = EventContent.objects.get(event=event)
        self.assertEqual(content.description_text, "Real Jekyll text.")

    def test_replaying_an_already_applied_artifact_is_a_no_op(self) -> None:
        import tempfile

        event = _event(source_key="2020-11-10-example")
        EventContent.objects.create(
            event=event,
            type=EventContent.Type.WEBINAR,
            starts_at=STARTS_AT,
            description_html="<p>Cleaned Eventbrite text.</p>",
            description_text="Cleaned Eventbrite text.",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_artifact(Path(directory), [self._record(event)])
            report = apply_eventbrite_descriptions(path=path)

        self.assertEqual(report.applied, 0)
        self.assertEqual(report.unchanged, 1)
