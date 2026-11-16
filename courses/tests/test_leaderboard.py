from unittest.mock import patch

from django.core.cache import cache
from django.db import transaction

from courses.leaderboard import update_leaderboard
from courses.leaderboard_cache import invalidate_leaderboard_cache
from courses.models import Cohort
from courses.tests.leaderboard_base import LeaderboardTestBase


class LeaderboardScoringTestCase(LeaderboardTestBase):
    def test_leaderboard(self):
        enrollments = self.create_leaderboard_fixture()

        update_leaderboard(self.course)

        self.assert_leaderboard_scores(enrollments)

    def test_leaderboard_cache_invalidation(self):
        enrollment1 = self.create_student("student1")
        enrollment2 = self.create_student("student2")
        homework = self.create_homework(1)
        self.submit_homework(homework, enrollment1, score=100)
        self.submit_homework(homework, enrollment2, score=50)

        update_leaderboard(self.course)

        self.course.refresh_from_db()
        original_version = self.course.leaderboard_cache_version
        cache_key = f"leaderboard:{self.course.id}:v{original_version}"
        cache.set(cache_key, "old worker rows", 3600)

        update_leaderboard(self.course)

        fresh_cohort = Cohort.objects.get(pk=self.course.pk)
        self.assertGreater(fresh_cohort.leaderboard_cache_version, original_version)
        # A different worker's old entry can remain, but fresh requests cannot use it.
        self.assertEqual(cache.get(cache_key), "old worker rows")
        new_key = f"leaderboard:{self.course.id}:v{fresh_cohort.leaderboard_cache_version}"
        self.assertIsNone(cache.get(new_key))

    def test_stale_cohort_instances_cannot_lose_version_advances(self):
        stale_copy = Cohort.objects.get(pk=self.course.pk)
        invalidate_leaderboard_cache(self.course.pk)
        invalidate_leaderboard_cache(stale_copy.pk)
        self.course.refresh_from_db()
        self.assertEqual(self.course.leaderboard_cache_version, 3)

    def test_failed_recalculation_rolls_back_totals_and_version(self):
        enrollment = self.create_student("rollback")
        homework = self.create_homework(1)
        self.submit_homework(homework, enrollment, score=100)
        with patch(
            "courses.leaderboard.invalidate_leaderboard_cache",
            side_effect=RuntimeError("cannot advance generation"),
        ):
            with self.assertRaises(RuntimeError):
                update_leaderboard(self.course)
        enrollment.refresh_from_db()
        self.course.refresh_from_db()
        self.assertEqual(enrollment.total_score, 0)
        self.assertEqual(self.course.leaderboard_cache_version, 1)

    def test_version_advance_rolls_back_with_surrounding_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                invalidate_leaderboard_cache(self.course.pk)
                raise RuntimeError("rollback")
        self.course.refresh_from_db()
        self.assertEqual(self.course.leaderboard_cache_version, 1)
