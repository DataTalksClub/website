"""REL-03/REL-04 acceptance for the learner-history importer.

The run binding must refuse a different export before any write, and a
killed batch must leave the database exactly as it was -- claims, watermark
and rows commit or roll back as one -- so an ordinary unmodified re-run
reaches the same end state as an uninterrupted one, with no manual progress
repair.
"""

from __future__ import annotations

import unittest.mock
from pathlib import Path
from typing import Any

from django.db import IntegrityError

from accounts.models import CustomUser
from courses.models import (
    CmpHistoryClaim,
    CmpHistoryImportBinding,
    CmpHistoryImportProgress,
    Enrollment,
)
from courses.services import cmp_learner_history_import as service
from courses.services.cmp_learner_history_import import (
    CmpHistoryImportError,
    import_cmp_learner_history,
)
from courses.tests.test_cmp_learner_history_import import (
    HistoryImportFixture,
    enrollment_row,
)


def _user_claims_for(learners) -> dict[int, int]:
    """CMP source ids 1..n -> the target pks the account importer wrote."""

    return {index + 1: learner.pk for index, learner in enumerate(learners)}


class HistoryRunBindingTests(HistoryImportFixture):
    def _second_learner(self):
        if not hasattr(self, "_extra_learner"):
            self._extra_learner = CustomUser.objects.create_user(
                username="learner-two",
                email="two@example.invalid",
            )
        return self._extra_learner

    def test_the_first_run_records_the_binding(self) -> None:
        source = self.source(courses_enrollment=[enrollment_row(1)])

        self.run_import(source)

        binding = CmpHistoryImportBinding.objects.get(kind=service.BINDING_KIND)
        self.assertEqual(binding.schema_version, service.SCHEMA_VERSION)
        self.assertEqual(binding.importer_version, service.IMPORTER_VERSION)
        self.assertEqual(len(binding.source_sha256), 64)

    def test_a_rerun_of_the_same_export_resumes(self) -> None:
        learners = [self.learner, self._second_learner()]
        source = self.source(
            courses_enrollment=[
                enrollment_row(1, student_id=1),
                enrollment_row(2, student_id=2),
            ]
        )
        self.run_import(source, batch_size=1, user_claims=_user_claims_for(learners))

        result = self.run_import(source, batch_size=1, user_claims=_user_claims_for(learners))

        self.assertEqual(Enrollment.objects.count(), 2)
        self.assertEqual(self.report(result, "courses_enrollment")["created"], 2)

    def test_a_changed_export_is_refused_before_any_write(self) -> None:
        first = self.source(courses_enrollment=[enrollment_row(1)])
        self.run_import(first)
        enrollments_before = Enrollment.objects.count()
        claims_before = CmpHistoryClaim.objects.count()

        # Same logical shape, different bytes: a different snapshot.
        second = self.source(courses_enrollment=[enrollment_row(1), enrollment_row(2)])

        with self.assertRaises(CmpHistoryImportError) as error:
            self.run_import(second)

        self.assertEqual(str(error.exception), "run-bound-to-different-source")
        self.assertEqual(Enrollment.objects.count(), enrollments_before)
        self.assertEqual(CmpHistoryClaim.objects.count(), claims_before)

    def test_user_claims_come_from_this_database(self) -> None:
        """A foreign user-claims mapping cannot attach rows: only the
        accounts the learner-account importer recorded in *this* database
        resolve, and anything else is an unresolved count, never a write."""

        source = self.source(courses_enrollment=[enrollment_row(1)])
        forged_user_claims = {1: 999_999}

        result = import_cmp_learner_history(
            source,
            user_claims=forged_user_claims,
            batch_size=10,
        )

        self.assertEqual(
            self.report(result, "courses_enrollment")["unresolved"],
            {"user": 1},
        )
        self.assertEqual(Enrollment.objects.count(), 0)


class HistoryKilledBatchAtomicityTests(HistoryImportFixture):
    """Fault injection immediately inside a batch (audit REL-04): the rows,
    their claims and the watermark must roll back together."""

    def _enrollment_source(self, count: int) -> Path:
        return self.source(
            courses_enrollment=[
                enrollment_row(index + 1, student_id=index + 1) for index in range(count)
            ]
        )

    def _learners(self, count: int) -> dict[int, int]:
        learners = [self.learner]
        while len(learners) < count:
            learner = CustomUser.objects.create_user(
                username=f"learner-extra-{len(learners)}",
                email=f"extra-{len(learners)}@example.invalid",
            )
            learners.append(learner)
        return _user_claims_for(learners)

    def test_a_killed_batch_leaves_no_partial_state_and_a_rerun_completes(
        self,
    ) -> None:
        source = self._enrollment_source(3)
        real_restore = service._restore_stamps
        calls = {"count": 0}

        def trapping_restore(plan: Any, created: Any, stamps: Any) -> None:
            calls["count"] += 1
            if calls["count"] == 2:
                raise IntegrityError("simulated kill mid-batch")
            return real_restore(plan, created, stamps)

        user_claims = self._learners(3)

        with unittest.mock.patch.object(service, "_restore_stamps", trapping_restore):
            with self.assertRaises(IntegrityError):
                self.run_import(source, batch_size=1, user_claims=user_claims)

        # Batch 1 committed; nothing from the killed batch 2 survived.
        self.assertEqual(Enrollment.objects.count(), 1)
        self.assertEqual(CmpHistoryClaim.objects.count(), 1)
        progress = CmpHistoryImportProgress.objects.get(table="courses_enrollment")
        self.assertEqual(progress.last_source_id, 1)
        self.assertEqual(progress.rows_created, 1)

        # An ordinary, unmodified re-run completes the import.
        result = self.run_import(source, batch_size=1, user_claims=user_claims)

        self.assertEqual(Enrollment.objects.count(), 3)
        self.assertEqual(CmpHistoryClaim.objects.count(), 3)
        self.assertEqual(
            sorted(
                CmpHistoryClaim.objects.filter(table="courses_enrollment").values_list(
                    "source_id", flat=True
                )
            ),
            [1, 2, 3],
        )
        progress.refresh_from_db()
        self.assertTrue(progress.completed)
        self.assertEqual(self.report(result, "courses_enrollment")["created"], 3)
