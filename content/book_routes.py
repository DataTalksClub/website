"""Canonical public paths for Book of the Week pages.

A book's source slug -- its identity in the content repository and the
``SyncedDocument`` stable key -- starts with the date of its week
(``20230807-driving-data-quality-with-data-contracts``).  The date is not part of
the public address: a book answers at ``/books/<slug without the date>.html``,
the shape of every other dateless detail page (``/blog/<slug>.html``,
``/people/<slug>.html``).  The dated address it was published under first stays
a permanent redirect, resolved from the same source slug (``book_source_slug``
route in ``content.public_urls``).
"""

from __future__ import annotations

import re

#: The week's date the content repository prefixes every book slug with.
_DATE_PREFIX = re.compile(r"(?P<date>[0-9]{8})-(?P<tail>.+)\Z")

#: The dated public spelling, as a route pattern fragment (``public_urls``).
DATED_BOOK_SLUG_PATTERN = r"[0-9]{8}-[-a-zA-Z0-9_]+"


def book_public_slug(source_slug: str) -> str:
    """The source slug without its leading ``YYYYMMDD-`` week date."""

    match = _DATE_PREFIX.fullmatch(source_slug)
    return match["tail"] if match is not None else source_slug


def book_canonical_path(source_slug: str) -> str:
    """The canonical ``/books/<dateless slug>.html`` path for a book."""

    return f"/books/{book_public_slug(source_slug)}.html"
