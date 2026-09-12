"""``Submission.submitted_at`` is recovered from the raw weekly export.

Regression cover for the bug where ``scoring_import.py`` never set
``submitted_at`` in its ``update_or_create`` ``defaults``, so every imported
submission silently fell back to the model's ``timezone.now`` default -- the
moment the import ran, not the learner's real historical submission time.
This clustered every submission from a cohort at one instant, which broke the
"Submission timing" and "Engagement over time" cohort dashboard widgets (both
read only from ``Submission.submitted_at``).

The fix joins each homework's raw Google Form export (still carrying a real,
plaintext ``Timestamp`` + ``Email``/``Email address`` column keyed by the same
address the processed/graded export hashes) back in by that same hash.
"""

from __future__ import annotations

import csv
import datetime
import shutil
import tempfile
from pathlib import Path

from django.test import TestCase

import scripts.prod
from scripts.prod.legacy_zoomcamp.editions import HomeworkSource
from scripts.prod.legacy_zoomcamp.scoring_import import (
    _import_homework,
    _parse_raw_timestamp,
    _raw_email_to_timestamp,
    _sniff_date_order,
)

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent

HASH_ONE = "356a192b7913b04c54574d18c28d46e6395428ab"  # sha1("1")
HASH_TWO = "da4b9237bacccdf19c0760cab7aec4a8359010b0"  # sha1("2")

RESULTS_FIELDNAMES = ["email", "question1", "learning_in_public", "total_score"]


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class DateOrderSniffingTests(TestCase):
    """Field order varies export to export, never row to row within one."""

    def test_a_value_over_12_in_the_first_field_pins_day_first(self) -> None:
        self.assertEqual(_sniff_date_order([("28", "03", "2022")]), "dmy")

    def test_a_value_over_12_in_the_second_field_pins_month_first(self) -> None:
        self.assertEqual(_sniff_date_order([("03", "28", "2022")]), "mdy")

    def test_a_four_digit_first_field_is_year_first(self) -> None:
        self.assertEqual(_sniff_date_order([("2023", "01", "18")]), "ymd")

    def test_an_export_with_no_disambiguating_value_defaults_to_day_first(self) -> None:
        self.assertEqual(_sniff_date_order([("05", "09", "2022")]), "dmy")


class RawTimestampParsingTests(TestCase):
    def test_day_first_slash_separated(self) -> None:
        parsed = _parse_raw_timestamp("06/09/2021 04:11:43", "dmy")
        self.assertEqual(
            parsed, datetime.datetime(2021, 9, 6, 4, 11, 43, tzinfo=datetime.UTC)
        )

    def test_month_first_slash_separated(self) -> None:
        parsed = _parse_raw_timestamp("9/26/2022 22:44:43", "mdy")
        self.assertEqual(
            parsed, datetime.datetime(2022, 9, 26, 22, 44, 43, tzinfo=datetime.UTC)
        )

    def test_year_first_slash_separated(self) -> None:
        parsed = _parse_raw_timestamp("2023/01/18 13:14:04", "ymd")
        self.assertEqual(
            parsed, datetime.datetime(2023, 1, 18, 13, 14, 4, tzinfo=datetime.UTC)
        )

    def test_dot_separated(self) -> None:
        parsed = _parse_raw_timestamp("03.10.2022 20:32:07", "dmy")
        self.assertEqual(
            parsed, datetime.datetime(2022, 10, 3, 20, 32, 7, tzinfo=datetime.UTC)
        )

    def test_an_unparseable_value_is_reported_as_absent_not_guessed(self) -> None:
        self.assertIsNone(_parse_raw_timestamp("not-a-timestamp", "dmy"))


class ScoringImportSubmittedAtTests(TestCase):
    """End-to-end: ``_import_homework`` joins the raw export back in by hash."""

    def setUp(self) -> None:
        super().setUp()
        scratch_root = PROD_ROOT.parents[1] / ".tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="scoring-submitted-at-test-", dir=scratch_root))
        self.addCleanup(shutil.rmtree, self.root, True)

        data = self.root / "old" / "ml-zoomcamp-2022" / "data"
        self.results_csv = data / "processed" / "hw-01.csv"
        self.raw_csv = data / "raw" / "homework-01.csv"

        from courses.models import Cohort, Course

        family = Course.objects.create(slug="ml-zoomcamp", title="Machine Learning Zoomcamp")
        self.cohort = Cohort.objects.create(
            course=family,
            slug="ml-zoomcamp-2022",
            identifier="2022",
            year=2022,
            title="Machine Learning Zoomcamp 2022",
            start_date=datetime.date(2022, 9, 1),
        )

    def _source(self) -> HomeworkSource:
        return HomeworkSource(
            slug_part="01",
            results_csv=self.results_csv,
            answers_json=None,
            raw_csv=self.raw_csv,
        )

    def test_a_matched_row_recovers_its_real_historical_timestamp(self) -> None:
        _write_csv(
            self.results_csv,
            RESULTS_FIELDNAMES,
            [{"email": HASH_ONE, "question1": 1, "learning_in_public": 0, "total_score": 1}],
        )
        _write_csv(
            self.raw_csv,
            ["Timestamp", "Email address"],
            [{"Timestamp": "05/09/2022 22:05:49", "Email address": "1"}],
        )

        _homework, _count, recovered, fallback = _import_homework(
            self.cohort, self._source(), position=0, hash_to_email={}, topic=None
        )

        from courses.models import Submission

        submission = Submission.objects.get()
        self.assertEqual(
            submission.submitted_at,
            datetime.datetime(2022, 9, 5, 22, 5, 49, tzinfo=datetime.UTC),
        )
        self.assertEqual(recovered, 1)
        self.assertEqual(fallback, 0)

    def test_an_unmatched_row_falls_back_without_a_fabricated_precise_time(self) -> None:
        """No raw export at all: every row in it visibly counts as fallback,
        never a guessed timestamp standing in for a real one."""

        _write_csv(
            self.results_csv,
            RESULTS_FIELDNAMES,
            [{"email": HASH_TWO, "question1": 1, "learning_in_public": 0, "total_score": 1}],
        )
        # No raw_csv written at all -- the missing-export case.
        before = datetime.datetime.now(datetime.UTC)

        _homework, _count, recovered, fallback = _import_homework(
            self.cohort, self._source(), position=0, hash_to_email={}, topic=None
        )

        from courses.models import Submission

        submission = Submission.objects.get()
        # The model's own import-time default -- never a fabricated historical value.
        self.assertGreaterEqual(submission.submitted_at, before)
        self.assertEqual(recovered, 0)
        self.assertEqual(fallback, 1)

    def test_a_replay_does_not_clobber_a_fallback_row_with_a_fresh_now(self) -> None:
        _write_csv(
            self.results_csv,
            RESULTS_FIELDNAMES,
            [{"email": HASH_TWO, "question1": 1, "learning_in_public": 0, "total_score": 1}],
        )

        _import_homework(self.cohort, self._source(), position=0, hash_to_email={}, topic=None)

        from courses.models import Submission

        first_submitted_at = Submission.objects.get().submitted_at

        _import_homework(self.cohort, self._source(), position=0, hash_to_email={}, topic=None)

        self.assertEqual(Submission.objects.get().submitted_at, first_submitted_at)

    def test_a_duplicate_email_in_the_raw_export_keeps_the_last_submission(self) -> None:
        """Mirrors the upstream grading pipeline's own dedup rule
        (``evaluate_week.py``: ``drop_duplicates(keep='last')``) so the
        recovered timestamp matches the resubmission that was actually
        graded."""

        _write_csv(
            self.results_csv,
            RESULTS_FIELDNAMES,
            [{"email": HASH_ONE, "question1": 1, "learning_in_public": 0, "total_score": 1}],
        )
        _write_csv(
            self.raw_csv,
            ["Timestamp", "Email address"],
            [
                {"Timestamp": "05/09/2022 10:00:00", "Email address": "1"},
                {"Timestamp": "06/09/2022 11:30:00", "Email address": "1"},
            ],
        )

        mapping = _raw_email_to_timestamp(self.raw_csv)

        self.assertEqual(
            mapping[HASH_ONE],
            datetime.datetime(2022, 9, 6, 11, 30, tzinfo=datetime.UTC),
        )

    def test_a_missing_raw_file_has_an_empty_map_rather_than_raising(self) -> None:
        self.assertEqual(_raw_email_to_timestamp(self.raw_csv), {})
        self.assertEqual(_raw_email_to_timestamp(None), {})
