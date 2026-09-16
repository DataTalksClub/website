"""Canonical public paths and stable identifiers for podcast episodes."""

from __future__ import annotations

import re

# Every episode answers at /podcast/<stable id>/<tail>.  Episodes whose source
# slug embeds their own stable id keep the source key intact but never repeat
# the prefix in the public tail.
_STABLE_ID_PREFIX = re.compile(r"(?P<prefix>s[0-9]{2}e[0-9]{2})-(?P<tail>.+)\Z")


def podcast_public_id(*, season: int, episode: int) -> str:
    """Return the stable, display-safe episode identifier used in public routes."""

    return f"s{season:02d}e{episode:02d}"


def podcast_canonical_path(*, season: int, episode: int, slug: str) -> str:
    """Return the canonical ``/podcast/<stable id>/<tail>`` path for an episode."""

    stable_id = podcast_public_id(season=season, episode=episode)
    match = _STABLE_ID_PREFIX.fullmatch(slug)
    tail = match["tail"] if match is not None and match["prefix"] == stable_id else slug
    return f"/podcast/{stable_id}/{tail}"


# This episode was explicitly cut over to the hierarchical route without
# retaining either of the generated flat-slug aliases.  The other reviewed
# stable-ID routes retain their existing migration aliases until their separate
# contracts change.
PODCAST_GENAI_PILOTS_SLUG = "s24e04-from-genai-pilots-to-production"
PODCAST_HIERARCHICAL_ONLY_SLUGS = frozenset({PODCAST_GENAI_PILOTS_SLUG})
