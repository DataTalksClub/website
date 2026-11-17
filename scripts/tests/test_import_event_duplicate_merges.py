"""The duplicate-merge entry point: a typed, reviewed, dry-run-first command."""

from __future__ import annotations

from django.test import SimpleTestCase

import scripts.prod
from scripts.prod import import_event_duplicate_merges as entry_point


class DuplicateMergeEntryPointTests(SimpleTestCase):
    def test_pairs_and_a_target_are_required_and_apply_is_opt_in(self) -> None:
        parser = entry_point._parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["--database", ".tmp/x.sqlite3"])
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
