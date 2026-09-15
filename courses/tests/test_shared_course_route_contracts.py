"""Contract tests for the course route shape (issue #320 reversal, 2026-09-15).

``/courses/<family>/cohorts/<identifier>/...`` was canonical from 2026-09-07
(frozen in the now-removed ``_docs/compatibility/
shared-curriculum-route-aliases.json``) until the owner reversed that
decision: the flat ``/courses/<family>/<identifier>/...`` shape is canonical
now. The retired ``cohorts/`` prefix carries no redirect and no longer
resolves to anything -- it is retired outright, not aliased.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import get_resolver

from courses.models import Cohort, Course
from courses.urls import urlpatterns

SHARED_ROUTE_NAMES = {"course_family", "shared_module", "shared_lesson"}
OPERATION_ROUTE_NAMES = {
    "cohort",
    "cohort_homework",
    "cohort_homework_statistics",
    "cohort_homework_submissions",
    "cohort_leaderboard",
    "cohort_leaderboard_score_breakdown",
    "cohort_leaderboard_complaint",
    "cohort_enrollment",
    "cohort_update_enrollment_toggle",
    "cohort_dashboard",
    "cohort_projects",
    "cohort_project",
    "cohort_project_list",
    "cohort_projects_eval",
    "cohort_project_results",
    "cohort_project_statistics",
    "cohort_project_submissions",
    "cohort_projects_eval_submit",
    "cohort_projects_eval_add",
    "cohort_projects_eval_delete",
    "cohort_calendar",
    "cohort_module",
    "cohort_unit",
    "cohort_unit_read_state",
}


class CourseRouteShapeTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        resolver = get_resolver()
        cls.live_route_names = set(resolver.reverse_dict)
        cls.pattern_by_name = {
            pattern.name: str(pattern.pattern)
            for pattern in urlpatterns
            if getattr(pattern, "name", None)
        }

    def test_every_declared_route_name_is_registered(self) -> None:
        declared = SHARED_ROUTE_NAMES | OPERATION_ROUTE_NAMES
        missing = declared - self.live_route_names
        self.assertEqual(missing, set())

    def test_canonical_routes_carry_no_cohorts_segment(self) -> None:
        for name in SHARED_ROUTE_NAMES | OPERATION_ROUTE_NAMES:
            with self.subTest(route=name):
                self.assertIn(name, self.pattern_by_name)
                self.assertNotIn("cohorts/", self.pattern_by_name[name])

    def test_shared_route_patterns_carry_no_cohort_segment(self) -> None:
        for name in SHARED_ROUTE_NAMES:
            pattern = self.pattern_by_name[name]
            self.assertNotIn("cohort", pattern, pattern)
            self.assertNotIn("modules/", pattern, pattern)

    def test_retired_cohorts_prefix_is_gone_not_redirected(self) -> None:
        family = Course.objects.create(
            slug="route-shape-family", title="Route Shape Family"
        )
        cohort = Cohort.objects.create(
            course=family,
            slug="route-shape-family-2026",
            identifier="2026",
            title="Route Shape Family 2026",
            description="Contract test cohort.",
        )
        response = self.client.get(
            f"/courses/{family.slug}/cohorts/{cohort.identifier}/dashboard?x=1"
        )
        self.assertEqual(response.status_code, 404)

    def test_flat_cohort_shape_is_reachable_directly(self) -> None:
        family = Course.objects.create(
            slug="route-shape-family-direct", title="Route Shape Family Direct"
        )
        cohort = Cohort.objects.create(
            course=family,
            slug="route-shape-family-direct-2026",
            identifier="2026",
            title="Route Shape Family Direct 2026",
            description="Contract test cohort.",
        )
        response = self.client.get(f"/courses/{family.slug}/{cohort.identifier}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cohort.canonical_url_path, f"/courses/{family.slug}/2026")
