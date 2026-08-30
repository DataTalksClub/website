"""The small, deterministic slug policy shared by Event imports and public links."""

from __future__ import annotations

import html
import re

_SLUG_PARTS = re.compile(r"[^a-z0-9]+")
MAX_EVENT_SLUG_LENGTH = 64


def event_title_slug(title: str) -> str:
    """Return the cosmetic slug for one Event title.

    Event titles are deliberately ASCII-normalised to match the checked legacy projection
    builder.  Unlike a model identity, this value may change whenever the title changes and
    is therefore never used to select an Event.
    """

    if not isinstance(title, str):
        raise ValueError("event title must be text")
    value = html.unescape(title).casefold().strip()
    if not value or "\x00" in value:
        raise ValueError("event title is empty or unsafe")
    slug = _SLUG_PARTS.sub("-", value).strip("-")
    if len(slug) > MAX_EVENT_SLUG_LENGTH:
        shortened = slug[:MAX_EVENT_SLUG_LENGTH].rstrip("-")
        # Prefer a complete title word. A single exceptionally long word still gets a
        # deterministic hard cut so every generated slug respects the URL budget.
        slug = shortened.rsplit("-", 1)[0] if "-" in shortened else shortened
    if not slug:
        raise ValueError("event title cannot produce a safe slug")
    return slug
