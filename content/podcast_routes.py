"""Canonical public paths for podcast seasons and episodes.

A season answers at ``/podcast/s18/`` and an episode at
``/podcast/s18/e09-<slug>``.  The season and episode numbers are the episode's
identity; the title slug after ``e09-`` is cosmetic.  Any other slug, and the
short ``/podcast/s18/e09`` form, redirect permanently to the canonical
(``content.public_urls``).  Episodes whose source slug embeds their own
``s18e09-`` prefix keep the source key intact but never repeat it in the
public tail.
"""

from __future__ import annotations

import re

_STABLE_ID_PREFIX = re.compile(r"(?P<prefix>s[0-9]{2}e[0-9]{2})-(?P<tail>.+)\Z")

#: Route pattern fragments (``content.public_urls``).  The numbers are matched
#: as written; the views compare the request to the canonical spelling.
SEASON_PATTERN = r"s(?P<season>[0-9]{2,4})"
EPISODE_PATTERN = r"e(?P<episode>[0-9]{2,4})"


def podcast_season_token(season: int) -> str:
    """The ``s18`` path segment of a season."""

    return f"s{season:02d}"


def podcast_episode_token(episode: int) -> str:
    """The ``e09`` part of an episode's path segment."""

    return f"e{episode:02d}"


def podcast_season_canonical_path(season: int) -> str:
    """Return the ``/podcast/s18/`` path of one season's page."""

    return f"/podcast/{podcast_season_token(season)}/"


def podcast_canonical_path(*, season: int, episode: int, slug: str) -> str:
    """Return the canonical ``/podcast/s18/e09-<slug>`` path for an episode."""

    season_token = podcast_season_token(season)
    episode_token = podcast_episode_token(episode)
    match = _STABLE_ID_PREFIX.fullmatch(slug)
    tail = (
        match["tail"]
        if match is not None and match["prefix"] == f"{season_token}{episode_token}"
        else slug
    )
    return f"/podcast/{season_token}/{episode_token}-{tail}"
