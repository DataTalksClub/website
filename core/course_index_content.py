"""Editorial composition for the redesigned public courses index.

The courses index renders the "6a" mockup from DataTalksClub/website#179 in the shared
design 5a system.  Every number the page shows — the size of the catalogue, the homework
total, the cohort length range, the week an enrolled learner is in, the dates of a run —
is derived from the course records the view already loaded.  An element whose fact is
missing is left out rather than filled with a plausible number.

The one editorial thing here is the promise lines, which the homepage already owns in
:mod:`core.home_content` and which this page reuses so the same course makes the same
promise on both surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.home_content import COURSE_FAMILIES, spelled_count

DEFAULT_COURSE_FILTER = "all"
# Filter slug, pill label, and the section the filter keeps.  The pills are links, so the
# control works without JavaScript and exposes its state through aria-current.
COURSE_FILTER_CHOICES: tuple[tuple[str, str], ...] = (
    ("all", "All courses"),
    ("active", "Active"),
    ("open", "Open registration"),
    ("finished", "Finished"),
)


@dataclass(frozen=True, slots=True)
class CourseFilter:
    """One pill in the courses-index segmented control."""

    slug: str
    label: str
    url: str
    is_current: bool


def selected_course_filter(raw: object) -> str:
    """Return a known filter slug, treating anything unexpected as "everything"."""

    candidate = str(raw or "").strip().casefold()
    for slug, _label in COURSE_FILTER_CHOICES:
        if candidate == slug:
            return slug
    return DEFAULT_COURSE_FILTER


def course_filters(selected: str, base_url: str) -> tuple[CourseFilter, ...]:
    """Build the pill row; the default filter keeps the page's own bare URL."""

    filters: list[CourseFilter] = []
    for slug, label in COURSE_FILTER_CHOICES:
        url = base_url if slug == DEFAULT_COURSE_FILTER else f"{base_url}?filter={slug}"
        filters.append(
            CourseFilter(
                slug=slug,
                label=label,
                url=url,
                is_current=slug == selected,
            )
        )
    return tuple(filters)


def course_promise(slug: str) -> str:
    """Return what this course's family promises you will ship, or "" when unknown.

    The promise is editorial copy for the six zoomcamp families the homepage already
    describes.  A course outside them simply renders without the line.
    """

    for family, _title, _label, promise in COURSE_FAMILIES:
        if slug == family or slug.startswith(f"{family}-"):
            return promise
    return ""


def cohort_weeks(start: date | None, end: date | None) -> int | None:
    """Return a run's length in whole weeks, or None when it has no bounded dates."""

    if start is None or end is None:
        return None
    days = (end - start).days + 1
    if days < 7:
        return None
    return max(1, round(days / 7))


def catalog_eyebrow(course_count: int) -> str:
    """The hero's mono eyebrow: how many courses there are, and what they cost."""

    return f"{spelled_count(course_count)} courses · zero tuition"


def _day_display(value: date, *, with_year: bool) -> str:
    if with_year:
        return f"{value:%b} {value.day}, {value:%Y}"
    return f"{value:%b} {value.day}"


def cohort_dates_display(start: date | None, end: date | None) -> str:
    """Write a run's dates the way the design does, or "" when there are none."""

    if start is None:
        return ""
    if end is None:
        return f"starts {_day_display(start, with_year=True)}"
    same_year = start.year == end.year
    opening = _day_display(start, with_year=not same_year)
    return f"{opening} – {_day_display(end, with_year=True)}"


def enrolled_state_label(
    *,
    enrolled: bool,
    start: date | None,
    end: date | None,
    today: date,
) -> str:
    """Return the quiet status pill for a learner's own enrolment, or ""."""

    if not enrolled:
        return ""
    total = cohort_weeks(start, end)
    if total is None or start is None or end is None:
        return "enrolled"
    if today < start or today > end:
        return "enrolled"
    week = min(total, (today - start).days // 7 + 1)
    return f"enrolled · week {week} of {total}"
