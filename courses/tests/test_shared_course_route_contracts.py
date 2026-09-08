"""Contract tests for the frozen shared-curriculum route map (W0).

The manifest at ``_docs/compatibility/shared-curriculum-route-aliases.json`` is the
reviewed classification of every course route shape: canonical shared paths,
``/cohorts/<identifier>`` operations, and the legacy alias set. These tests fail
closed when a route is unclassified, a wildcard redirect sneaks in, an archive
URL claims a branch instead of a full commit SHA, or a canonical shared URL
carries a cohort query.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from django.test import SimpleTestCase

MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "_docs"
    / "compatibility"
    / "shared-curriculum-route-aliases.json"
)

# A redirect pattern must name every segment it matches; a bare ``*``, an
# unanchored ``.*``, or a ``<path:...>`` converter would swallow neighbouring
# routes and is exactly the wildcard redirect this contract forbids.
_WILDCARD = re.compile(r"(\*|<path:|\.\*)")

_SHA_IN_URL = re.compile(r"/(blob|tree)/([0-9a-f]{40})/")
_BRANCH_IN_URL = re.compile(r"/(?:blob|tree)/(?!([0-9a-f]{40})(/|$))[^/]+/")

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


class SharedCurriculumRouteManifestTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_manifest_schema_and_reviewed_entries(self) -> None:
        assert self.manifest["schema_version"] == 1
        assert self.manifest["owner"]
        assert self.manifest["purpose"]
        for canonical in self.manifest["canonical_routes"]:
            for key in ("pattern", "route_name", "kind"):
                assert canonical.get(key), (canonical, key)
            assert canonical["kind"] in {"canonical", "redirect-only"}
        for alias in self.manifest["legacy_aliases"]:
            for key in ("old_pattern", "target", "owner", "reason", "status", "methods", "query"):
                assert alias.get(key), (alias, key)
            assert alias["status"] in {"planned", "active", "retired", "governed-elsewhere"}

    def test_every_declared_route_name_is_classified_exactly_once(self) -> None:
        canonical_names = [row["route_name"] for row in self.manifest["canonical_routes"]]
        self.assertEqual(len(canonical_names), len(set(canonical_names)))
        self.assertEqual(set(canonical_names), SHARED_ROUTE_NAMES | OPERATION_ROUTE_NAMES)

    def test_no_wildcard_redirect_entries(self) -> None:
        for canonical in self.manifest["canonical_routes"]:
            self.assertIsNone(
                _WILDCARD.search(canonical["pattern"]),
                f"wildcard in canonical pattern: {canonical['pattern']}",
            )
        for alias in self.manifest["legacy_aliases"]:
            self.assertIsNone(
                _WILDCARD.search(alias["old_pattern"]),
                f"wildcard redirect alias: {alias['old_pattern']}",
            )

    def test_archive_urls_carry_a_full_commit_sha_never_a_branch(self) -> None:
        archive_examples = re.findall(
            r"https://github\.com/[^\"' ]+",
            MANIFEST_PATH.read_text(encoding="utf-8"),
        )
        # The manifest itself records the policy; any concrete GitHub blob/tree
        # URL it names (now or after W6 fills examples) must pin a full SHA.
        for url in archive_examples:
            match = _SHA_IN_URL.search(url)
            if match is None:
                # A plain repository URL is fine; only blob/tree identity is
                # required to be commit-pinned.
                assert "/blob/" not in url and "/tree/" not in url, url
                continue
            self.assertRegex(url, r"/(?:blob|tree)/[0-9a-f]{40}/")
            self.assertIsNone(_BRANCH_IN_URL.search(url), f"branch-named archive URL: {url}")

    def test_canonical_shared_patterns_carry_no_cohort_segment_or_query(self) -> None:
        for canonical in self.manifest["canonical_routes"]:
            if canonical["route_name"] not in SHARED_ROUTE_NAMES:
                continue
            pattern = canonical["pattern"]
            self.assertNotIn("cohorts/", pattern, pattern)
            self.assertNotIn("?", pattern, pattern)
            self.assertNotIn("cohort", pattern, pattern)
            self.assertNotIn("modules/", pattern, pattern)
            self.assertNotIn("year", pattern, pattern)

    def test_mutation_routes_are_declared_never_redirected(self) -> None:
        mutation_names = {
            "cohort_update_enrollment_toggle",
            "cohort_projects_eval_submit",
            "cohort_projects_eval_add",
            "cohort_projects_eval_delete",
            "cohort_unit_read_state",
        }
        by_name = {
            row["route_name"]: row
            for row in self.manifest["canonical_routes"]
        }
        for name in mutation_names:
            self.assertIn(name, by_name)
        # No legacy alias may target a mutation route.
        mutation_markers = ("toggle", "submissions", "eval", "read")
        for alias in self.manifest["legacy_aliases"]:
            target = alias["target"]
            if any(marker in target for marker in mutation_markers):
                self.fail(f"alias must not target a mutation route: {alias['old_pattern']}")

    def test_every_manifest_route_name_exists_in_the_urlconf(self) -> None:
        from django.urls import get_resolver

        resolver = get_resolver()
        live_names = set(resolver.reverse_dict)
        for urlconf in resolver.url_patterns:
            if getattr(urlconf, "namespace", None) is None:
                live_names |= {p.name for p in getattr(urlconf, "urlpatterns", []) if hasattr(p, "name")}
        declared = {row["route_name"] for row in self.manifest["canonical_routes"]}
        missing = declared - live_names
        self.assertEqual(missing, set())

    def test_every_manifest_route_name_exists_in_the_urlconf(self) -> None:
        from django.urls import get_resolver

        resolver = get_resolver()
        live_names = set(resolver.reverse_dict)
        for urlconf in resolver.url_patterns:
            if getattr(urlconf, "namespace", None) is None:
                live_names |= {p.name for p in getattr(urlconf, "urlpatterns", []) if hasattr(p, "name")}
        declared = {row["route_name"] for row in self.manifest["canonical_routes"]}
        missing = declared - live_names
        self.assertEqual(missing, set())

    def test_unknown_identifiers_have_no_fallback(self) -> None:
        invariants = self.manifest["invariants"]
        self.assertIn("unknown_identifiers_are_404", invariants)
        self.assertIn("no_wildcard_redirects", invariants)
        self.assertIn("archive_urls_require_full_commit_sha", invariants)
        self.assertIn("canonical_shared_urls_forbid_cohort_query", invariants)
        self.assertIn("mutations_never_redirected", invariants)
        self.assertIn("kwarg_compatibility_window", invariants)
