"""Editorial composition for the redesigned public course page.

The course page renders the "6b" mockup of the design system (DataTalksClub/website#179).
Every fact it shows — title, description, dates, deadlines, module titles, submission state,
and counts — is read from the cohort record and its homework and project rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CourseSpec:
    """One label-over-value fact in the hero's dashed fact strip."""

    label: str
    value: str
    classes: str = ""


# How the design writes a course date: "Mon, Sep 14, 2026".
SPEC_DATE_FORMAT = "%a, %b %-d, %Y"


def course_specs(
    course: Any,
    homework_count: int,
    project_count: int,
    signup_count: int | None,
) -> tuple[CourseSpec, ...]:
    """Return only the facts this course actually has.

    Every value is read from the course record or counted from its own rows.  A course
    without a start date simply has no "starts" fact; the strip is never padded with a
    placeholder, and an empty strip is not rendered at all.
    """

    specs: list[CourseSpec] = []
    if course.start_date:
        specs.append(
            CourseSpec("starts", course.start_date.strftime(SPEC_DATE_FORMAT), "spec-date")
        )
    if course.end_date:
        specs.append(CourseSpec("ends", course.end_date.strftime(SPEC_DATE_FORMAT), "spec-date"))
    duration = getattr(course, "home_duration_label", "TBA")
    if duration and duration != "TBA":
        specs.append(CourseSpec("length", duration))
    if homework_count:
        specs.append(CourseSpec("homework", str(homework_count)))
    if project_count:
        specs.append(CourseSpec("projects", str(project_count)))
    if signup_count:
        specs.append(CourseSpec("registered", f"{signup_count} people"))
    return tuple(specs)


@dataclass(frozen=True, slots=True)
class CourseModule:
    """One unit of course work: a homework or a project, in deadline order.

    The page draws homework and projects as two tables of their own rows, so this
    numbering is no longer rendered; it is how the learner's submitted-module count
    below is taken across both lists at once.
    """

    number: str
    kind: str
    item: Any


def course_modules(homeworks: list, projects: list) -> tuple[CourseModule, ...]:
    """Number the course's homework and project rows as one continuous module list.

    The page shows homework first and projects after, which is the order the two
    context builders already return and the order the two tables render.
    """

    modules: list[CourseModule] = []
    for kind, items in (("homework", homeworks), ("project", projects)):
        for item in items:
            modules.append(
                CourseModule(
                    number=f"{len(modules) + 1:02d}",
                    kind=kind,
                    item=item,
                )
            )
    return tuple(modules)


@dataclass(frozen=True, slots=True)
class SubmissionProgress:
    """How much of the course this signed-in learner has handed in."""

    submitted: int
    total: int
    percent: int


def submission_progress(modules: tuple[CourseModule, ...]) -> SubmissionProgress | None:
    """Return the learner's own submitted-module count, or ``None`` when there is nothing to count.

    Every number comes from the same ``submitted`` flag the module rows render, so the bar can
    never disagree with the list above it.
    """

    total = len(modules)
    if not total:
        return None
    submitted = sum(1 for module in modules if getattr(module.item, "submitted", False))
    return SubmissionProgress(
        submitted=submitted,
        total=total,
        percent=round(submitted * 100 / total),
    )
