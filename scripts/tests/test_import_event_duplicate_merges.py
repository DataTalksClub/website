"""The duplicate-merge entry point: a typed, reviewed, dry-run-first command."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from django.test import SimpleTestCase, TestCase

import scripts.prod
from events.identity import create_event_identity
from scripts.prod import import_event_duplicate_merges as entry_point
from test_support.event_pictures import TEST_EVENT_PICTURE


class DuplicateMergeEntryPointTests(SimpleTestCase):
    def test_pairs_and_a_target_are_required_and_apply_is_opt_in(self) -> None:
        parser = entry_point._parser()

        with self.assertRaises(SystemExit):
            entry_point.main(["--database", ".tmp/x.sqlite3"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["--pair", "150:422"])
        args = parser.parse_args(["--database", ".tmp/x.sqlite3", "--pair", "150:422"])
        self.assertFalse(args.apply)
        self.assertFalse(args.allow_date_mismatch)
        self.assertFalse(args.start_from_duplicate)
        self.assertEqual(args.max_days_apart, 1)
        self.assertEqual(args.pairs, ["150:422"])

    def test_it_is_a_typed_operator_command_never_part_of_a_rebuild(self) -> None:
        self.assertEqual(entry_point.SYNC_MODEL, "one-time")
        self.assertFalse(entry_point.BOOTSTRAPS_EMPTY_DATABASE)
        self.assertIn("import_event_duplicate_merges", scripts.prod.SCRIPT_COMMAND_EXCLUSIONS)


class PairsFileTests(TestCase):
    """A reviewed pairs file: row ids, each guarded by the row's content UUID."""

    def setUp(self) -> None:
        self.keep = create_event_identity(
            picture=TEST_EVENT_PICTURE,
            title="Kept",
            event_type="webinar",
            speakers=[("janedoe", "Jane Doe")],
            description="Kept.",
        )
        self.duplicate = create_event_identity(
            picture=TEST_EVENT_PICTURE,
            title="Duplicate",
            event_type="webinar",
            speakers=[("janedoe", "Jane Doe")],
            description="Duplicate.",
        )
        self.path = Path(tempfile.mkdtemp()) / "pairs.json"

    def _write(self, pairs: list[dict]) -> Path:
        self.path.write_text(json.dumps({"schema_version": 1, "pairs": pairs}), encoding="utf-8")
        return self.path

    def _pair(self, **overrides: object) -> dict:
        pair = {
            "keep_id": self.keep.pk,
            "duplicate_id": self.duplicate.pk,
            "keep_content_id": str(self.keep.content_id),
            "duplicate_content_id": str(self.duplicate.content_id),
        }
        pair.update(overrides)
        return pair

    def test_matching_rows_become_pair_values(self) -> None:
        values = entry_point.pairs_from_file(self._write([self._pair()]))

        self.assertEqual(values, [f"{self.keep.pk}:{self.duplicate.pk}"])

    def test_a_row_carrying_another_uuid_is_refused(self) -> None:
        path = self._write([self._pair(duplicate_content_id=str(uuid.uuid4()))])

        with self.assertRaisesMessage(ValueError, "pairs_file_content_id_mismatch"):
            entry_point.pairs_from_file(path)

    def test_an_already_merged_duplicate_passes_through_for_the_replay_report(self) -> None:
        path = self._write([self._pair(duplicate_id=self.duplicate.pk + 1000)])

        self.assertEqual(
            entry_point.pairs_from_file(path), [f"{self.keep.pk}:{self.duplicate.pk + 1000}"]
        )
