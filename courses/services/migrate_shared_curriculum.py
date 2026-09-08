"""Backfill the one current shared graph from existing cohort-owned rows.

Before a source repository moves to the schema-2 layout, the current teaching
material may already exist as cohort-owned ``Module``/``Unit`` rows imported by
the schema-1 importer (duplicated once per cohort).  This service reads those
rows by stable source content ID, creates one shared graph for the course, one
placement per cohort, and copies each learner's earliest read timestamp per
stable ID into the shared read state.

Nothing else changes: old rows, homework, projects, submissions, scores,
enrollments, and certificates are untouched, and every write is idempotent.
A conflict -- one stable ID mapped to two rows with different content or
provenance -- stops the run with a bounded report; there is no fuzzy title or
year matching, and a failed or conflicting run changes no rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Mapping

from django.db import transaction
from django.utils import timezone

from courses.models import (
    Cohort,
    CohortSharedModule,
    Course,
    Module,
    SharedCurriculum,
    SharedLesson,
    SharedLessonReadState,
    SharedModule,
    Unit,
    UnitReadState,
)

BACKFILL_PARSER_VERSION = "backfill-from-cohort-modules-v1"
MAX_CONFLICTS = 50


class SharedCurriculumBackfillError(RuntimeError):
    """A fail-closed backfill stop. ``conflicts`` is bounded and log-safe."""

    def __init__(self, conflicts: list[dict[str, str]]) -> None:
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} stable-ID conflict(s)")


@dataclass(frozen=True, slots=True)
class BackfillResult:
    course: Course
    dry_run: bool
    shared_curriculum_id: int | None
    counts: Mapping[str, int]
    conflicts: list[dict[str, str]]


def _provenance_of(instance) -> dict[str, object]:
    return {
        "source_content_id": instance.source_content_id,
        "source_path": instance.source_path,
        "source_commit_sha": instance.source_commit_sha,
        "source_checksum": instance.source_checksum,
    }


def _record_conflicts(
    rows: list,
    *,
    label: str,
    compare_fields: list[str],
    conflicts: list[dict[str, str]],
) -> None:
    """Record bounded conflicts for one stable ID with diverging rows."""

    by_id: dict[uuid.UUID, list] = {}
    for row in rows:
        if row.source_content_id is not None:
            by_id.setdefault(row.source_content_id, []).append(row)
    for source_id, group in sorted(by_id.items(), key=lambda item: str(item[0])):
        if len(conflicts) >= MAX_CONFLICTS:
            return
        if len(group) < 2:
            continue
        first = group[0]
        for other in group[1:]:
            diverging = [
                field_name
                for field_name in compare_fields
                if getattr(first, field_name) != getattr(other, field_name)
            ]
            if diverging or _provenance_of(first) != _provenance_of(other):
                conflicts.append(
                    {
                        "kind": label,
                        "source_content_id": str(source_id),
                        "rows": str(len(group)),
                        "diverging": ",".join(diverging) or "provenance",
                    }
                )
                break


def backfill_shared_curriculum(
    *, course_slug: str, apply: bool = False
) -> BackfillResult:
    """Create the course's shared graph from its cohort-owned module rows.

    With ``apply=False`` the run is computed inside a transaction that is then
    rolled back, so a dry run is repeatable and side-effect free.  With
    ``apply=True`` the run is idempotent: existing shared rows are reused and
    updated in place by stable ID.  Conflicts never mutate the database.
    """

    course = Course.objects.filter(slug=course_slug).first()
    if course is None:
        raise SharedCurriculumBackfillError(
            [
                {
                    "kind": "course",
                    "source_content_id": course_slug,
                    "rows": "0",
                    "diverging": "missing",
                }
            ]
        )

    conflicts: list[dict[str, str]] = []
    counts: dict[str, int] = {
        "shared_modules": 0,
        "shared_lessons": 0,
        "placements": 0,
        "read_states": 0,
        "conflicts": 0,
    }

    module_format_cohorts = list(
        Cohort.objects.filter(course=course, curriculum_format="modules").order_by(
            "identifier", "id"
        )
    )
    cohort_modules: dict[int, list[Module]] = {}
    all_modules: list[Module] = []
    for cohort in module_format_cohorts:
        rows = list(
            cohort.modules.filter(source_content_id__isnull=False).order_by("position", "id")
        )
        cohort_modules[cohort.pk] = rows
        all_modules.extend(rows)

    _record_conflicts(
        all_modules,
        label="shared_module",
        compare_fields=["slug", "title"],
        conflicts=conflicts,
    )
    units_by_module: dict[int, list[Unit]] = {}
    for row in all_modules:
        units_by_module[row.pk] = list(
            row.units.filter(source_content_id__isnull=False).order_by("position", "id")
        )
    _record_conflicts(
        [unit for units in units_by_module.values() for unit in units],
        label="shared_lesson",
        compare_fields=["slug", "title", "content_markdown"],
        conflicts=conflicts,
    )
    counts["conflicts"] = len(conflicts)
    if conflicts:
        return BackfillResult(
            course=course,
            dry_run=not apply,
            shared_curriculum_id=None,
            counts=counts,
            conflicts=conflicts,
        )

    def mutate() -> int:
        shared, _ = SharedCurriculum.objects.get_or_create(
            course=course,
            defaults={
                "parser_version": BACKFILL_PARSER_VERSION,
                "updated_at": timezone.now(),
                **_provenance_of(course),
            },
        )
        shared.parser_version = BACKFILL_PARSER_VERSION
        shared.updated_at = timezone.now()
        shared.save(update_fields=("parser_version", "updated_at"))

        module_by_source_id: dict[uuid.UUID, SharedModule] = {}
        ordered_source_ids: list[uuid.UUID] = []
        for row in all_modules:
            if row.source_content_id in module_by_source_id:
                continue
            shared_module, created = SharedModule.objects.update_or_create(
                curriculum=shared,
                source_content_id=row.source_content_id,
                defaults={
                    "slug": row.slug,
                    "title": row.title,
                    "position": len(ordered_source_ids),
                    **_provenance_of(row),
                    "retired_at": None,
                },
            )
            module_by_source_id[row.source_content_id] = shared_module
            ordered_source_ids.append(row.source_content_id)
            if created:
                counts["shared_modules"] += 1

        lesson_by_source_id: dict[uuid.UUID, SharedLesson] = {}
        for row in all_modules:
            shared_module = module_by_source_id[row.source_content_id]
            for position, unit in enumerate(units_by_module[row.pk]):
                if unit.source_content_id in lesson_by_source_id:
                    continue
                shared_lesson, created = SharedLesson.objects.update_or_create(
                    module=shared_module,
                    source_content_id=unit.source_content_id,
                    defaults={
                        "slug": unit.slug,
                        "title": unit.title,
                        "position": position,
                        "content_markdown": unit.content_markdown,
                        "rendered_html": unit.rendered_html,
                        "video_url": unit.video_url,
                        "code_sources": unit.code_sources,
                        **_provenance_of(unit),
                        "retired_at": None,
                    },
                )
                lesson_by_source_id[unit.source_content_id] = shared_lesson
                if created:
                    counts["shared_lessons"] += 1

        for cohort in module_format_cohorts:
            for position, row in enumerate(cohort_modules[cohort.pk]):
                shared_module = module_by_source_id[row.source_content_id]
                placement, created = CohortSharedModule.objects.update_or_create(
                    cohort=cohort,
                    shared_module=shared_module,
                    defaults={
                        "position": position,
                        "terminal_homework": row.terminal_homework,
                    },
                )
                if created:
                    counts["placements"] += 1

        # Copy each learner's earliest read marker per stable lesson ID.  A
        # different source ID is a different lesson: nothing silently merges.
        unit_ids_by_source_id: dict[uuid.UUID, list[int]] = {}
        for row in all_modules:
            for unit in units_by_module[row.pk]:
                unit_ids_by_source_id.setdefault(unit.source_content_id, []).append(unit.pk)
        for source_id, shared_lesson in lesson_by_source_id.items():
            earliest_per_user = (
                UnitReadState.objects.filter(
                    unit_id__in=unit_ids_by_source_id.get(source_id, [])
                )
                .order_by("user_id", "read_at", "pk")
                .values("user_id", "read_at")
            )
            seen_users: set[int] = set()
            for marker in earliest_per_user:
                if marker["user_id"] in seen_users:
                    continue
                seen_users.add(marker["user_id"])
                _, created = SharedLessonReadState.objects.get_or_create(
                    user_id=marker["user_id"],
                    shared_lesson=shared_lesson,
                    defaults={"read_at": marker["read_at"]},
                )
                if created:
                    counts["read_states"] += 1

        return shared.pk

    class _DryRunComplete(Exception):
        """Rolls back only the inner savepoint; never escapes this module."""

    if not apply:
        # Compute the dry run inside a savepoint that is rolled back on exit,
        # so a dry run is repeatable and provably side-effect free -- and the
        # surrounding transaction (including a test case's own) stays usable.
        try:
            with transaction.atomic():
                shared_pk = mutate()
                raise _DryRunComplete
        except _DryRunComplete:
            pass
        return BackfillResult(
            course=course,
            dry_run=True,
            shared_curriculum_id=shared_pk,
            counts=counts,
            conflicts=conflicts,
        )
    with transaction.atomic():
        shared_pk = mutate()
    return BackfillResult(
        course=course,
        dry_run=False,
        shared_curriculum_id=shared_pk,
        counts=counts,
        conflicts=conflicts,
    )


__all__ = (
    "BACKFILL_PARSER_VERSION",
    "BackfillResult",
    "SharedCurriculumBackfillError",
    "backfill_shared_curriculum",
)
