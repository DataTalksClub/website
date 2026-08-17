from __future__ import annotations

import re
from urllib.parse import urlsplit

PRODUCTION_CANONICAL_HOST = "datatalks.club"
_ENCODED_AMBIGUOUS_CHARACTER = re.compile(r"%(?:0[0-9a-f]|5c|7f)", re.IGNORECASE)
_PODCAST_SEASON_QUERY = re.compile(r"season=([1-9][0-9]{0,8})\Z", re.ASCII)
_EVENTS_QUERY = re.compile(r"filter=past(?:&page=([1-9][0-9]{0,2}))?\Z", re.ASCII)
_PUBLIC_PAGE_QUERY = re.compile(r"page=([1-9][0-9]{0,2})\Z", re.ASCII)
# The public catalogues that share one paginator (issue #178).  Kept here rather than
# imported from `content.pagination` so the canonical validator stays a leaf with no
# application dependency; `content/tests/test_public_pagination.py` pins the two lists
# to each other.
_PUBLIC_PAGINATION_PATHS = frozenset({"/blog", "/books", "/events/past", "/wiki"})


def _is_normalized_podcast_season(path: str, query: str) -> bool:
    if path != "/podcast":
        return False
    return _PODCAST_SEASON_QUERY.fullmatch(query) is not None


def _is_normalized_events_filter(path: str, query: str) -> bool:
    if path != "/events":
        return False
    return _EVENTS_QUERY.fullmatch(query) is not None


def _is_normalized_public_page(path: str, query: str) -> bool:
    return path in _PUBLIC_PAGINATION_PATHS and _PUBLIC_PAGE_QUERY.fullmatch(query) is not None


def validated_canonical_url(value: object) -> str:
    """Return an approved production canonical, or fail closed with no value."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2_048
        or "\\" in value
        or any(
            character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
        or _ENCODED_AMBIGUOUS_CHARACTER.search(value)
    ):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or parsed.netloc != PRODUCTION_CANONICAL_HOST
        or parsed.hostname != PRODUCTION_CANONICAL_HOST
        or parsed.username is not None
        or parsed.password is not None
        or (
            parsed.query
            and not _is_normalized_podcast_season(parsed.path, parsed.query)
            and not _is_normalized_events_filter(parsed.path, parsed.query)
            and not _is_normalized_public_page(parsed.path, parsed.query)
        )
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
    ):
        return ""
    return value
