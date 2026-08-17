"""What a public catalogue reads out of a raw query string, and what it ignores.

Three public catalogues parse a raw query against a strict grammar: the shared
paginator's ``?page=``, the podcast's ``?season=`` and the events index's
``?filter=past``.  Each grammar matched the *whole* query string, so any
parameter it did not name made the request a bad request.

That is right for the selector and wrong for everything else.  A link to
``/blog?utm_source=newsletter`` is an ordinary link to the blog: the campaign
tag is addressed to an analytics tool, not to this site, and answering it with
a 400 breaks every tagged link anyone has ever posted.  There is no closed list
of those tags either — every tool invents its own, so an allowlist would only
move the breakage to the next vendor.

So a raw query is split here first: the parameters a catalogue actually selects
on are kept and handed to that catalogue's grammar unchanged, and everything
else is dropped before the grammar ever sees it.  The grammars stay exactly as
strict as they were about what they do read, which is what keeps
``?page=1&page=2`` a bad request rather than a coin toss.

Dropping is safe against duplicating the catalogue across a crawl: the page
already declares a canonical built from a path and a page number, so a tagged
URL points at the clean one.

The whole raw string is still bounded, so an enormous query is refused rather
than parsed.
"""

from __future__ import annotations

MAX_RAW_QUERY_LENGTH = 512


def _parameter_name(segment: str) -> str:
    return segment.split("=", 1)[0]


def selector_query(raw_query: object, *, selectors: frozenset[str]) -> str | None:
    """Return `raw_query` with every non-selector parameter removed.

    Returns ``None`` when the raw query cannot be read at all — the caller
    answers that with its own bad request, as it did before.  A query that held
    nothing but ignorable parameters comes back empty, which every grammar
    already treats as the unadorned first page.
    """

    if not isinstance(raw_query, str) or len(raw_query) > MAX_RAW_QUERY_LENGTH:
        return None
    return "&".join(
        segment for segment in raw_query.split("&") if _parameter_name(segment) in selectors
    )
