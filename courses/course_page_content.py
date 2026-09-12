"""Editorial composition for the redesigned public course pages.

The cohort page renders the "6b" mockup of the design system
(DataTalksClub/website#179) and the family landing page the course-page mock of
2026-09. Every fact either page shows — title, description, dates, deadlines,
module titles, submission state, and counts — is read from the course records
and their homework and project rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class CourseSpec:
    """One label-over-value fact in the hero's dashed fact strip."""

    label: str
    value: str
    classes: str = ""


# How the design writes a course date: "Mon, Sep 14, 2026".
SPEC_DATE_FORMAT = "%a, %b %-d, %Y"

# How the family landing writes a span: compact, like the mock's register card —
# "Sep 14 — Jan 25, 2027" inside one year, "Sep 14, 2026 — Jan 25, 2027" across two.
FAMILY_DATE_FORMAT = "%b %-d, %Y"


def family_date_span(start: Any, end: Any) -> str:
    """Return a cohort's run as one compact span, weekday-free."""

    if start.strftime("%Y") == end.strftime("%Y"):
        return f"{start.strftime('%b %-d')} — {end.strftime(FAMILY_DATE_FORMAT)}"
    return f"{start.strftime(FAMILY_DATE_FORMAT)} — {end.strftime(FAMILY_DATE_FORMAT)}"


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


@dataclass(frozen=True, slots=True)
class FamilyEditionRow:
    """One visible edition in the family landing's cohort list, with its honest state."""

    cohort: Any
    projects: list
    index: str
    state_words: str
    state_pill_class: str


def family_edition_rows(
    editions: list,
    registration_cohort: Any,
    today: date,
) -> tuple[FamilyEditionRow, ...]:
    """Number the visible editions and name each one's state from its own record.

    The edition a campaign is actively promoting is "registration open"; a self-paced
    edition says so; a dated edition reads against today. Anything the data cannot
    place draws no pill at all rather than a guess, and the words are the state —
    the pill colour only reinforces them.
    """

    rows: list[FamilyEditionRow] = []
    for position, edition in enumerate(editions, start=1):
        cohort = edition.cohort
        if registration_cohort is not None and cohort.pk == registration_cohort.pk:
            words, variant = "registration open", "open"
        elif getattr(cohort, "delivery_mode", "") == "self_paced":
            words, variant = "self-paced", ""
        elif (
            cohort.start_date
            and cohort.end_date
            and cohort.start_date <= today <= cohort.end_date
        ):
            words, variant = "in progress", "live"
        elif cohort.end_date and cohort.end_date < today:
            words, variant = "finished", "wait"
        else:
            words, variant = "", ""
        rows.append(
            FamilyEditionRow(
                cohort=cohort,
                projects=edition.projects,
                index=f"{position:02d}",
                state_words=words,
                state_pill_class=f"status-pill-{variant}" if variant else "",
            )
        )
    return tuple(rows)


def family_registration_specs(
    cohort: Any,
    registered: int | None,
) -> tuple[CourseSpec, ...]:
    """The registration card's dashed fact rows, only from dates and counts that exist."""

    specs: list[CourseSpec] = []
    if cohort is not None and cohort.start_date and cohort.end_date:
        specs.append(
            CourseSpec(
                "runs",
                family_date_span(cohort.start_date, cohort.end_date),
            )
        )
    if registered is not None:
        specs.append(CourseSpec("registered", f"{registered} people"))
    return tuple(specs)


def family_facts(
    editions: list,
    duration_label: str,
    registered: int | None,
) -> tuple[str, ...]:
    """The hero's fact chips as plain phrases, read from the family's own rows.

    A chip is a fact the records can back: the front cohort's length, how many
    visible editions the family has, how many learner projects they hold, and the
    published registration total. Absent facts are skipped, never padded.
    """

    facts: list[str] = []
    if duration_label and duration_label != "TBA":
        facts.append(duration_label)
    if editions:
        facts.append(f"{len(editions)} cohorts")
    project_count = sum(len(edition.projects) for edition in editions)
    if project_count:
        facts.append(f"{project_count} learner projects")
    if registered is not None:
        facts.append(f"{registered} registered")
    return tuple(facts)


@dataclass(frozen=True, slots=True)
class FamilySyllabusRow:
    """One numbered row of the family landing's syllabus band."""

    index: str
    title: str
    summary: str = ""


# Curriculum modules and homework both arrive titled "Module 1: Agentic RAG"
# or "Homework 3: Orchestration"; the row's own mono index already says the
# number, so the spoken prefix would repeat it.
_UNIT_TITLE_PREFIX = re.compile(r"^(?:module|homework)\s+\d+\s*:\s*", re.IGNORECASE)


def family_syllabus_rows(units: list) -> tuple[FamilySyllabusRow, ...]:
    """Number the family's syllabus units in teaching order.

    A unit is a shared-curriculum module when the course's import created one,
    otherwise a homework row of the front cohort; both carry a title and,
    optionally, a one-line summary.  A unit the data cannot summarise is a
    title-only row rather than a padded one.
    """

    rows = []
    for position, unit in enumerate(units, start=1):
        title = _UNIT_TITLE_PREFIX.sub("", unit.title).strip()
        rows.append(
            FamilySyllabusRow(
                index=f"{position:02d}",
                title=title,
                summary=getattr(unit, "summary", "") or "",
            )
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class FamilyStory:
    """One graduate quote on the family landing, with its checkable attribution."""

    name: str
    quote: str
    attribution: str = ""
    portrait_url: str = ""
    source_url: str = ""


def family_story_rows(testimonials) -> tuple[FamilyStory, ...]:
    """Flatten published course testimonials into the rows the band renders."""

    return tuple(
        FamilyStory(
            name=testimonial.name,
            quote=testimonial.quote,
            attribution=testimonial.attribution,
            portrait_url=testimonial.portrait_url,
            source_url=testimonial.source_url,
        )
        for testimonial in testimonials
    )


@dataclass(frozen=True, slots=True)
class FamilyProjectCard:
    """One project of the edition the gallery band shows."""

    cohort: Any
    project: Any


def family_project_cards(editions: list) -> tuple[FamilyProjectCard, ...]:
    """The projects of the newest visible edition that actually holds some.

    ``editions`` arrives newest first, as the family landing's other bands read
    it, so the scan stops at the first edition whose project list is not empty
    and the gallery always shows one cohort's work rather than a mixture.
    """

    for edition in editions:
        if edition.projects:
            return tuple(
                FamilyProjectCard(cohort=edition.cohort, project=project)
                for project in edition.projects
            )
    return ()


def family_capstone_project(projects: list):
    """The edition's closing artifact: its last project, or none.

    A course's project list runs in submission order and ends with the
    capstone attempts, so the tail of the list is the closest thing the data
    has to "the capstone" without second-guessing an editor's titles.
    """

    return projects[-1] if projects else None
