"""Answer replacement is one atomic unit per submission (audit REL-16).

``_import_homework`` used to delete a submission's answers and bulk-create
replacements with no transaction around the pair, so a failure in between
left a previously populated submission with no answers and moved score
totals.  The coupled writes now share one bounded transaction per submission,
and these tests fault each phase of a replay to prove the prior state
survives, that a normal replay stays duplicate-free, and that a failed later
row leaves earlier rows committed.
"""

from __future__ import annotations

import csv
import datetime
import json
import shutil
import tempfile
import unittest.mock
from pathlib import Path
from typing import Any

from django.test import TestCase

import scripts.prod
from scripts.prod.legacy_zoomcamp.editions import HomeworkSource
from scripts.prod.legacy_zoomcamp.scoring_import import _import_homework

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent

HASH_ONE = "356a192b7913b04c54574d18c28d46e6395428ab"
HASH_TWO = "da4b9237bacccdf19c0760cab7aec4a8359010b0"

FIELDNAMES = [
    "email",
    "question1",
    "question2",
    "learning_in_public",
    "total_score",
]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


class ScoringImportAtomicityTests(TestCase):
    """End-to-end against a synthetic one-homework source tree."""

    def setUp(self) -> None:
        super().setUp()
        # Scratch data belongs in the project-local .tmp/, never a system temp dir.
        scratch_root = PROD_ROOT.parents[1] / ".tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="scoring-atomicity-test-", dir=scratch_root))
        self.addCleanup(shutil.rmtree, self.root, True)

        data = self.root / "old" / "mlops-zoomcamp-2022" / "data"
        self.results_csv = data / "processed" / "hw-1.csv"
        self.answers_json = data / "answers" / "answers-1.json"

        from courses.models import Cohort, Course

        family = Course.objects.create(slug="mlops-zoomcamp", title="MLOps Zoomcamp")
        self.cohort = Cohort.objects.create(
            course=family,
            slug="mlops-zoomcamp-2022",
            identifier="2022",
            year=2022,
            title="MLOps Zoomcamp 2022",
            start_date=datetime.date(2022, 1, 10),
        )

    def _write_rows(self, rows: list[dict[str, object]]) -> None:
        _write_csv(self.results_csv, rows)
        self.answers_json.parent.mkdir(parents=True, exist_ok=True)
        self.answers_json.write_text(
            json.dumps(
                {
                    "mapping": {
                        "What is 2+2?": "question1",
                        "What is a DAG?": "question2",
                    },
                    "answers": [
                        {"question": "question1", "points": 1},
                        {"question": "question2", "points": 1},
                    ],
                }
            ),
            encoding="utf-8",
        )

    def _source(self) -> HomeworkSource:
        return HomeworkSource(
            slug_part="1",
            results_csv=self.results_csv,
            answers_json=self.answers_json,
        )

    def _import(self) -> tuple[object, int, int, int]:
        return _import_homework(
            self.cohort,
            self._source(),
            position=0,
            hash_to_email={HASH_ONE: "learner-one@example.invalid"},
            topic=None,
        )

    @staticmethod
    def _answers(submission) -> list[tuple[str, str]]:
        return sorted(
            (answer.question.text, answer.answer_text) for answer in submission.answer_set.all()
        )

    def test_a_failure_during_answer_insertion_keeps_the_prior_state(self) -> None:
        """Replay with the re-insertion faulted: the populated submission
        keeps its prior answers and score totals -- the delete rolled back."""

        self._write_rows(
            [
                {
                    "email": HASH_ONE,
                    "question1": 1,
                    "question2": 1,
                    "learning_in_public": 2,
                    "total_score": 4,
                }
            ]
        )
        _homework, _count, _recovered, _fallback = self._import()
        from courses.models import Answer, Submission

        submission = Submission.objects.get()
        prior_answers = self._answers(submission)
        prior_total = submission.total_score
        self.assertEqual(len(prior_answers), 2)

        def failing_bulk_create(*args: object, **kwargs: object) -> object:
            raise RuntimeError("simulated failure during answer insertion")

        with (
            unittest.mock.patch.object(Answer.objects, "bulk_create", failing_bulk_create),
            self.assertRaises(RuntimeError),
        ):
            self._import()

        submission.refresh_from_db()
        self.assertEqual(self._answers(submission), prior_answers)
        self.assertEqual(submission.total_score, prior_total)
        self.assertEqual(Answer.objects.count(), 2)

    def test_a_failure_at_answer_deletion_keeps_the_prior_state(self) -> None:
        """Replay with the deletion itself faulted: nothing is written."""

        self._write_rows(
            [
                {
                    "email": HASH_ONE,
                    "question1": 1,
                    "question2": 1,
                    "learning_in_public": 2,
                    "total_score": 4,
                }
            ]
        )
        self._import()
        from courses.models import Answer, Submission

        submission = Submission.objects.get()
        prior_answers = self._answers(submission)

        with (
            unittest.mock.patch.object(
                Answer.objects,
                "filter",
                side_effect=RuntimeError("simulated failure at delete"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self._import()

        submission.refresh_from_db()
        self.assertEqual(self._answers(submission), prior_answers)
        self.assertEqual(Answer.objects.count(), 2)

    def test_normal_replay_creates_no_duplicate_answers(self) -> None:
        self._write_rows(
            [
                {
                    "email": HASH_ONE,
                    "question1": 1,
                    "question2": 0,
                    "learning_in_public": 2,
                    "total_score": 3,
                }
            ]
        )
        self._import()
        _homework, count, _recovered, _fallback = self._import()

        from courses.models import Answer, Enrollment, Submission

        self.assertEqual(count, 1)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(Enrollment.objects.count(), 1)
        self.assertEqual(Answer.objects.count(), 2)

    def test_a_failed_later_row_preserves_earlier_complete_rows(self) -> None:
        """Per-submission transactions: the first row commits even though a
        later row fails, so a re-run repairs only the failed row."""

        self._write_rows(
            [
                {
                    "email": HASH_ONE,
                    "question1": 1,
                    "question2": 1,
                    "learning_in_public": 2,
                    "total_score": 4,
                },
                {
                    "email": HASH_TWO,
                    "question1": 0,
                    "question2": 0,
                    "learning_in_public": 0,
                    "total_score": 0,
                },
            ]
        )
        from courses.models import Answer, Submission

        real_bulk_create = Answer.objects.bulk_create
        calls = {"count": 0}

        def flaky_bulk_create(*args: Any, **kwargs: Any) -> object:
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("simulated failure on the second row")
            return real_bulk_create(*args, **kwargs)

        with (
            unittest.mock.patch.object(Answer.objects, "bulk_create", flaky_bulk_create),
            self.assertRaises(RuntimeError),
        ):
            self._import()

        # Row one committed whole; row two left nothing behind.
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(Answer.objects.count(), 2)
        first_submission = Submission.objects.get()
        self.assertEqual(len(self._answers(first_submission)), 2)

        # An ordinary re-run completes the import with no duplicates.
        _homework, count, _recovered, _fallback = self._import()
        self.assertEqual(count, 2)
        self.assertEqual(Submission.objects.count(), 2)
        self.assertEqual(Answer.objects.count(), 4)
