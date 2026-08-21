"""Editorial composition for the redesigned public courses index."""

from __future__ import annotations

from datetime import date


def cohort_weeks(start: date | None, end: date | None) -> int | None:
    """Return a run's length in whole weeks, or None when it has no bounded dates."""

    if start is None or end is None:
        return None
    days = (end - start).days + 1
    if days < 7:
        return None
    return max(1, round(days / 7))


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
