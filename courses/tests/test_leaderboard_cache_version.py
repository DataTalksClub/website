from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from django.test import TransactionTestCase

from courses.leaderboard_cache import invalidate_leaderboard_cache
from courses.models import Cohort
from test_support.migrations import MigrationWindowTestCase


class LeaderboardCacheVersionMigrationTests(MigrationWindowTestCase):
    def test_existing_cohort_gets_version_and_reverse_preserves_cohort(self):
        previous = ("courses", "0032_genmedia_approval")
        current = ("courses", "0033_cohort_leaderboard_cache_version")
        state = self.migrate(previous)
        Course = state.get_model("courses", "Course")
        CohortModel = state.get_model("courses", "Cohort")
        family = Course.objects.create(slug="cache-migration", title="Cache migration")
        cohort = CohortModel.objects.create(
            course_id=family.pk,
            slug="cache-migration-2026",
            title="Cache migration 2026",
            description="Synthetic migration fixture",
            identifier="2026",
        )

        state = self.migrate(current)
        CohortModel = state.get_model("courses", "Cohort")
        self.assertEqual(CohortModel.objects.get(pk=cohort.pk).leaderboard_cache_version, 1)
        state = self.migrate(previous)
        CohortModel = state.get_model("courses", "Cohort")
        self.assertEqual(CohortModel.objects.get(pk=cohort.pk).slug, "cache-migration-2026")
        state = self.migrate(current)
        CohortModel = state.get_model("courses", "Cohort")
        self.assertEqual(CohortModel.objects.get(pk=cohort.pk).leaderboard_cache_version, 1)


class LeaderboardCacheVersionConcurrencyTests(TransactionTestCase):
    def test_concurrent_workers_do_not_lose_version_advances(self):
        cohort = Cohort.objects.create(
            slug="cache-concurrent", title="Concurrent", description="Synthetic fixture"
        )

        def advance(_):
            close_old_connections()
            try:
                for _ in range(5):
                    invalidate_leaderboard_cache(cohort.pk)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(advance, range(2)))
        cohort.refresh_from_db()
        self.assertEqual(cohort.leaderboard_cache_version, 11)
