from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from events.queries import published_event_records

from . import catalogue
from .public_graph import validate_wiki_graph

#: The wiki's default social-card image. It is a design asset that ships with
#: the app, not editorial content, so it stays a file; the route that serves it
#: still checks the published manifest before handing it over. The reviewed
#: ingest tree keeps its own copy because its digest covers it, and that copy is
#: evidence rather than something a request reads. Nothing else in this module
#: reads a file -- the catalogue is database rows.
WIKI_ASSET_ROOT = Path(__file__).with_name("wiki_assets")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

#: The count keys the empty catalogue's manifest declares, so a page reading
#: "how many articles are there" gets a zero rather than a missing key.
REQUIRED_COUNT_KEYS = frozenset(
    {
        "articles",
        "podcasts",
        "transcripts",
        "books",
        "people",
        "wiki",
        "courses",
        "media",
    }
)
# The accepted projection audit found these exact supported metadata markers.  Runtime cleanup
# uses this canary to ensure the immutable-source allowlist does not silently broaden or drift.
# Articles are zero because their bodies are now projected by the article block builder, which
# removes the legacy `{:target="_blank"}` directive at build time instead of leaving 270 of them
# in the published text for the runtime to clean up.  Person bios still take the older plain-text
# path, so their ten markers are still removed here.
EXPECTED_LEAKED_TARGET_MARKERS = {"articles": 0, "people": 10}


#: The selection mode the empty catalogue reports, matching what a published
#: one carries.
EXPECTED_SELECTION = "preferred"

COLLECTION_NAMES = catalogue.COLLECTION_NAMES
EVENT_TYPE_ICONS = {
    "conference": "fas fa-briefcase",
    "podcast": "fas fa-microphone-alt",
    "webinar": "fas fa-tv",
    "workshop": "fas fa-wrench",
}


@dataclass(frozen=True, slots=True)
class EventGroups:
    upcoming: tuple[dict[str, Any], ...]
    recent: tuple[dict[str, Any], ...]
    upcoming_groups: tuple[EventDateGroup, ...] = ()
    recent_groups: tuple[EventDateGroup, ...] = ()


@dataclass(frozen=True, slots=True)
class EventDateGroup:
    """Events sharing the displayed local calendar date.

    The public projection stores timezone-aware ISO timestamps.  The event hub displays dates
    in the site's established Europe/Berlin timezone, so grouping must happen after conversion
    rather than by slicing the UTC source string.  ``key`` is deliberately a stable ISO date
    used only for accessible DOM identifiers.
    """

    key: str
    date: date
    display_date: str
    weekday: str
    events: tuple[dict[str, Any], ...]


@lru_cache(maxsize=1)
def _empty_public_projection() -> dict[str, Any]:
    """Return the default empty catalogue used when no projection is present.

    Hubs render empty, detail lookups miss (404), sitemaps list only static
    paths.  No ImproperlyConfigured is raised: an absent snapshot is the normal
    state, not a deployment failure.  The migration helper under
    temporary/content/ is read only when explicitly requested.
    """

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "selection_mode": EXPECTED_SELECTION,
        "counts": {key: 0 for key in REQUIRED_COUNT_KEYS},
        "wiki_assets": {},
        "absent": True,
    }
    projection: dict[str, Any] = {"manifest": manifest}
    for name in COLLECTION_NAMES:
        projection[name] = ()
        projection[f"{name}_by_slug"] = {}
        projection[f"{name}_by_path"] = {}
    projection["podcast_platforms"] = ()
    projection["wiki_graph"] = {"nodes": [], "links": [], "counts": {"nodes": 0, "links": 0}}
    projection["wiki_search"] = {"docs": []}
    projection["editorial_route_migration"] = {"finals": [], "aliases": []}
    projection["editorial_route_aliases_by_path"] = {}
    return projection


#: The registered source whose active release publishes the editorial
#: catalogue. Named here as well so the offline import keeps one spelling of it.
PUBLIC_CONTENT_STABLE_ID = catalogue.PUBLIC_CONTENT_STABLE_ID
#: Records that are one document each, keyed by slug within their collection.
_COLLECTION_KINDS = catalogue.COLLECTION_KINDS
#: Records the projection carries exactly one of. They are stored as a single
#: document apiece rather than pretending to be a collection of one.
_SINGLETON_KINDS = (
    "manifest",
    "podcast_platforms",
    "wiki_graph",
    "wiki_search",
    "editorial_route_migration",
)


def _checked_public_projection() -> dict[str, Any]:
    """Return the published catalogue, rebuilt only when the release changes."""

    return _editorial_catalogue(catalogue.active_release_id())


@lru_cache(maxsize=2)
def _editorial_catalogue(release_id: str) -> dict[str, Any]:
    """Reassemble the old projection dictionary from the database catalogue.

    This is the compatibility shape, not a second way to read the database:
    every record here comes from ``content.catalogue``. It exists only for the
    surfaces that have not been given their own query yet, and it goes away with
    the last of them.

    A database publishing nothing yields the empty catalogue -- hubs render
    empty, detail lookups 404.

    ``release_id`` is the cache key, not an argument this reads: the catalogue
    resolves the active release itself, and naming it here is what makes the
    cached result follow an import instead of outliving it.
    """

    singletons = {name: catalogue.singleton(name) for name in _SINGLETON_KINDS}
    collections = {name: catalogue.records(kind) for name, kind in _COLLECTION_KINDS.items()}
    if not any(singletons.values()) and not any(collections.values()):
        return _empty_public_projection()

    projection: dict[str, Any] = dict(singletons)
    projection["podcast_platforms"] = catalogue.podcast_platforms()
    # The graph is drawn as links on a public page, so its destinations are
    # checked where they are read. A stored graph that fails this is a refusal,
    # not something the page renders and hopes about.
    validate_wiki_graph(projection["wiki_graph"])

    for name, records in collections.items():
        projection[name] = records
        projection[f"{name}_by_slug"] = {item["slug"]: item for item in records if "slug" in item}
        projection[f"{name}_by_path"] = {
            item["public_path"]: item for item in records if "public_path" in item
        }
    projection["editorial_route_aliases_by_path"] = {
        str(alias["source_path"]): alias
        for alias in projection["editorial_route_migration"].get("aliases", ())
        if isinstance(alias, dict) and isinstance(alias.get("source_path"), str)
    }
    return projection


def _adapted_public_projection() -> dict[str, Any]:
    """The catalogue with the records their own query services own."""

    return _adapted_catalogue(catalogue.active_release_id())


@lru_cache(maxsize=2)
def _adapted_catalogue(release_id: str) -> dict[str, Any]:
    """The compatibility dictionary, with articles and profiles taken as read.

    Both are published records the reader sees differently from the way they are
    stored -- a body with its source's link metadata cleaned out, a profile
    pointed at the live event routes -- and ``content.catalogue`` is what decides
    that now. This only re-indexes them for the surfaces still reading keys.
    """

    projection = dict(_checked_public_projection())
    for name, records in (("articles", catalogue.articles()), ("people", catalogue.people())):
        projection[name] = records
        projection[f"{name}_by_slug"] = {item["slug"]: item for item in records}
        projection[f"{name}_by_path"] = {item["public_path"]: item for item in records}
    return projection


def public_projection() -> dict[str, Any]:
    """Return the checked projection with database-owned public URL adapters applied.

    A database with no active editorial release returns the empty catalogue: hubs
    render empty, detail lookups miss (404), sitemaps list only static paths.
    """

    try:
        return _adapted_public_projection()
    except ImproperlyConfigured as exc:
        if "absent" in str(exc).lower():
            return _empty_public_projection()
        raise


def event_groups(now: datetime | None = None) -> EventGroups:
    current = now or timezone.now()
    if timezone.is_naive(current):
        current = timezone.make_aware(current)
    upcoming: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    for raw in published_event_records():
        event = {**raw, "starts_at_value": datetime.fromisoformat(raw["starts_at"])}
        local_start = event["starts_at_value"].astimezone(ZoneInfo("Europe/Berlin"))
        event["display_time"] = f"{local_start:%b} {local_start.day}, {local_start:%Y, %H:%M %Z}"
        event["display_date"] = f"{local_start:%b} {local_start.day}, {local_start:%Y}"
        event["display_clock"] = f"{local_start:%H:%M %Z}"
        event["type_icon"] = EVENT_TYPE_ICONS.get(
            event["type"].casefold(),
            "fas fa-calendar-check",
        )
        (upcoming if event["starts_at_value"] >= current else recent).append(event)

    # Keep ties deterministic even when two events share a title-derived slug.  UUID is the
    # immutable final tie-breaker, so a source reorder cannot change the rendered catalogue.
    def tie_breaker(item: dict[str, Any]) -> tuple[str, str]:
        return item["title"].casefold(), str(item.get("identity_id", ""))

    upcoming.sort(key=tie_breaker)
    upcoming.sort(key=lambda item: item["starts_at_value"])
    recent.sort(key=tie_breaker)
    recent.sort(key=lambda item: item["starts_at_value"], reverse=True)
    return EventGroups(
        tuple(upcoming),
        tuple(recent),
        _event_date_groups(upcoming),
        _event_date_groups(recent, descending=True),
    )


def event_date_groups(
    events: list[dict[str, Any]], *, descending: bool = False
) -> tuple[EventDateGroup, ...]:
    grouped: dict[date, list[dict[str, Any]]] = {}
    for event in events:
        local_date = event["starts_at_value"].astimezone(ZoneInfo("Europe/Berlin")).date()
        grouped.setdefault(local_date, []).append(event)

    return tuple(
        EventDateGroup(
            key=local_date.isoformat(),
            date=local_date,
            display_date=f"{local_date:%b} {local_date.day}, {local_date:%Y}",
            weekday=local_date.strftime("%A"),
            events=tuple(items),
        )
        for local_date, items in sorted(grouped.items(), reverse=descending)
    )


# Keep the implementation detail private for callers that only need EventGroups while exposing
# a small pure helper for the paginated event view and deterministic unit tests.
_event_date_groups = event_date_groups


def public_paths() -> tuple[str, ...]:
    projection = public_projection()
    paths = {
        "/",
        "/blog",
        "/podcast",
        "/books",
        "/events",
        "/events/past",
        "/courses",
        "/wiki",
        "/docs/",
        "/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
        "/faq/",
        "/faq/ai-dev-tools-zoomcamp.html",
    }
    paths.update(record["public_path"] for record in projection["wiki"])
    paths.update(record["public_path"] for record in catalogue.people())
    paths.update(record["public_path"] for record in catalogue.books())
    paths.update(record["public_path"] for record in catalogue.podcasts())
    paths.update(record["public_path"] for record in catalogue.articles())
    paths.update(record["public_path"] for record in published_event_records())
    paths.update(
        {
            "/wiki/graph",
            "/wiki/search",
            "/wiki/special-pages",
            "/wiki/feed.xml",
            "/wiki/sitemap.xml",
        }
    )
    paths.update(
        f"/wiki/special-pages/{category}"
        for category in ("guides", "comparisons", "roadmaps", "transitions", "how-tos")
    )
    return tuple(sorted(paths))
