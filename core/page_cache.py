"""Anonymous-only full-page cache for public Django responses.

The catalogue readers (``content.catalogue``), the event service
(``events.queries``) and the chrome readers already collapse their repeated
work inside one request (``core.request_cache``), but every anonymous request
still pays the same stamp aggregates, small row reads and template render as
the previous one: on the production link each of those database round trips
costs several milliseconds, so a warm ``/blog`` still spends ~100 ms
server-side and the heavier anonymous pages (``/``, ``/tour``, large podcast
episodes) stay over their budgets.

This module is the cross-request layer the intake requires: the first
anonymous ``GET`` of a public page renders normally and stores the bytes in
the Django cache; later anonymous ``GET``s of the same path and allowlisted
query are served with zero database queries and no template render.  The
entry key carries no content stamp at all, so a hit provably costs no round
trip.

Freshness comes from two bounds, documented here because the shape is
deliberate:

* any database write bumps a process-local generation counter, and the key
  names the generation, so the next anonymous request after a content sync,
  an event import, an admin edit or a settings change re-renders instead of
  serving the previous bytes on the worker that took the write;
* every entry carries a short TTL (60 s), which bounds staleness on the
  workers that did not take the write.

The invalidation receiver is intentionally global (``post_save`` /
``post_delete`` with no sender): a curated model list would silently go stale
whenever a new content-owned model is added, while over-invalidation only
costs a re-render and this site's write traffic is tiny next to its anonymous
reads.  Serving a public page performs no writes (the warm query budgets pin
read-only renders), so ordinary anonymous traffic never churns the cache.

What is never cached, by construction:

* anything but an anonymous ``GET`` of an explicitly listed public view with
  an allowlisted (or empty) query string — the same query grammar
  ``core.cache_policy`` reviews for shareability;
* any response for a credential-bearing request (authenticated user, session
  or CSRF cookie, authorization-style header, unknown cookie) or a private
  surface — those bypass lookup and store alike, and the outer
  ``ResponsePolicyMiddleware`` still forces ``private, no-store`` on them;
* anything but a ``200`` ``text/html`` ``HttpResponse`` without cookies.

``Vary: Cookie`` handling needs no code here: the stored entry predates the
outer ``SessionMiddleware`` response pass, which adds the ``Vary`` header to
served hits exactly as it does to fresh renders.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse

from core.cache_policy import QUERY_ALLOWLISTS, request_query_keys

logger = logging.getLogger(__name__)

#: How long a stored anonymous page may be served, in seconds.  The bound is
#: for the workers that did not take the invalidating write; the worker that
#: took it re-renders on its next request via the generation bump below.
PAGE_CACHE_TTL_SECONDS = 60

#: Code-owned key version.  Bump when the stored entry shape changes so older
#: entries (which carry a different version in their key) stop matching.
PAGE_CACHE_KEY_VERSION = 1

#: Largest body kept.  The biggest public page is a few hundred kilobytes;
#: anything larger re-renders rather than risking cache-memory pressure.
PAGE_CACHE_MAX_BODY_BYTES = 2 * 1024 * 1024

_CACHE_KEY_PREFIX = "anon-page"

#: Views whose anonymous ``GET`` responses may be stored and served.  This is
#: deliberately explicit rather than "every shareable registry class": feeds,
#: sitemaps, file assets and permanent redirects stay out of the page cache
#: (they are either tiny, non-HTML, or owned by the edge), and any view added
#: later is uncached until it is reviewed into this set.  The anonymous
#: ``/`` branch (public home) is covered by ``core.views.home``; the
#: authenticated branch never reaches lookup or store because its requests
#: are credential-bearing.
CACHEABLE_VIEWS = frozenset(
    {
        "core.views.home",
        "core.views.sponsors",
        "core.views.tour",
        "content.public_views.collection_hub",
        "content.public_views.events",
        "content.public_views.events_past",
        "content.public_views.event_detail",
        "content.public_views.event_detail_without_slug",
        "content.public_views.podcast_hub",
        "content.public_views.article_detail",
        "content.public_views.book_detail",
        "content.public_views.person_detail",
        "content.public_views.podcast_detail_by_id",
        "content.public_views.podcast_detail_by_id_without_slug",
        "content.public_views.wiki_hub",
        "content.public_views.wiki_detail",
        "content.public_views.wiki_graph",
        "content.public_views.wiki_special",
        "content.review_views.docs_home",
        "content.review_views.docs_page",
        "content.review_views.faq_home",
        "content.review_views.faq_course",
        "content.review_views.slack",
        "courses.views.course_list.course_list",
        "courses.views.course.course_family_view",
        "courses.views.shared_course.shared_lesson_view",
        "courses.views.shared_course.shared_module_view",
        "courses.views.wrapped.wrapped_view",
    }
)

_lock = threading.Lock()
_generation = 1


def _enabled() -> bool:
    """Whether the page cache takes part in this request at all."""

    return bool(getattr(settings, "ANONYMOUS_PAGE_CACHE_ENABLED", False))


def cache_generation() -> int:
    """The current invalidation generation (tests may read it)."""

    with _lock:
        return _generation


def reset_page_cache_state() -> None:
    """Restore the initial generation and drop stored pages (tests only)."""

    global _generation
    with _lock:
        _generation = 1
    cache.clear()


def invalidate_anonymous_page_cache() -> None:
    """Orphan every stored anonymous page so the next lookup re-renders.

    Old generations expire by TTL; the bump itself is one locked integer, so
    a content sync that writes hundreds of rows only churns the cache once
    per worker instead of serving any of the pre-sync bytes afterwards.
    """

    global _generation
    with _lock:
        _generation += 1


def _handle_model_write(**kwargs: Any) -> None:
    """Bump the generation on any database write while the cache is on.

    Reads never reach this receiver, and serving a public page performs no
    writes, so bumping on every write cannot churn the cache under anonymous
    traffic; logins, registrations and syncs are rare by comparison.  The
    early return keeps the receiver a no-op in tests and in environments
    where the flag is off.
    """

    del kwargs
    if not _enabled():
        return
    invalidate_anonymous_page_cache()


def _resolved_view_path(request: HttpRequest) -> str | None:
    """Resolve the view that will serve this request, without raising.

    New-style middleware runs before Django sets ``request.resolver_match``
    (resolution happens just ahead of the view), so the cache resolves the
    path itself instead of relying on handler ordering.  Anything
    unresolvable bypasses the cache.
    """

    from django.urls import Resolver404, resolve

    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    func = match.func
    view_class = getattr(func, "view_class", None)
    if view_class is not None:
        return f"{view_class.__module__}.{view_class.__name__}"
    module = getattr(func, "__module__", None)
    name = getattr(func, "__name__", None)
    if not module or not name:
        return None
    return f"{module}.{name}"


def _query_is_cacheable(view_path: str, raw_query_string: str) -> bool:
    """Only the reviewed query grammar may address a stored page.

    Mirrors ``core.middleware.response_may_stay_shareable``: a shareable view
    absent from the query map admits no query at all, so only its canonical
    path is cacheable; a listed view admits exactly its reviewed selectors.
    """

    if not raw_query_string:
        return True
    allowlist = QUERY_ALLOWLISTS.get(view_path)
    if allowlist is None:
        return False
    return request_query_keys(raw_query_string) <= allowlist


def lookup_key(request: HttpRequest) -> str | None:
    """The cache key for this request, or ``None`` when it must bypass.

    Every ``None`` direction is fail-closed: unresolvable views, unlisted
    views, credentialed or private requests, and non-canonical queries all
    render normally.
    """

    if not _enabled() or request.method != "GET":
        return None
    view_path = _resolved_view_path(request)
    if view_path is None or view_path not in CACHEABLE_VIEWS:
        return None
    # Credential and privacy policy lives in core.middleware; this module
    # shares it rather than restating who counts as anonymous, so a new
    # credential cookie shape stays private here automatically.
    from core.middleware import _is_credential_bearing_request, _is_private_surface

    if _is_private_surface(request) or _is_credential_bearing_request(request):
        return None
    raw_query = request.META.get("QUERY_STRING", "")
    if not _query_is_cacheable(view_path, raw_query):
        return None
    query_hash = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()
    return (
        f"{_CACHE_KEY_PREFIX}:v{PAGE_CACHE_KEY_VERSION}"
        f":g{cache_generation()}:{view_path}:{request.path_info}:{query_hash}"
    )


def _storable_response(response: HttpResponse) -> bool:
    if response.status_code != 200:
        return False
    if getattr(response, "streaming", False):
        return False
    content_type = response.get("Content-Type", "")
    if not content_type.startswith("text/html"):
        return False
    if response.cookies:
        return False
    try:
        body = response.content
    except (AttributeError, ValueError):
        return False
    if not isinstance(body, (bytes, bytearray)) or len(body) > PAGE_CACHE_MAX_BODY_BYTES:
        return False
    return True


def store_response(key: str, response: HttpResponse) -> None:
    """Store a freshly rendered anonymous page under its lookup key."""

    if not _enabled() or not _storable_response(response):
        return
    entry = {
        "status": response.status_code,
        "body": bytes(response.content),
        "content_type": response.get("Content-Type", "text/html; charset=utf-8"),
        "cache_control": response.get("Cache-Control", ""),
    }
    try:
        cache.set(key, entry, PAGE_CACHE_TTL_SECONDS)
    except Exception:  # A cache backend outage must never fail a page.
        logger.warning("anonymous page cache store failed", exc_info=True)


def cached_response(key: str) -> HttpResponse | None:
    """Rebuild a stored anonymous page, or ``None`` on a miss or outage."""

    try:
        entry = cache.get(key)
    except Exception:  # A cache backend outage renders normally.
        logger.warning("anonymous page cache lookup failed", exc_info=True)
        return None
    if not isinstance(entry, dict):
        return None
    try:
        body = entry["body"]
        status = entry["status"]
        content_type = entry["content_type"]
    except KeyError:
        return None
    if not isinstance(body, (bytes, bytearray)) or status != 200:
        return None
    response = HttpResponse(content=bytes(body), status=status, content_type=content_type)
    cache_control = entry.get("cache_control", "")
    if cache_control:
        response["Cache-Control"] = cache_control
    response["X-Page-Cache"] = "HIT"
    return response


class AnonymousPageCacheMiddleware:
    """Serve and store anonymous public pages around the view.

    The middleware sits inside ``AuthenticationMiddleware`` (so
    ``request.user`` is resolved) and inside ``ResponsePolicyMiddleware`` (so
    the outer privacy policy still classifies served hits against the live
    request).  Lookup happens before the view: a hit returns without touching
    the database or the template.  Store happens after the view: only a
    ``200`` ``text/html`` response to the same anonymous request that passed
    lookup is kept, so credentialed responses can never enter the cache.
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        key = lookup_key(request)
        if key is None:
            return self.get_response(request)
        hit = cached_response(key)
        if hit is not None:
            return hit
        response = self.get_response(request)
        # The request was anonymous at lookup and Django does not
        # authenticate mid-view for these routes; re-checking here would only
        # re-read the same request state, while the outer policy middleware
        # still forces private on anything credentialed on the way out.
        store_response(key, response)
        return response
