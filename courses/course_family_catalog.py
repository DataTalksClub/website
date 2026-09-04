"""Reviewed identity mapping for the adopted course-edition catalogue.

The source catalogue uses edition slugs (for example, ``de-zoomcamp-2026``),
while the application exposes a reusable family and an explicit cohort year.
This mapping is deliberately data-owned rather than inferred by stripping a
suffix at runtime.

A course *repository* names itself after its GitHub repository, which is not
always the public family slug: ``DataTalksClub/ai-dev-tools-zoomcamp`` declares
``slug: ai-dev-tools-zoomcamp`` in its ``course.yaml`` while the course is
published as ``ai-dev-tools``.  Projecting the repository slug verbatim minted a
second family row for the same course and split its cohorts across both.  The
owner has ruled that the published AI Dev Tools family is ``ai-dev-tools``: a
repository's ``-zoomcamp`` suffix never mints a family beside an existing one, and
the duplicate is merged into the published slug rather than aliased alongside it.
The five families that *are* published as ``…-zoomcamp`` keep their slugs, because
those are what ``courses.datatalks.club`` serves.  This module owns the single
normalization used by every writer of a family slug.
"""

from __future__ import annotations

from courses.migration_family_identity import (
    REPOSITORY_FAMILY_SUFFIX,
    duplicate_family_identities,
    family_identity,
)

__all__ = [
    "COHORT_FAMILY_IDENTITIES",
    "COURSE_FAMILY_TITLES",
    "REPOSITORY_FAMILY_SUFFIX",
    "canonical_family_slug",
    "cohort_family_identity",
    "course_family_title",
    "duplicate_family_identities",
    "family_identity",
    "family_slug_variants",
]


COHORT_FAMILY_IDENTITIES: dict[str, tuple[str, int]] = {
    "de-zoomcamp-2022": ("de-zoomcamp", 2022),
    "de-zoomcamp-2023": ("de-zoomcamp", 2023),
    "de-zoomcamp-2024": ("de-zoomcamp", 2024),
    "de-zoomcamp-2025": ("de-zoomcamp", 2025),
    "de-zoomcamp-2026": ("de-zoomcamp", 2026),
    "ml-zoomcamp-2021": ("ml-zoomcamp", 2021),
    "ml-zoomcamp-2022": ("ml-zoomcamp", 2022),
    "ml-zoomcamp-2023": ("ml-zoomcamp", 2023),
    "ml-zoomcamp-2024": ("ml-zoomcamp", 2024),
    "ml-zoomcamp-2025": ("ml-zoomcamp", 2025),
    "llm-zoomcamp-2024": ("llm-zoomcamp", 2024),
    "llm-zoomcamp-2025": ("llm-zoomcamp", 2025),
    "mlops-zoomcamp-2022": ("mlops-zoomcamp", 2022),
    "mlops-zoomcamp-2023": ("mlops-zoomcamp", 2023),
    "mlops-zoomcamp-2024": ("mlops-zoomcamp", 2024),
    "mlops-zoomcamp-2025": ("mlops-zoomcamp", 2025),
    "sma-zoomcamp-2024": ("sma-zoomcamp", 2024),
    "sma-zoomcamp-2025": ("sma-zoomcamp", 2025),
    "sma-zoomcamp-2026": ("sma-zoomcamp", 2026),
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


# ``family_identity`` and ``duplicate_family_identities`` live in the frozen
# migration module so the repair migration can replay without importing mutable
# runtime code.  ``ai-dev-tools`` and ``ai-dev-tools-zoomcamp`` share the identity
# ``ai-dev-tools``, which is exactly the collision that split the AI Dev Tools
# catalogue in two.  The reviewed families ``de-zoomcamp``, ``ml-zoomcamp``,
# ``llm-zoomcamp``, ``mlops-zoomcamp`` and ``sma-zoomcamp`` keep their published
# slugs; they simply must not gain a de-suffixed twin.


def family_slug_variants(family_slug: str) -> tuple[str, ...]:
    """Return every family slug that shares ``family_slug``'s identity."""

    identity = family_identity(family_slug)
    return (identity, f"{identity}{REPOSITORY_FAMILY_SUFFIX}")


def canonical_family_slug(source_slug: str) -> str:
    """Return the published family slug for a source course slug.

    A slug already in the reviewed catalogue is authoritative and returned
    unchanged, so ``ml-zoomcamp`` stays ``ml-zoomcamp``.  Otherwise a repository
    slug whose de-suffixed form *is* a reviewed family resolves to that family,
    so ``ai-dev-tools-zoomcamp`` resolves to ``ai-dev-tools``.  An unknown slug
    is returned unchanged so local fixtures keep working.
    """

    if source_slug in COURSE_FAMILY_TITLES:
        return source_slug
    identity = family_identity(source_slug)
    if identity != source_slug and identity in COURSE_FAMILY_TITLES:
        return identity
    return source_slug


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
