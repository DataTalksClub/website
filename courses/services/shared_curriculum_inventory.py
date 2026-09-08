"""Operator inventory for the shared-curriculum rollout (W7).

Before any source repository moves, an operator needs one bounded,
content-free picture of every cohort of the course family: identifier,
delivery, curriculum source, mapped homework, enrollment and
submitted-assessment counts, stable IDs, and route aliases.  This service
builds that picture and optionally validates the operator's reviewed
keep-current / become-archive decisions against it.

The report never contains learner identities, emails, or content bodies:
people appear only as counts, and links appear only as slugs and stable IDs.
"""

from __future__ import annotations

import json
from typing import Any

from django.utils import timezone

from courses.models import (
    Cohort,
    CohortSharedModule,
    Course,
    CurriculumRouteAlias,
    Enrollment,
    ProjectSubmission,
    SharedCurriculum,
    SharedLesson,
    Submission,
)

DECISION_KEEP_CURRENT = "keep-current"
DECISION_BECOME_ARCHIVE = "become-archive"
_DECISION_VALUES = frozenset({DECISION_KEEP_CURRENT, DECISION_BECOME_ARCHIVE})


class SharedCurriculumInventoryError(RuntimeError):
    """A fail-closed inventory stop. ``conflicts`` is bounded and log-safe."""

    def __init__(self, conflicts: list[dict[str, str]]) -> None:
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} inventory conflict(s)")


def build_inventory(*, course_slug: str) -> dict[str, Any]:
    """Build the bounded per-cohort inventory for one course family."""

    course = Course.objects.filter(slug=course_slug).first()
    if course is None:
        raise SharedCurriculumInventoryError(
            [
                {
                    "kind": "course",
                    "identifier": course_slug,
                    "detail": "no course family with this slug",
                }
            ]
        )

    shared = SharedCurriculum.objects.filter(course=course).select_related("course").first()
    course_record: dict[str, Any] = {
        "slug": course.slug,
        "title": course.title,
        "shared_curriculum": {
            "present": shared is not None,
            "parser_version": shared.parser_version if shared else None,
            "modules_published": (
                shared.modules.filter(published=True).count() if shared else 0
            ),
            "lessons_published": (
                SharedLesson.objects.filter(
                    module__curriculum=shared, published=True
                ).count()
                if shared
                else 0
            ),
        },
    }

    cohorts: list[dict[str, Any]] = []
    for cohort in Cohort.objects.filter(course=course).order_by("identifier", "id"):
        placements = list(
            CohortSharedModule.objects.filter(cohort=cohort)
            .select_related("shared_module", "terminal_homework")
            .order_by("position", "id")
        )
        homeworks = list(
            cohort.homework_set.order_by("slug").values(
                "slug", "state", "source_content_id"
            )
        )
        aliases = list(
            CurriculumRouteAlias.objects.filter(archive_cohort=cohort)
            .order_by("old_path")
            .values_list("old_path", flat=True)
        )
        cohorts.append(
            {
                "identifier": cohort.identifier,
                "slug": cohort.slug,
                "curriculum_format": cohort.curriculum_format,
                "delivery_mode": cohort.delivery_mode,
                "curriculum_source": cohort.curriculum_source,
                "visible": cohort.visible,
                "year": cohort.year,
                "start_date": cohort.start_date.isoformat() if cohort.start_date else None,
                "end_date": cohort.end_date.isoformat() if cohort.end_date else None,
                "source_content_id": (
                    str(cohort.source_content_id)
                    if cohort.source_content_id
                    else None
                ),
                "shared_placements": [
                    {
                        "position": placement.position,
                        "module_slug": placement.shared_module.slug,
                        "module_source_content_id": str(
                            placement.shared_module.source_content_id
                        ),
                        "terminal_homework_slug": (
                            placement.terminal_homework.slug
                            if placement.terminal_homework
                            else None
                        ),
                    }
                    for placement in placements
                ],
                "homework": [
                    {
                        "slug": row["slug"],
                        "state": row["state"],
                        "source_content_id": (
                            str(row["source_content_id"])
                            if row["source_content_id"]
                            else None
                        ),
                    }
                    for row in homeworks
                ],
                "archive_identity": (
                    {
                        "notice_path": cohort.archive_notice_path,
                        "commit_sha": cohort.archive_commit_sha,
                    }
                    if cohort.curriculum_source == "github_archive"
                    else None
                ),
                "route_aliases": list(aliases),
                "counts": {
                    "enrollments": Enrollment.objects.filter(course=cohort).count(),
                    "homework_submissions": Submission.objects.filter(
                        homework__course=cohort
                    ).count(),
                    "project_submissions": ProjectSubmission.objects.filter(
                        project__course=cohort
                    ).count(),
                },
            }
        )

    return {
        "generated_at": timezone.now().isoformat(),
        "course": course_record,
        "cohorts": cohorts,
    }


def validate_decisions(
    inventory: dict[str, Any], decisions: dict[str, Any]
) -> dict[str, str]:
    """Validate operator keep/archive decisions against the inventory.

    Returns ``{identifier: decision}``.  Fails closed with a bounded conflict
    list when a decision is missing, unknown, malformed, or violates an
    invariant: an ongoing self-paced cohort is never archived, and archiving
    a cohort that carries submitted assessments requires the operator to
    acknowledge that submitted work in the decision itself.
    """

    conflicts: list[dict[str, str]] = []
    known = {cohort["identifier"]: cohort for cohort in inventory["cohorts"]}
    resolved: dict[str, str] = {}

    for identifier in sorted(known):
        raw = decisions.get(identifier)
        acknowledge = False
        if isinstance(raw, dict):
            acknowledge = bool(raw.get("acknowledge_submitted_work"))
            raw = raw.get("decision")
        if raw is None:
            conflicts.append(
                {
                    "kind": "decision_missing",
                    "identifier": identifier,
                    "detail": "every inventory cohort needs a reviewed decision",
                }
            )
            continue
        if raw not in _DECISION_VALUES:
            conflicts.append(
                {
                    "kind": "decision_invalid",
                    "identifier": identifier,
                    "detail": f"{raw!r} is not one of {sorted(_DECISION_VALUES)}",
                }
            )
            continue
        cohort = known[identifier]
        if raw == DECISION_BECOME_ARCHIVE and cohort["delivery_mode"] == "self_paced":
            conflicts.append(
                {
                    "kind": "decision_self_paced_archive",
                    "identifier": identifier,
                    "detail": (
                        "an ongoing self-paced cohort is never archived"
                    ),
                }
            )
            continue
        if (
            raw == DECISION_BECOME_ARCHIVE
            and not acknowledge
            and (
                cohort["counts"]["homework_submissions"]
                or cohort["counts"]["project_submissions"]
            )
        ):
            conflicts.append(
                {
                    "kind": "decision_submitted_work",
                    "identifier": identifier,
                    "detail": (
                        "become-archive requires acknowledge_submitted_work "
                        "when the cohort carries submitted assessments"
                    ),
                }
            )
            continue
        resolved[identifier] = raw  # type: ignore[assignment]

    for identifier in sorted(decisions):
        if identifier not in known:
            conflicts.append(
                {
                    "kind": "decision_unknown_cohort",
                    "identifier": identifier,
                    "detail": "not a cohort of the inventoried course",
                }
            )

    if conflicts:
        raise SharedCurriculumInventoryError(conflicts)
    return resolved


def load_decisions(path: str) -> dict[str, Any]:
    """Load and shape-check one decisions JSON file."""

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise SharedCurriculumInventoryError(
            [{"kind": "decisions_file_unreadable", "identifier": path, "detail": str(error)}]
        ) from error
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), dict):
        raise SharedCurriculumInventoryError(
            [
                {
                    "kind": "decisions_file_invalid",
                    "identifier": path,
                    "detail": 'expected {"decisions": {<identifier>: ...}}',
                }
            ]
        )
    return data["decisions"]


__all__ = (
    "DECISION_BECOME_ARCHIVE",
    "DECISION_KEEP_CURRENT",
    "SharedCurriculumInventoryError",
    "build_inventory",
    "load_decisions",
    "validate_decisions",
)
