"""Build the public representation of a cohort's module-shaped curriculum.

The public course page already receives homework and project objects decorated
with learner-specific state.  This adapter only supplies their curriculum
position and the module-owned unit metadata; it deliberately does not rebuild
any homework or project presentation logic.

A ``modules``-format cohort owns its Module/Unit/CurriculumFlowItem rows directly.
A ``shared``-format cohort instead places the course's one current shared curriculum
graph through ``CohortSharedModule``; its module row is a ``SharedModule`` (no
cohort-owned units to show here -- those live on the shared module's own canonical
page) and each placement carries its own terminal-homework binding rather than the
module owning one.  Both shapes render through the same ``ModuleFlowItem``, with
``url`` pointing at whichever page is each format's real module destination.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from django.db.models import Prefetch
from django.urls import reverse

from courses.models.cohort import Cohort, CurriculumFormat
from courses.models.curriculum import CurriculumFlowItem, Module, Unit
from courses.models.homework import Homework
from courses.models.project import Project
from courses.models.shared_curriculum import CohortSharedModule, SharedModule


@dataclass(frozen=True, slots=True)
class ModuleFlowItem:
    """One module and its ordered units, ending in terminal homework."""

    position: int
    module: Module | SharedModule
    units: tuple[Unit, ...]
    homework: Homework
    url: str
    kind: Literal["module"] = "module"


@dataclass(frozen=True, slots=True)
class ProjectFlowItem:
    """A project at its configured top-level curriculum position."""

    position: int
    project: Project
    kind: Literal["project"] = "project"


CurriculumFlowEntry: TypeAlias = ModuleFlowItem | ProjectFlowItem


def build_curriculum_flow(
    cohort: Cohort,
    homeworks: Iterable[Homework],
    projects: Iterable[Project],
) -> tuple[CurriculumFlowEntry, ...]:
    """Return a deterministic public flow for a module-shaped cohort.

    ``homeworks`` and ``projects`` are the already decorated lists from the
    public page loaders.  Matching by primary key keeps their deadline,
    status, links, and learner submission state intact while the flow query
    contributes only ordering and module/unit ownership.

    Legacy cohorts intentionally return an empty tuple without querying flow
    rows.  A malformed flow target that is absent from the current public list
    is omitted rather than rendered with incomplete learner state.
    """

    if cohort.curriculum_format == CurriculumFormat.MODULES:
        return _modules_flow(cohort, homeworks, projects)
    if cohort.curriculum_format == CurriculumFormat.SHARED:
        return _shared_flow(cohort, homeworks)
    return ()


def _modules_flow(
    cohort: Cohort,
    homeworks: Iterable[Homework],
    projects: Iterable[Project],
) -> tuple[CurriculumFlowEntry, ...]:
    homeworks_by_id = {homework.pk: homework for homework in homeworks}
    projects_by_id = {project.pk: project for project in projects}
    flow_items = (
        CurriculumFlowItem.objects.filter(cohort=cohort)
        .select_related("module", "module__terminal_homework", "project")
        .prefetch_related(
            Prefetch(
                "module__units",
                queryset=Unit.objects.order_by("position", "id"),
                to_attr="public_flow_units",
            )
        )
        .order_by("position", "id")
    )

    flow: list[CurriculumFlowEntry] = []
    for flow_item in flow_items:
        if flow_item.module_id is not None:
            module = flow_item.module
            homework = homeworks_by_id.get(module.terminal_homework_id)
            if homework is None:
                continue
            units = tuple(getattr(module, "public_flow_units", ()))
            flow.append(
                ModuleFlowItem(
                    position=flow_item.position,
                    module=module,
                    units=units,
                    homework=homework,
                    url=reverse(
                        "cohort_module",
                        kwargs={
                            "course_slug": cohort.course.slug,
                            "cohort_identifier": cohort.identifier,
                            "module_slug": module.slug,
                        },
                    ),
                )
            )
        elif flow_item.project_id is not None:
            project = projects_by_id.get(flow_item.project_id)
            if project is None:
                continue
            flow.append(
                ProjectFlowItem(
                    position=flow_item.position,
                    project=project,
                )
            )

    return tuple(flow)


def _shared_flow(
    cohort: Cohort,
    homeworks: Iterable[Homework],
) -> tuple[CurriculumFlowEntry, ...]:
    """Return the flow for a cohort placing the course's shared curriculum.

    A shared module has no cohort-owned units to prefetch here -- its lesson list
    lives on the module's own canonical ``/courses/<family>/<module>`` page, which
    is also where its link points, never the modules-format ``cohort_module`` route
    that no ``SharedModule`` row resolves against.  A placement with no matching,
    currently-visible homework (self-paced, or a homework the public list dropped)
    is omitted rather than rendered with a broken assignment link.
    """

    homeworks_by_id = {homework.pk: homework for homework in homeworks}
    placements = (
        CohortSharedModule.objects.filter(cohort=cohort)
        .select_related("shared_module", "terminal_homework")
        .order_by("position", "id")
    )

    flow: list[CurriculumFlowEntry] = []
    for placement in placements:
        homework = homeworks_by_id.get(placement.terminal_homework_id)
        if homework is None:
            continue
        module = placement.shared_module
        flow.append(
            ModuleFlowItem(
                position=placement.position,
                module=module,
                units=(),
                homework=homework,
                url=reverse(
                    "shared_module",
                    kwargs={
                        "course_slug": cohort.course.slug,
                        "module_slug": module.slug,
                    },
                ),
            )
        )

    return tuple(flow)
