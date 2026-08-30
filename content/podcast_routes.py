"""Canonical public paths and stable identifiers for podcast episodes."""

from __future__ import annotations

PODCAST_ROUTE_MIGRATION_SLUG = "s24e05-ai-adoption-in-enterprise-beyond-writing-code"
PODCAST_ROUTE_MIGRATION_PATH = "/podcast/s24e05/ai-adoption-in-enterprise-beyond-writing-code"
PODCAST_AI_PRODUCTION_SLUG = "s24e06-how-to-build-ai-that-actually-ships-in-production"
PODCAST_AI_PRODUCTION_PATH = "/podcast/s24e06/how-to-build-ai-that-actually-ships-in-production"

PODCAST_STABLE_ROUTES = {
    PODCAST_ROUTE_MIGRATION_SLUG: PODCAST_ROUTE_MIGRATION_PATH,
    PODCAST_AI_PRODUCTION_SLUG: PODCAST_AI_PRODUCTION_PATH,
}


def podcast_canonical_path(slug: str) -> str:
    """Return the reviewed canonical path for a podcast stable key."""

    return PODCAST_STABLE_ROUTES.get(slug, f"/podcast/{slug}.html")


def podcast_public_id(*, season: int, episode: int) -> str:
    """Return the stable, display-safe episode identifier used in public routes."""

    return f"s{season:02d}e{episode:02d}"


def podcast_legacy_path(slug: str) -> str:
    """Return the established .html path retained as a migration alias."""

    return f"/podcast/{slug}.html"
