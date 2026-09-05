"""Building staged descriptions for events the reviewed corpus cannot reach.

The corpus of 421 is frozen and the bridge that described it matches on a legacy
tuple a discovered event does not have, so these records are built a second way.
These tests pin what that build refuses and what it never decides on its own: an
event's type, and whether a link destination may be published.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from scripts.staging.luma_event_descriptions import (
    REVIEWED_TYPE_INPUT_PATH,
    LumaDescriptionError,
    build_artifact,
    build_record,
    discover_description_exports,
    load_reviewed_event_types,
    render_and_normalize,
    unreviewed_link_destinations,
    validate_artifact,
)

STEM = "2026-08-10_a-synthetic-workshop_evt-synthetic01"
EVENT_IDENTIFIER = "evt-Synthetic01"
IDENTITY = "8f2b1c5e-0d3a-4a6b-9c7d-1e2f3a4b5c6d"
# A value that must never leave the checkpoint. The reader locates the three
# event fields by position and decodes only those, so this is never parsed.
GUEST_CANARY = "synthetic-guest-canary@example.invalid"

DESCRIPTION = """**A synthetic workshop**

About the event

We will build the thing, end to end.

About the Speaker

Ada has spent twenty years building things.

DataTalks.Club is the place to talk about data.
"""


def _checkpoint(
    *,
    event_identifier: str = EVENT_IDENTIFIER,
    name: str = "A synthetic workshop",
    start_at: str | None = "2026-08-10T15:00:00.000Z",
) -> str:
    event: dict[str, object] = {"id": event_identifier, "name": name}
    if start_at is not None:
        event["start_at"] = start_at
    return json.dumps(
        {
            "schema_version": 1,
            "event": event,
            "guests": [{"user_email": GUEST_CANARY, "approval_status": "approved"}],
        }
    )


class DescriptionExportReadingTests(SimpleTestCase):
    """One .md plus one _json checkpoint is one event, or it is nothing."""

    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temporary.name)
        (self.root / "descriptions").mkdir()
        (self.root / "_json").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(
        self, stem: str = STEM, *, checkpoint: str | None = None, markdown: str = "Copy."
    ) -> None:
        (self.root / "descriptions" / f"{stem}.md").write_text(markdown, encoding="utf-8")
        if checkpoint is not None:
            (self.root / "_json" / f"{stem}.json").write_text(checkpoint, encoding="utf-8")

    def test_a_pair_carries_the_identifier_and_the_start_the_export_states(self) -> None:
        self._write(checkpoint=_checkpoint())

        (export,) = discover_description_exports(self.root)

        self.assertEqual(export.date, "2026-08-10")
        self.assertEqual(export.slug, "a-synthetic-workshop")
        # The case the provider spells it in, not the lower-cased file name: this
        # is the source_key the identity was minted under.
        self.assertEqual(export.external_event_identifier, EVENT_IDENTIFIER)
        self.assertEqual(export.starts_at, "2026-08-10T15:00:00+00:00")

    def test_the_guest_list_is_never_decoded(self) -> None:
        self._write(checkpoint=_checkpoint())

        (export,) = discover_description_exports(self.root)

        self.assertNotIn(GUEST_CANARY, repr(export))

    def test_a_file_name_outside_the_convention_is_refused(self) -> None:
        self._write("august-2026-a-synthetic-workshop", checkpoint=_checkpoint())

        with self.assertRaises(LumaDescriptionError) as refusal:
            discover_description_exports(self.root)

        self.assertEqual(str(refusal.exception), "luma_description_name_unrecognised")

    def test_a_description_with_no_checkpoint_beside_it_is_refused(self) -> None:
        self._write()

        with self.assertRaises(LumaDescriptionError) as refusal:
            discover_description_exports(self.root)

        self.assertEqual(str(refusal.exception), "luma_description_checkpoint_missing")

    def test_a_checkpoint_describing_another_event_is_refused(self) -> None:
        self._write(checkpoint=_checkpoint(event_identifier="evt-SomethingElse"))

        with self.assertRaises(LumaDescriptionError) as refusal:
            discover_description_exports(self.root)

        self.assertEqual(str(refusal.exception), "luma_description_pair_mismatch")

    def test_a_checkpoint_with_no_start_is_refused_rather_than_defaulted(self) -> None:
        """Nothing invents a start time; an export that does not state one stops."""

        self._write(checkpoint=_checkpoint(start_at=None))

        with self.assertRaises(LumaDescriptionError) as refusal:
            discover_description_exports(self.root)

        self.assertEqual(str(refusal.exception), "luma_checkpoint_field_missing")

    def test_a_naive_start_is_refused(self) -> None:
        self._write(checkpoint=_checkpoint(start_at="2026-08-10T15:00:00"))

        with self.assertRaises(LumaDescriptionError) as refusal:
            discover_description_exports(self.root)

        self.assertEqual(str(refusal.exception), "luma_checkpoint_start_not_aware")

    def test_an_empty_description_is_refused(self) -> None:
        self._write(checkpoint=_checkpoint(), markdown="   \n")

        with self.assertRaises(LumaDescriptionError) as refusal:
            discover_description_exports(self.root)

        self.assertEqual(str(refusal.exception), "luma_description_empty")


class ReviewedEventTypeTests(SimpleTestCase):
    """A type is a person's decision, read from a file, never derived from a title."""

    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.path = Path(self.temporary.name) / "event-type-input.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, entries: list[dict[str, object]], *, revision: int = 1) -> None:
        self.path.write_text(
            json.dumps({"schema_version": 1, "review_revision": revision, "events": entries}),
            encoding="utf-8",
        )

    def test_an_entry_decides_one_description_file(self) -> None:
        self._write(
            [
                {
                    "description_file": f"{STEM}.md",
                    "type": "workshop",
                    "reason": "Hands-on session run by the speaker.",
                }
            ],
            revision=4,
        )

        reviewed = load_reviewed_event_types(self.path)

        self.assertEqual(reviewed.review_revision, 4)
        self.assertEqual(reviewed.entries[f"{STEM}.md"].type, "workshop")

    def test_a_type_outside_the_four_is_refused(self) -> None:
        self._write(
            [{"description_file": f"{STEM}.md", "type": "meetup", "reason": "Looks like one."}]
        )

        with self.assertRaises(LumaDescriptionError) as refusal:
            load_reviewed_event_types(self.path)

        self.assertEqual(str(refusal.exception), "luma_type_input_type_invalid")

    def test_an_entry_with_no_reason_is_refused(self) -> None:
        self._write([{"description_file": f"{STEM}.md", "type": "webinar", "reason": "  "}])

        with self.assertRaises(LumaDescriptionError) as refusal:
            load_reviewed_event_types(self.path)

        self.assertEqual(str(refusal.exception), "luma_type_input_reason_invalid")

    def test_the_checked_in_file_decides_nothing_yet(self) -> None:
        """It ships empty on purpose: nobody has reviewed a type for these events."""

        reviewed = load_reviewed_event_types(REVIEWED_TYPE_INPUT_PATH)

        self.assertEqual(reviewed.entries, {})


class DescriptionRenderingTests(SimpleTestCase):
    """The reviewed policies, applied to a description the bridge never saw."""

    def test_the_speaker_biography_and_the_footer_are_removed(self) -> None:
        result = render_and_normalize(DESCRIPTION)

        self.assertTrue(result["removed_speaker_bio"])
        self.assertEqual(result["removed_platform_boilerplate"], 1)
        self.assertIn("We will build the thing", result["description_text"])
        self.assertNotIn("twenty years", result["description_text"])
        self.assertNotIn("place to talk about data", result["description_text"])

    def test_a_destination_no_one_has_reviewed_is_named_by_its_url(self) -> None:
        """Naming it is the whole point: approving one is an edit to the link policy."""

        markdown = "Details at [our host](https://not-a-reviewed-host.example/talk).\n"

        (unreviewed,) = unreviewed_link_destinations(markdown)

        self.assertEqual(unreviewed.url, "https://not-a-reviewed-host.example/talk")
        self.assertEqual(unreviewed.reason, "description URL has no reviewed decision")

    def test_a_reviewed_host_with_an_unreviewed_destination_is_still_stopped(self) -> None:
        """Host approval alone is deliberately not enough."""

        markdown = "See [the repo](https://github.com/DataTalksClub/not-reviewed-yet).\n"

        (unreviewed,) = unreviewed_link_destinations(markdown)

        self.assertEqual(unreviewed.reason, "description rendered link is not reviewed")

    def test_a_reviewed_destination_passes(self) -> None:
        markdown = "See [the repo](https://github.com/DataTalksClub/llm-zoomcamp).\n"

        self.assertEqual(unreviewed_link_destinations(markdown), ())

    def test_rendering_refuses_the_description_an_unreviewed_link_appears_in(self) -> None:
        markdown = "Details at [our host](https://not-a-reviewed-host.example/talk).\n"

        with self.assertRaises(LumaDescriptionError) as refusal:
            render_and_normalize(markdown)

        self.assertTrue(str(refusal.exception).startswith("luma_description_render_refused"))


class ArtifactTests(SimpleTestCase):
    """What the builder hands over, and what the receiving end can check about it."""

    def _record(self) -> dict:
        from scripts.staging.luma_event_descriptions import DescriptionExport, ReviewedEventType

        export = DescriptionExport(
            stem=STEM,
            path=Path(f"{STEM}.md"),
            date="2026-08-10",
            slug="a-synthetic-workshop",
            external_event_identifier=EVENT_IDENTIFIER,
            title="A synthetic workshop",
            starts_at="2026-08-10T15:00:00+00:00",
            markdown=DESCRIPTION,
        )
        return build_record(
            export,
            identity_id=IDENTITY,
            source_repository="dtc-historical-source/luma",
            source_revision="luma-aggregate-v1",
            reviewed_type=ReviewedEventType(
                description_file=f"{STEM}.md",
                type="workshop",
                reason="Hands-on session run by the speaker.",
            ),
            review_revision=1,
        )

    def test_a_record_carries_the_reviewed_type_and_the_exported_start(self) -> None:
        record = self._record()

        self.assertEqual(record["type"], "workshop")
        self.assertEqual(record["starts_at"], "2026-08-10T15:00:00+00:00")
        # Luma derives end_at from a nominal duration, so it is never stated here.
        self.assertEqual(record["ends_at"], "")
        self.assertEqual(record["type_provenance"]["review_revision"], 1)
        self.assertEqual(
            record["provenance"],
            {
                "repository": "dtc-historical-source/luma",
                "revision": "luma-aggregate-v1",
                "source_key": EVENT_IDENTIFIER,
            },
        )

    def test_a_tampered_artifact_fails_its_own_digest(self) -> None:
        artifact = build_artifact([self._record()])
        artifact["events"][0]["description_html"] = "<p>Something else.</p>"

        with self.assertRaises(LumaDescriptionError) as refusal:
            validate_artifact(artifact)

        self.assertEqual(str(refusal.exception), "luma_artifact_digest_mismatch")

    def test_the_importer_accepts_what_the_builder_produces(self) -> None:
        """The two ends agree on the shape, so neither can drift alone."""

        from events.content_import import parse_new_event_content

        (record,) = parse_new_event_content(build_artifact([self._record()]))

        self.assertEqual(str(record.identity_id), IDENTITY)
        self.assertEqual(record.type, "workshop")
        self.assertEqual(record.provenance.source_key, EVENT_IDENTIFIER)


class StagedContentImportLegTests(TestCase):
    """The orchestrator's leg. Landing the records is covered in ``events.tests``."""

    def test_an_absent_artifact_is_reported_rather_than_failing(self) -> None:
        """It exists only while there is content waiting, which is most of the time not."""

        from scripts.prod.import_events import import_new_content

        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            report = import_new_content(source=Path(directory) / "not-built-yet.json")

        self.assertEqual(report, {"present": False, "applied": True})
