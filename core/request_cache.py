"""Per-request memoization for catalogue stamps and derived reads.

Public pages fan out to many small readers (``content.catalogue``,
``content.wiki_reader``, ``content.docs_reader``, ``events.queries``,
``content.event_content``) that each re-derive the same cheap aggregate
"stamp" (``COUNT(*)``/``MAX(updated_at)``) binding their process-local
``lru_cache`` entries.  A single homepage render recomputed the same stamp
dozens of times: ~35-50 stamp queries per page, each a database round trip
on production Postgres.

This module holds one dict per request, carried on a :class:`ContextVar` so
async and threaded servers both isolate it.  Readers memoize only the
*stamp* (and the small fully-derived tuples built from already-cached rows),
never the records themselves: the existing ``lru_cache`` entries stay the
content authority and their stamp-keyed invalidation contract is unchanged.
A sync still moves the stamp, and the next request rebuilds instead of
serving stale pages.

Outside a request (management commands, tests calling readers directly) the
context holds no dict and every reader behaves exactly as before: one live
aggregate per call.  That keeps the cache-binding tests' semantics -- a
write followed by a read in the same process sees fresh data -- while
requests collapse dozens of duplicate aggregates into one per source.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from contextvars import ContextVar, Token
from typing import Any

#: The active request's memo dict, or ``None`` outside a request.  The
#: middleware (``core.middleware.RequestCacheMiddleware``) binds a fresh dict
#: at the start of every request and resets it afterwards, so entries never
#: cross requests, workers, or tests.
_request_memo: ContextVar[dict[Hashable, Any] | None] = ContextVar("dtc_request_memo", default=None)


def bind_request_memo() -> Token:
    """Bind a fresh memo dict for one request; reset it with the token."""

    return _request_memo.set({})


def reset_request_memo(token: Token) -> None:
    """Release one request's memo dict."""

    _request_memo.reset(token)


def memoized[T](key: Hashable, compute: Callable[[], T]) -> T:
    """Return the memoized value for ``key``, computing and storing it once."""

    memo = _request_memo.get()
    if memo is None:
        return compute()
    if key not in memo:
        memo[key] = compute()
    return memo[key]  # type: ignore[no-any-return]


def current_memo() -> dict[Hashable, Any] | None:
    """Return the active request's memo dict, or ``None`` outside a request."""

    return _request_memo.get()
