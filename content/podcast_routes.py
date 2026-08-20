"""Canonical public paths for podcast episodes."""

from __future__ import annotations

PODCAST_ROUTE_MIGRATION_SLUG = "s24e05-ai-adoption-in-enterprise-beyond-writing-code"
PODCAST_ROUTE_MIGRATION_PATH = "/podcast/s24e05/ai-adoption-in-enterprise-beyond-writing-code"


def podcast_canonical_path(slug: str) -> str:
    """Return the reviewed canonical path for a podcast stable key."""

    if slug == PODCAST_ROUTE_MIGRATION_SLUG:
        return PODCAST_ROUTE_MIGRATION_PATH
    return f"/podcast/{slug}.html"


def podcast_legacy_path(slug: str) -> str:
    """Return the established .html path retained as a migration alias."""

    return f"/podcast/{slug}.html"
