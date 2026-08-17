from __future__ import annotations

from collections.abc import Sized
from datetime import date

from django import template
from django.forms import BoundField
from django.utils.dateparse import parse_date, parse_datetime

register = template.Library()


@register.filter
def human_date(value: object) -> str:
    raw = str(value)
    parsed_datetime = parse_datetime(raw)
    if parsed_datetime is not None:
        zone = parsed_datetime.tzname() or "UTC"
        return (
            f"{parsed_datetime:%B} {parsed_datetime.day}, {parsed_datetime:%Y} "
            f"at {parsed_datetime:%H:%M} {zone}"
        )
    parsed = parse_date(raw[:10])
    if parsed is None:
        return str(value)
    return f"{parsed:%B} {parsed.day}, {parsed:%Y}"


def _publication_day(value: object) -> date | None:
    """Read the calendar day out of a publication date, in either stored shape.

    ``human_date`` renders a clock reading whenever the value parses as a datetime,
    and ``parse_datetime`` accepts a bare ``YYYY-MM-DD`` as midnight.  A publication
    date has no time in it: the articles catalogue stores a day, and the books
    catalogue stores that same day padded with ``T00:00:00``.  Surfaces that publish
    the day read it through here, so they never show an hour the record does not
    carry — and so the machine value and the visible text cannot disagree.
    """

    return parse_date(str(value)[:10])


@register.filter
def human_day(value: object) -> str:
    """Render a publication date as the calendar day it is, and nothing more."""

    parsed = _publication_day(value)
    if parsed is None:
        return str(value)
    return f"{parsed:%B} {parsed.day}, {parsed:%Y}"


@register.filter
def day_and_month(value: object) -> str:
    """The day half of a publication date: ``August 11``.

    The archive rails set the date on two lines, day above year, so that every
    rail is the same height whatever the month is called.  Read as one line,
    ``January 25, 2026`` wraps and ``August 11, 2025`` does not, and a column of
    rows then sits at two different heights for no reason a reader can see.
    """

    parsed = _publication_day(value)
    if parsed is None:
        return str(value)
    return f"{parsed:%B} {parsed.day}"


@register.filter
def year_only(value: object) -> str:
    """The year half of a publication date, the second line of a date rail."""

    parsed = _publication_day(value)
    if parsed is None:
        return ""
    return f"{parsed:%Y}"


@register.filter
def iso_day(value: object) -> str:
    """The machine-readable counterpart of ``human_day``, for ``<time datetime>``."""

    parsed = _publication_day(value)
    if parsed is None:
        return str(value)
    return f"{parsed:%Y-%m-%d}"


@register.filter
def counted(value: object, noun: str) -> str:
    """Name a count in the words a reader reads: ``12 questions``.

    A count with nothing in it has nothing to say, so it renders as the empty
    string and the pill, line or label that would have carried it does not
    appear at all.  This exists because the shared archive row takes its pill as
    one piece of text: a template can count and it can pluralise, but it cannot
    join the two back together without three nested ``{% with %}`` blocks.

    ``noun`` is one word, or the singular and the plural separated by a comma
    when adding an ``s`` is wrong: ``|counted:"entry,entries"``.
    """

    if isinstance(value, Sized):
        total = len(value)
    elif isinstance(value, int) and not isinstance(value, bool):
        total = value
    else:
        return ""
    if not total:
        return ""
    singular, _, plural = noun.partition(",")
    return f"{total} {singular if total == 1 else (plural or f'{singular}s')}"


@register.filter
def human_datetime(value: object) -> str:
    parsed = parse_datetime(str(value))
    if parsed is None:
        return str(value)
    zone = parsed.tzname() or "UTC"
    return f"{parsed:%B} {parsed.day}, {parsed:%Y} at {parsed:%H:%M} {zone}"


def field_help_id(field: BoundField) -> str:
    return f"{field.id_for_label}-help"


def field_error_id(field: BoundField) -> str:
    return f"{field.id_for_label}-error"


@register.simple_tag
def accessible_widget(field: BoundField):
    """Render a bound field with deterministic help and error relationships."""

    described_by: list[str] = []
    if field.help_text:
        described_by.append(field_help_id(field))
    if field.errors:
        described_by.append(field_error_id(field))

    attrs: dict[str, str | bool] = {}
    existing = field.field.widget.attrs.get("aria-describedby", "")
    if existing:
        described_by.extend(str(existing).split())
    if described_by:
        attrs["aria-describedby"] = " ".join(dict.fromkeys(described_by))
    if field.errors:
        attrs["aria-invalid"] = "true"
        attrs["aria-errormessage"] = field_error_id(field)
    return field.as_widget(attrs=attrs)


@register.simple_tag
def accessibility_help_id(field: BoundField) -> str:
    return field_help_id(field)


@register.simple_tag
def accessibility_error_id(field: BoundField) -> str:
    return field_error_id(field)


@register.inclusion_tag("accessibility/error_summary.html")
def accessibility_error_summary(form) -> dict[str, object]:
    return {"form": form}
