from __future__ import annotations

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
