#!/usr/bin/env python3
"""Check that a prepared local database matches the shape production will serve.

This is the acceptance gate for ``make production-prep-dataset``.  It reports numbers
rather than prose: which cohorts exist, which of them carry module curricula and how
large those curricula are, how many course families back them, whether any upstream test
course leaked in, and how many future-dated events the public site would render.

Every check is aggregate-only.  Nothing here reads or prints learner data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import django

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The reviewed 2026 delivery. Legacy cohorts are checked separately: their presence is
# expected but their exact set follows the pinned upstream catalogue, not this list.
EXPECTED_2026_COHORTS = (
    "ai-dev-tools-zoomcamp-2026",
    "de-zoomcamp-2026",
    "llm-zoomcamp-2026",
    "ml-zoomcamp-2026",
)
EXPECTED_MODULE_CURRICULA = {
    "ml-zoomcamp-2026": (9, 105),
    "llm-zoomcamp-2026": (7, 72),
    "ai-dev-tools-zoomcamp-2026": (4, 4),
}
# Upstream test rows that must never reach a public catalogue.
FORBIDDEN_COHORT_SLUGS = ("fake-course", "fake-course-2")


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")
    django.setup()


def _cohort_report() -> dict[str, Any]:
    from django.db.models import Count

    from courses.models import Cohort, Course

    cohorts = {
        cohort.slug: cohort for cohort in Cohort.objects.select_related("course").order_by("slug")
    }
    module_counts = {
        row["slug"]: (row["module_total"], row["unit_total"])
        for row in Cohort.objects.filter(curriculum_format="modules")
        .annotate(
            module_total=Count("modules", distinct=True),
            unit_total=Count("modules__units"),
        )
        .values("slug", "module_total", "unit_total")
    }
    family_rows = list(Course.objects.prefetch_related("cohorts").order_by("slug"))
    families = {family.slug: family.cohorts.count() for family in family_rows}
    # One real course must own exactly one family row. A course split across two rows
    # keeps its title, so a repeated title is the reliable signal for issue #308.
    titles: dict[str, list[str]] = {}
    for family in family_rows:
        titles.setdefault(family.title, []).append(family.slug)
    split_families = {title: slugs for title, slugs in titles.items() if len(slugs) > 1}
    modules_format = sorted(
        slug for slug, cohort in cohorts.items() if cohort.curriculum_format == "modules"
    )
    return {
        "cohort_total": len(cohorts),
        "cohort_slugs": sorted(cohorts),
        "missing_2026_cohorts": [slug for slug in EXPECTED_2026_COHORTS if slug not in cohorts],
        "modules_format_cohorts": modules_format,
        "modules_format_unexpected": sorted(set(modules_format) - set(EXPECTED_MODULE_CURRICULA)),
        "curriculum_counts": {
            slug: {
                "modules": module_counts.get(slug, (0, 0))[0],
                "units": module_counts.get(slug, (0, 0))[1],
            }
            for slug in EXPECTED_MODULE_CURRICULA
        },
        "curriculum_count_mismatches": {
            slug: {
                "expected": {"modules": expected[0], "units": expected[1]},
                "actual": {
                    "modules": module_counts.get(slug, (0, 0))[0],
                    "units": module_counts.get(slug, (0, 0))[1],
                },
            }
            for slug, expected in EXPECTED_MODULE_CURRICULA.items()
            if module_counts.get(slug, (0, 0)) != expected
        },
        "course_families": families,
        "course_family_total": len(families),
        "split_course_families": split_families,
        "empty_course_families": sorted(slug for slug, count in families.items() if not count),
        "forbidden_cohorts_present": [slug for slug in FORBIDDEN_COHORT_SLUGS if slug in cohorts],
    }


def _event_report() -> dict[str, Any]:
    from django.utils import timezone

    from content.public_data import event_groups
    from events.models import Event

    groups = event_groups()
    return {
        "checked_at": timezone.now().isoformat(),
        "projection_event_total": len(groups.upcoming) + len(groups.recent),
        "future_dated_events": len(groups.upcoming),
        "next_event_starts_at": groups.upcoming[0]["starts_at"] if groups.upcoming else None,
        "database_event_identities": Event.objects.count(),
    }


def _failures(report: dict[str, Any]) -> list[str]:
    courses = report["courses"]
    failures: list[str] = []
    if courses["missing_2026_cohorts"]:
        failures.append(f"missing 2026 cohorts: {courses['missing_2026_cohorts']}")
    if courses["modules_format_unexpected"]:
        failures.append(
            f"unexpected modules-format cohorts: {courses['modules_format_unexpected']}"
        )
    if courses["curriculum_count_mismatches"]:
        failures.append(
            f"curriculum count mismatches: {sorted(courses['curriculum_count_mismatches'])}"
        )
    if courses["forbidden_cohorts_present"]:
        failures.append(f"upstream test courses present: {courses['forbidden_cohorts_present']}")
    if courses["split_course_families"]:
        failures.append(
            "one course split across several families (issue #308): "
            f"{courses['split_course_families']}"
        )
    if courses["empty_course_families"]:
        failures.append(f"course families with no cohorts: {courses['empty_course_families']}")
    if not report["events"]["future_dated_events"]:
        failures.append("no future-dated events (issue #307 data-freshness gate)")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Print the report and exit 0 even when a check fails.",
    )
    args = parser.parse_args(argv)
    _configure(args.database.resolve())

    report = {
        "database": str(args.database.resolve()),
        "courses": _cohort_report(),
        "events": _event_report(),
    }
    failures = _failures(report)
    report["failures"] = failures
    print(json.dumps(report, indent=2, sort_keys=True))
    if failures and not args.allow_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
