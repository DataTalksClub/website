"""Cache behavior tests for the public leaderboard data endpoint."""

from time import time
from unittest.mock import patch

import yaml
from django.core.cache.backends.locmem import LocMemCache
from django.test import override_settings

from courses.leaderboard import update_leaderboard
from courses.models import Cohort
from courses.views.course_enrollment import EnrollmentToggleUpdate, update_enrollment_toggle_value
from courses.views.course_leaderboard_data import CurrentLeaderboardStudent, get_leaderboard_data

from .leaderboard_base import LeaderboardDataViewBase


class LeaderboardCacheViewTestCase(LeaderboardDataViewBase):
    def test_response_is_cached(self):
        self.client.get(self.url)

        self.enrollment1.display_name = "Alice Changed"
        self.enrollment1.save()

        response = self.client.get(self.url)
        data = yaml.safe_load(response.content)
        self.assertEqual(data["leaderboard"][0]["display_name"], "Alice")

    def test_rendered_yaml_response_is_cached(self):
        self.client.get(self.url)

        with patch("api.views.leaderboard_exports.yaml.dump") as yaml_dump:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        yaml_dump.assert_not_called()

    def test_cache_invalidation(self):
        self.client.get(self.url)

        self.enrollment1.display_name = "Alice Changed"
        self.enrollment1.save()
        update_leaderboard(self.course)

        response = self.client.get(self.url)
        data = yaml.safe_load(response.content)
        self.assertEqual(
            data["leaderboard"][0]["display_name"],
            "Alice Changed",
        )

    def test_visibility_change_invalidates_another_workers_html_and_yaml(self):
        worker_cache = LocMemCache("leaderboard-other-worker", {})
        worker_cache.clear()
        no_student = CurrentLeaderboardStudent(None, None)
        with (
            patch("api.views.leaderboard_exports.cache", worker_cache),
            patch("courses.views.course_leaderboard_data.cache", worker_cache),
        ):
            self.client.get(self.url)
            rows = get_leaderboard_data(self.course, no_student)
            self.assertIn(self.enrollment1.pk, [row["id"] for row in rows])

        # Mutation happens in another worker; it cannot delete the warmed local cache.
        update_enrollment_toggle_value(
            EnrollmentToggleUpdate(
                course=self.course,
                enrollment=self.enrollment1,
                field="display_on_leaderboard",
                enabled=False,
            )
        )
        fresh_cohort = Cohort.objects.get(pk=self.course.pk)
        with (
            patch("api.views.leaderboard_exports.cache", worker_cache),
            patch("courses.views.course_leaderboard_data.cache", worker_cache),
        ):
            response = self.client.get(self.url)
            names = [row["display_name"] for row in yaml.safe_load(response.content)["leaderboard"]]
            # Reusing the worker's stale model must still read the DB version.
            self.assertGreater(
                fresh_cohort.leaderboard_cache_version, self.course.leaderboard_cache_version
            )
            rows = get_leaderboard_data(self.course, no_student)
        self.assertNotIn("Alice", names)
        self.assertNotIn(self.enrollment1.pk, [row["id"] for row in rows])

    @override_settings(LEADERBOARD_CACHE_TTL=0)
    def test_zero_ttl_refreshes_export_and_html_without_recalculation(self):
        no_student = CurrentLeaderboardStudent(None, None)
        self.client.get(self.url)
        get_leaderboard_data(self.course, no_student)
        self.enrollment1.display_name = "Changed after cache fill"
        self.enrollment1.save(update_fields=["display_name"])

        response = self.client.get(self.url)
        names = [row["display_name"] for row in yaml.safe_load(response.content)["leaderboard"]]
        rows = get_leaderboard_data(self.course, no_student)
        self.assertIn("Changed after cache fill", names)
        self.assertIn("Changed after cache fill", [row["display_name"] for row in rows])

    def test_default_ttl_expires_both_worker_caches_after_sixty_seconds(self):
        no_student = CurrentLeaderboardStudent(None, None)
        self.client.get(self.url)
        get_leaderboard_data(self.course, no_student)
        self.enrollment1.display_name = "Changed after default expiry"
        self.enrollment1.save(update_fields=["display_name"])

        with patch("django.core.cache.backends.locmem.time.time", return_value=time() + 61):
            response = self.client.get(self.url)
            names = [row["display_name"] for row in yaml.safe_load(response.content)["leaderboard"]]
            rows = get_leaderboard_data(self.course, no_student)
        self.assertIn("Changed after default expiry", names)
        self.assertIn("Changed after default expiry", [row["display_name"] for row in rows])

    def test_invalidation_does_not_change_another_cohort_version(self):
        from courses.leaderboard_cache import invalidate_leaderboard_cache

        other = Cohort.objects.create(slug="isolated", title="Isolated", description="Fixture")
        invalidate_leaderboard_cache(self.course.pk)
        other.refresh_from_db()
        self.assertEqual(other.leaderboard_cache_version, 1)

    def test_stale_hidden_enrollment_cannot_skip_a_later_hide_invalidation(self):
        from courses.models import Enrollment

        self.enrollment1.display_on_leaderboard = False
        self.enrollment1.save(update_fields=["display_on_leaderboard"])
        stale_hidden = Enrollment.objects.get(pk=self.enrollment1.pk)
        update_enrollment_toggle_value(
            EnrollmentToggleUpdate(
                self.enrollment1,
                self.course,
                "display_on_leaderboard",
                True,
            )
        )
        self.assertIn(
            "Alice", [row["display_name"] for row in self.leaderboard_data()["leaderboard"]]
        )
        update_enrollment_toggle_value(
            EnrollmentToggleUpdate(
                stale_hidden,
                self.course,
                "display_on_leaderboard",
                False,
            )
        )
        self.assertNotIn(
            "Alice", [row["display_name"] for row in self.leaderboard_data()["leaderboard"]]
        )
