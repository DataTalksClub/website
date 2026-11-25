"""The taxonomy the ``/courses`` catalogue filters by, and the filter rules.

Site feedback 3.1 (``_docs/audits/2026-11-19-site-feedback.md``) asks the
catalogue to let a reader filter by what people pick a course by -- topic,
length, tools, level, format/status -- and to show every card with the same
fields in the same order.  The facts themselves are database-owned ``Course``
fields (``catalog_topic``, ``catalog_level``, ``catalog_duration_weeks``,
``catalog_hours_per_week``, ``catalog_tools``, ``catalog_project``,
``catalog_fits``), curated in the admin and loaded by
``scripts/prod/import_course_catalog_facets.py``.  This module only holds the
closed vocabularies those fields validate against and the pure filter logic the
view (and, mirrored, the page script) apply.

A family with no facts recorded still renders: its card omits what it lacks,
and the filters never hide it while no filter is active.
"""

from __future__ import annotations

from collections.abc import Mapping

from django.core.exceptions import ValidationError
from django.db import models


class CatalogTopic(models.TextChoices):
    DATA_ENGINEERING = "data-engineering", "Data Engineering"
    MACHINE_LEARNING = "machine-learning", "Machine Learning"
    MLOPS = "mlops", "MLOps"
    LLM_AI = "llm-ai", "LLMs & AI agents"
    AI_DEV_TOOLS = "ai-dev-tools", "AI for developers"
    FINANCE = "finance", "Finance"


class CatalogLevel(models.TextChoices):
    BEGINNER = "beginner", "Beginner-friendly"
    EXPERIENCED = "experienced", "Some experience needed"


class CatalogFit(models.TextChoices):
    """ "Which course fits me?" -- the situation a reader starts from."""

    NEW_TO_DATA = "new-to-data", "I'm new to data"
    DEVELOPER = "developer", "I'm a software developer"
    MODELS = "models", "I already work with models"
    FINANCE = "finance", "I'm into markets and finance"


FIT_HINTS: Mapping[str, str] = {
    CatalogFit.NEW_TO_DATA: "Start with the fundamentals and finish with a project.",
    CatalogFit.DEVELOPER: "Bring AI and LLMs into the code you already write.",
    CatalogFit.MODELS: "Take models from a notebook to production.",
    CatalogFit.FINANCE: "Apply data analysis and ML to trading.",
}

LENGTHS: tuple[tuple[str, str], ...] = (
    ("short", "Up to 6 weeks"),
    ("medium", "7–12 weeks"),
    ("long", "3+ months"),
)

STATUSES: tuple[tuple[str, str], ...] = (
    ("open", "Registration open"),
    ("running", "Running now"),
    ("next", "Next cohort soon"),
    ("self-paced", "Self-paced"),
)

#: The catalogue kind (``courses.views.course_list.CatalogCard.kind``) each
#: status filter value covers.
STATUS_BY_KIND: Mapping[str, str] = {
    "open_dated": "open",
    "active": "running",
    "open_undated": "next",
    "self_paced": "self-paced",
    "archived": "self-paced",
}

FILTER_KEYS: tuple[str, ...] = ("for", "topic", "level", "length", "status", "tool")

MAX_TOOLS = 5
MAX_TOOL_LENGTH = 40


def validate_catalog_tools(value) -> None:
    if not isinstance(value, list) or not all(isinstance(tool, str) for tool in value):
        raise ValidationError("Enter a list of tool names.")
    if len(value) > MAX_TOOLS:
        raise ValidationError(f"List at most {MAX_TOOLS} key tools.")
    for tool in value:
        if not tool.strip() or len(tool) > MAX_TOOL_LENGTH or not tool_key(tool):
            raise ValidationError(f"Tool names must be 1-{MAX_TOOL_LENGTH} characters.")
    if len({tool_key(tool) for tool in value}) != len(value):
        raise ValidationError("List each tool once.")


def validate_catalog_fits(value) -> None:
    if not isinstance(value, list) or not all(isinstance(fit, str) for fit in value):
        raise ValidationError("Enter a list of fit keys.")
    unknown = [fit for fit in value if fit not in CatalogFit.values]
    if unknown:
        raise ValidationError(f"Unknown fit keys; use: {', '.join(CatalogFit.values)}.")
    if len(set(value)) != len(value):
        raise ValidationError("List each fit once.")


def tool_key(tool: str) -> str:
    """The URL-safe value a tool chip carries, e.g. ``"LLM APIs"`` -> ``"llm-apis"``."""

    out = []
    for char in tool.lower():
        out.append(char if char.isalnum() else "-")
    return "-".join(part for part in "".join(out).split("-") if part)


def length_bucket(weeks: int | None) -> str:
    if not weeks:
        return ""
    if weeks <= 6:
        return "short"
    if weeks <= 12:
        return "medium"
    return "long"


def duration_label(weeks: int | None) -> str:
    """``10`` -> ``"10 weeks"``; past twelve weeks, months read better."""

    if not weeks:
        return ""
    if weeks <= 12:
        return f"{weeks} week{'s' if weeks != 1 else ''}"
    return f"About {round(weeks / 4.345)} months"


def card_filter_values(course, kind: str) -> dict[str, tuple[str, ...]]:
    """The values one catalogue card matches, per filter key."""

    status = STATUS_BY_KIND.get(kind, "")
    topic = getattr(course, "catalog_topic", "") or ""
    level = getattr(course, "catalog_level", "") or ""
    length = length_bucket(getattr(course, "catalog_duration_weeks", None))
    tools = getattr(course, "catalog_tools", None) or []
    fits = getattr(course, "catalog_fits", None) or []
    return {
        "for": tuple(fits),
        "topic": (topic,) if topic else (),
        "level": (level,) if level else (),
        "length": (length,) if length else (),
        "status": (status,) if status else (),
        "tool": tuple(tool_key(tool) for tool in tools),
    }


def parse_filters(query: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Read ``?topic=mlops,llm-ai&level=beginner`` into a clean selection.

    Unknown keys are ignored; values are split on commas, deduplicated and kept in
    the order given.  The page script reads the address the same way, so a shared
    link renders the same filtered grid with or without JavaScript.
    """

    selection: dict[str, tuple[str, ...]] = {}
    for key in FILTER_KEYS:
        raw = query.get(key, "") or ""
        values = []
        for value in raw.split(","):
            value = value.strip().lower()
            if value and value not in values:
                values.append(value)
        if values:
            selection[key] = tuple(values)
    return selection


def card_matches(card_values: Mapping[str, tuple[str, ...]], selection) -> bool:
    """OR within one filter, AND across filters."""

    for key, wanted in selection.items():
        if not set(wanted) & set(card_values.get(key, ())):
            return False
    return True
