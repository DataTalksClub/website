from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.db.models import Count, Max
from django.utils import timezone

from events.queries import published_event_records

from . import catalogue
from .public_graph import validate_wiki_graph
from .public_text import strip_leaked_target_attributes, target_attribute_count

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


_EVENT_UUID_PATH = re.compile(
    r"^/events/(?P<identity>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)"
)


def _apply_runtime_event_public_paths(
    projection: dict[str, Any], event_paths: Mapping[str, str]
) -> None:
    """Point projected people relationships at the database-owned event URLs.

    Person records still carry the UUID event paths their source was built with,
    while the running site addresses an event by its stable numeric
    ``Event.public_id``. One query resolves every referenced identity rather than
    turning a person page into a round trip per relationship.

    A relationship naming an event the database does not have keeps its own path:
    the person page is not the place to discover that an event is missing, and a
    dead link there is better than a 500.
    """

    people = projection.get("people", ())
    replacements = {f"/events/{identity}": path for identity, path in event_paths.items()}
    if not replacements:
        return
    for person in people:
        person["relationships"] = tuple(
            {
                **relationship,
                "public_path": _rewritten_event_path(
                    relationship.get("public_path", ""), replacements
                ),
            }
            for relationship in person.get("relationships", ())
        )


def _rewritten_event_path(path: object, replacements: Mapping[str, str]) -> object:
    if not isinstance(path, str):
        return path
    match = _EVENT_UUID_PATH.match(path)
    if match is None:
        return path
    return replacements.get(f"/events/{match.group('identity')}", path)


#: The registered source whose active release publishes the editorial
#: catalogue. Named here as well so the offline import keeps one spelling of it.
PUBLIC_CONTENT_STABLE_ID = catalogue.PUBLIC_CONTENT_STABLE_ID
#: Records that are one document each, keyed by slug within their collection.
_COLLECTION_KINDS = {name: name.rstrip("s") or name for name in COLLECTION_NAMES}
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
    """The catalogue with the database-owned URL adapters applied."""

    return _adapted_catalogue(catalogue.active_release_id(), _event_public_path_stamp())


def _event_public_path_stamp() -> tuple[int, str]:
    """A cheap stamp that moves whenever an event public path could have.

    One aggregate, not a row per event. Reading all 421 events on the way to
    every request, just to decide whether the adapted catalogue is still valid,
    costs far more than rebuilding it on the rare occasion this changes.
    """

    from events.models import Event

    try:
        stamp = Event.objects.aggregate(total=Count("id"), latest=Max("updated_at"))
    except DatabaseError:
        return (0, "")
    return (int(stamp["total"] or 0), str(stamp["latest"] or ""))


def _event_public_paths() -> dict[str, str]:
    """The canonical public path of every event that has one."""

    from events.models import Event

    try:
        return {
            str(event_id): f"/events/{public_id}/{slug}"
            for event_id, public_id, slug in Event.objects.exclude(public_id=None).values_list(
                "id", "public_id", "slug"
            )
        }
    except DatabaseError:
        return {}


@lru_cache(maxsize=2)
def _adapted_catalogue(release_id: str, event_stamp: tuple[int, str]) -> dict[str, Any]:
    """The catalogue with the database-owned URL adapters applied.

    Cached on the pair that can change it: the active editorial release, and a
    stamp over the event rows the people adapter rewrites paths to.
    """

    # Runtime URL adapters replace values in a handful of nested structures.  Copy only the
    # records they mutate rather than deep-copying the entire (large) content projection on every
    # request; the checked file-backed projection remains immutable and safely cached.
    source = _checked_public_projection()
    projection = dict(source)
    projection["podcasts"] = tuple(dict(podcast) for podcast in source["podcasts"])

    # The checked projection flattened Markdown links before runtime loading.  Build a provenance
    # allowlist from that immutable source so only its known leaked blocks opt into metadata
    # removal; an arbitrary attached ``literal{:target="blank"}`` value never does.
    leaked_blocks: dict[str, dict[str, frozenset[int]]] = {}
    for collection in ("articles", "people"):
        collection_allowlist: dict[str, frozenset[int]] = {}
        marker_count = 0
        for record in source[collection]:
            block_indexes = frozenset(
                index
                for index, block in enumerate(record.get("blocks", ()))
                if isinstance(block, dict)
                and isinstance(block.get("text"), str)
                and target_attribute_count(block["text"]) > 0
            )
            marker_count += sum(
                target_attribute_count(block.get("text", ""))
                for block in record.get("blocks", ())
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
            collection_allowlist[record["slug"]] = block_indexes
        if marker_count != EXPECTED_LEAKED_TARGET_MARKERS[collection]:
            raise ImproperlyConfigured("Public projection leaked target marker count mismatch.")
        leaked_blocks[collection] = collection_allowlist

    def copy_body_record(
        record: dict[str, Any],
        *,
        collection: str,
    ) -> dict[str, Any]:
        copied = dict(record)
        raw_blocks = record.get("blocks")
        if isinstance(raw_blocks, (list, tuple)):
            copied_blocks: list[Any] = []
            allowed_indexes = leaked_blocks[collection].get(record["slug"], frozenset())
            for index, raw_block in enumerate(raw_blocks):
                if not isinstance(raw_block, dict):
                    copied_blocks.append(raw_block)
                    continue
                block = dict(raw_block)
                text = block.get("text")
                if isinstance(text, str) and index in allowed_indexes:
                    block["text"] = strip_leaked_target_attributes(
                        text,
                        validated_projection=True,
                    )
                copied_blocks.append(block)
            copied["blocks"] = copied_blocks
        return copied

    projection["articles"] = tuple(
        copy_body_record(article, collection="articles") for article in source["articles"]
    )
    projection["people"] = tuple(
        {
            **copy_body_record(person, collection="people"),
            "relationships": tuple(
                dict(relationship) for relationship in person.get("relationships", ())
            ),
        }
        for person in source["people"]
    )
    _apply_runtime_event_public_paths(projection, _event_public_paths())
    # The adapters mutate copied article/people records; refresh their lookup indexes as well so
    # detail pages and relationship tests do not retain references to checked source records.
    projection["articles_by_slug"] = {
        article["slug"]: article for article in projection["articles"]
    }
    projection["articles_by_path"] = {
        article["public_path"]: article for article in projection["articles"]
    }
    projection["people_by_slug"] = {person["slug"]: person for person in projection["people"]}
    projection["people_by_path"] = {
        person["public_path"]: person for person in projection["people"]
    }
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
    paths.update(
        record["public_path"]
        for name in ("articles", "people", "wiki")
        for record in projection[name]
    )
    paths.update(record["public_path"] for record in catalogue.books())
    paths.update(record["public_path"] for record in catalogue.podcasts())
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
