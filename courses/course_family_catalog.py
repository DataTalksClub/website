"""Reviewed identity mapping for the adopted course-edition catalogue.

The source catalogue uses edition slugs (for example, ``de-zoomcamp-2026``),
while the application exposes a reusable family and an explicit cohort year.
This mapping is deliberately data-owned rather than inferred by stripping a
suffix at runtime.
"""

from __future__ import annotations


COHORT_FAMILY_IDENTITIES: dict[str, tuple[str, int]] = {
    "de-zoomcamp-2024": ("de-zoomcamp", 2024),
    "de-zoomcamp-2025": ("de-zoomcamp", 2025),
    "de-zoomcamp-2026": ("de-zoomcamp", 2026),
    "ml-zoomcamp-2024": ("ml-zoomcamp", 2024),
    "ml-zoomcamp-2025": ("ml-zoomcamp", 2025),
    "llm-zoomcamp-2024": ("llm-zoomcamp", 2024),
    "llm-zoomcamp-2025": ("llm-zoomcamp", 2025),
    "mlops-zoomcamp-2024": ("mlops-zoomcamp", 2024),
    "mlops-zoomcamp-2025": ("mlops-zoomcamp", 2025),
    "sma-zoomcamp-2024": ("sma-zoomcamp", 2024),
    "sma-zoomcamp-2025": ("sma-zoomcamp", 2025),
    "ai-dev-tools-2025": ("ai-dev-tools", 2025),
}

COURSE_FAMILY_TITLES: dict[str, str] = {
    "de-zoomcamp": "Data Engineering Zoomcamp",
    "ml-zoomcamp": "Machine Learning Zoomcamp",
    "llm-zoomcamp": "LLM Zoomcamp",
    "mlops-zoomcamp": "MLOps Zoomcamp",
    "sma-zoomcamp": "Stock Markets Analytics Zoomcamp",
    "ai-dev-tools": "AI Dev Tools Zoomcamp",
}


def cohort_family_identity(cohort_slug: str) -> tuple[str, int]:
    """Return the reviewed family slug and year for a source cohort slug."""

    try:
        return COHORT_FAMILY_IDENTITIES[cohort_slug]
    except KeyError as error:
        raise ValueError(f"unmapped cohort slug: {cohort_slug}") from error


def course_family_title(family_slug: str, cohort_title: str = "") -> str:
    """Return the family title, with a safe fallback for local fixtures."""

    if family_slug in COURSE_FAMILY_TITLES:
        return COURSE_FAMILY_TITLES[family_slug]
    if cohort_title:
        return cohort_title.rsplit(" ", 1)[0]
    return family_slug.replace("-", " ").title()
