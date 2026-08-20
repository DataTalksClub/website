"""Build the public representation of a module-format curriculum.

The public course page already receives homework and project objects decorated
with learner-specific state.  This adapter only supplies their curriculum
position and the module-owned unit metadata; it deliberately does not rebuild
any homework or project presentation logic.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from django.db.models import Prefetch

from courses.models.cohort import Cohort, CurriculumFormat
from courses.models.curriculum import CurriculumFlowItem, Module, Unit
from courses.models.homework import Homework
from courses.models.project import Project


@dataclass(frozen=True, slots=True)
class ModuleFlowItem:
    """One module and its ordered units, ending in terminal homework."""

    position: int
    module: Module
    units: tuple[Unit, ...]
    homework: Homework
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
    """Return a deterministic public flow for a module-format cohort.

    ``homeworks`` and ``projects`` are the already decorated lists from the
    public page loaders.  Matching by primary key keeps their deadline,
    status, links, and learner submission state intact while the flow query
    contributes only ordering and module/unit ownership.

    Legacy cohorts intentionally return an empty tuple without querying flow
    rows.  A malformed flow target that is absent from the current public list
    is omitted rather than rendered with incomplete learner state.
    """

    if cohort.curriculum_format != CurriculumFormat.MODULES:
        return ()

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
