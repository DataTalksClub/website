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
    """One edition card on the family landing's strip, with its honest state."""

    cohort: Any
    state_words: str
    state_pill_class: str


def family_edition_rows(
    editions: list,
    registration_cohort: Any,
    today: date,
) -> tuple[FamilyEditionRow, ...]:
    """Name each visible edition's state from its own record.

    The edition a campaign is actively promoting reads "registration open" only
    while it genuinely hasn't started yet — the same reading
    ``courses.views.dashboard_context.dashboard_lifecycle_status`` gives one
    cohort's own hero pill. Once its start date has passed, the promoted
    edition is "in progress" like any other, because the campaign still
    pointing at it doesn't make the cohort any less under way. A self-paced
    edition says so; any other dated edition reads against today. Anything the
    data cannot place draws no pill at all rather than a guess, and the words
    are the state — the pill colour only reinforces them.
    """

    rows: list[FamilyEditionRow] = []
    for edition in editions:
        cohort = edition.cohort
        is_promoted = registration_cohort is not None and cohort.pk == registration_cohort.pk
        started = bool(cohort.start_date) and cohort.start_date <= today
        if is_promoted and not started:
            words, variant = "registration open", "open"
        elif getattr(cohort, "delivery_mode", "") == "self_paced":
            words, variant = "self-paced", ""
        elif cohort.end_date and cohort.end_date < today:
            words, variant = "finished", "wait"
        elif started:
            words, variant = "in progress", "live"
        else:
            words, variant = "", ""
        rows.append(
            FamilyEditionRow(
                cohort=cohort,
                state_words=words,
                state_pill_class=f"status-pill-{variant}" if variant else "",
            )
        )
    return tuple(rows)


def split_current_edition(
    rows: tuple[FamilyEditionRow, ...],
) -> tuple[FamilyEditionRow | None, tuple[FamilyEditionRow, ...]]:
    """Pull the one edition that's actually live right now out of the strip.

    Owner feedback (2026-09): a visitor doesn't need "finished" or "in
    progress" spelled out on every card -- the cohort that's open for
    registration or already under way is the one worth a prominent card of
    its own, and everything else is honestly just "previous cohorts", no
    per-card state word required.
    """

    current = next(
        (row for row in rows if row.state_words in ("registration open", "in progress")),
        None,
    )
    previous = tuple(row for row in rows if row is not current)
    return current, previous


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


@dataclass(frozen=True, slots=True)
class FamilySyllabusRow:
    """One numbered row of the family landing's syllabus band."""

    index: str
    title: str
    summary: str = ""
    url: str = ""


# Curriculum modules and homework both arrive titled "Module 1: Agentic RAG"
# or "Homework 3: Orchestration"; the row's own mono index already says the
# number, so the spoken prefix would repeat it.
_UNIT_TITLE_PREFIX = re.compile(r"^(?:module|homework)\s+\d+\s*:\s*", re.IGNORECASE)


def family_syllabus_rows(
    units: list,
    *,
    urls: list[str] | None = None,
) -> tuple[FamilySyllabusRow, ...]:
    """Number the family's syllabus units in teaching order.

    A unit is a shared-curriculum module when the course's import created one,
    otherwise a homework row of the front cohort; both carry a title and,
    optionally, a one-line summary.  A unit the data cannot summarise is a
    title-only row rather than a padded one.
    """

    rows = []
    destinations = urls or [""] * len(units)
    for position, (unit, url) in enumerate(zip(units, destinations, strict=True), start=1):
        title = _UNIT_TITLE_PREFIX.sub("", unit.title).strip()
        rows.append(
            FamilySyllabusRow(
                index=f"{position:02d}",
                title=title,
                summary=(
                    getattr(unit, "summary", "")
                    or getattr(unit, "description", "")
                    or ""
                ),
                url=url,
            )
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class FamilyStory:
    """One graduate quote on the family landing, with its checkable attribution.

    ``role_before``/``role_after``/``elapsed`` mirror the Testimonial model:
    optional, because not every real quote states a role transition, and the
    shared story card must not invent one when they are empty.
    """

    name: str
    quote: str
    attribution: str = ""
    portrait_url: str = ""
    source_url: str = ""
    role_before: str = ""
    role_after: str = ""
    elapsed: str = ""


def family_story_rows(testimonials) -> tuple[FamilyStory, ...]:
    """Flatten published course testimonials into the rows the band renders."""

    return tuple(
        FamilyStory(
            name=testimonial.name,
            quote=testimonial.quote,
            attribution=testimonial.attribution,
            portrait_url=testimonial.portrait_url,
            source_url=testimonial.source_url,
            role_before=testimonial.role_before,
            role_after=testimonial.role_after,
            elapsed=testimonial.elapsed,
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


@dataclass(frozen=True, slots=True)
class FamilyOutcomeStat:
    """One number in the family landing's honest outcome strip."""

    value: str
    label: str


def family_outcome_stats(
    enrolled_count: int,
    certificate_count: int,
    submission_count: int,
    since_year: int | None,
    *,
    registration_count: int | None = None,
) -> tuple[FamilyOutcomeStat, ...]:
    """The family's published participation/outcome numbers, only for what the data has.

    A campaign's published registration aggregate is the leading count when
    available. Otherwise the page falls back to live enrollments across the
    family's visible cohorts. No estimate or rounding is introduced. A family
    with neither a published registration count nor an enrollment has nothing
    honest to show, so the whole strip is omitted.

    Certificates are gated separately from the other two counts: a family
    whose cohorts never reliably populated ``Enrollment.certificate_url``
    (a self-paced-only family, or one whose current cohort hasn't finished)
    would otherwise show a misleading "0 certificates issued" beside two
    real, nonzero numbers -- so that one stat alone is dropped when it is
    zero, instead of hiding the strip the other two counts can still stand on.
    """

    if registration_count is None and not enrolled_count:
        return ()
    if registration_count is not None:
        noun = "registration" if registration_count == 1 else "registrations"
        stats = [FamilyOutcomeStat(f"{registration_count:,}", noun)]
    else:
        since = f" since {since_year}" if since_year else ""
        stats = [FamilyOutcomeStat(f"{enrolled_count:,}", f"enrolled{since}")]
    if certificate_count:
        noun = "certificate" if certificate_count == 1 else "certificates"
        stats.append(FamilyOutcomeStat(f"{certificate_count:,}", f"{noun} issued"))
    if submission_count:
        noun = "project submission" if submission_count == 1 else "project submissions"
        stats.append(FamilyOutcomeStat(f"{submission_count:,}", noun))
    return tuple(stats)
