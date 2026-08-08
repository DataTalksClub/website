from __future__ import annotations

import re
from urllib.parse import urlsplit

PRODUCTION_CANONICAL_HOST = "datatalks.club"
_ENCODED_AMBIGUOUS_CHARACTER = re.compile(r"%(?:0[0-9a-f]|5c|7f)", re.IGNORECASE)


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
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
    ):
        return ""
    return value
