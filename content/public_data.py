from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.utils import timezone

from events.identity import EventIdentityError, load_identity_manifest
from events.slugs import event_title_slug

from .event_description_bridge import (
    EVENT_RECORD_SCHEMA_VERSION,
    EXPECTED_EVENTS_WITH_LINKS,
    EXPECTED_EVENTS_WITHOUT_LINKS,
    EXPECTED_MATCH_COUNT,
    EXPECTED_NON_LUMA_LINKS,
    EventDescriptionBridgeError,
    bridge_manifest_binding,
    load_event_description_bridge,
    validate_projected_event,
)
from .public_text import strip_leaked_target_attributes, target_attribute_count

PROJECTION_ROOT = Path(__file__).with_name("public_projection")
PODCAST_PLATFORM_FILENAME = "podcast_platforms.json"
EXPECTED_PODCAST_PLATFORM_PROVIDERS = (
    "apple",
    "spotify",
    "youtube",
    "spotify_for_creators",
)
EVENT_IDENTITY_MANIFEST = PROJECTION_ROOT.parents[1] / "events" / "event_identity_manifest.json"
ACCEPTED_UUID_IDENTITY_BINDING = {
    "path": "events/event_identity_manifest.json",
    "sha256": "26c2fd6589ff8acd132ebef0b31d15a81dc43901e1ba0b2e4437e72aeab5d91e",
    "schema_version": 1,
    "counts": {"events": 421, "aliases": 421},
}
EDITORIAL_ROUTE_MIGRATION_FILENAME = "editorial_route_migration.json"
EDITORIAL_ROUTE_MIGRATION_SCHEMA = (
    PROJECTION_ROOT.parents[1] / "_docs" / "compatibility" / "editorial-route-migration.schema.json"
)
EXPECTED_COUNTS = {
    "articles": 55,
    "podcasts": 205,
    "transcripts": 203,
    "books": 98,
    "people": 438,
    "events": 421,
    "wiki": 282,
    "courses": 12,
    "media": 1_253,
}
# The accepted projection audit found these exact supported metadata markers.  Runtime cleanup
# uses this canary to ensure the immutable-source allowlist does not silently broaden or drift.
# Articles are zero because their bodies are now projected by the article block builder, which
# removes the legacy `{:target="_blank"}` directive at build time instead of leaving 270 of them
# in the published text for the runtime to clean up.  Person bios still take the older plain-text
# path, so their ten markers are still removed here.
EXPECTED_LEAKED_TARGET_MARKERS = {"articles": 0, "people": 10}
EXPECTED_SELECTION = "preferred"
EDITORIAL_ROUTE_COLLECTIONS = {
    "articles": "/blog",
    "podcasts": "/podcast",
    "books": "/books",
    "people": "/people",
}
EXPECTED_EDITORIAL_FINALS = sum(EXPECTED_COUNTS[name] for name in EDITORIAL_ROUTE_COLLECTIONS)
EXPECTED_EDITORIAL_ALIASES = 2 * EXPECTED_EDITORIAL_FINALS
EXPECTED_REVISIONS = {
    "preferred_content": "e29f56ce70bd997171a78a9f0facc9354797f421",
    "fallback_selection": "373bef2912342ece1d2a2d2a9395aa3417243283",
    "legacy_main": "ee43d3fa0929faf691178d79f19528e6f15a83e5",
    "wiki": "988b79d0d655bf4755945c3118544cb9e0dbead6",
    "courses": "98a235283904b4ef9ad29e196298540756cf1bcc",
}
COLLECTION_NAMES = (
    "articles",
    "podcasts",
    "books",
    "people",
    "events",
    "wiki",
    "courses",
    "media",
)
EVENT_TYPE_ICONS = {
    "conference": "fas fa-briefcase",
    "podcast": "fas fa-microphone-alt",
    "webinar": "fas fa-tv",
    "workshop": "fas fa-wrench",
}
EXPECTED_RECORD_SOURCES = {
    "articles": {("DataTalksClub/content", EXPECTED_REVISIONS["preferred_content"])},
    "podcasts": {("DataTalksClub/content", EXPECTED_REVISIONS["preferred_content"])},
    "books": {("DataTalksClub/content", EXPECTED_REVISIONS["preferred_content"])},
    "people": {("DataTalksClub/datatalksclub.github.io", EXPECTED_REVISIONS["legacy_main"])},
    "events": {("DataTalksClub/datatalksclub.github.io", EXPECTED_REVISIONS["legacy_main"])},
    "wiki": {("DataTalksClub/podwiki", EXPECTED_REVISIONS["wiki"])},
    "courses": {("DataTalksClub/course-management-platform", EXPECTED_REVISIONS["courses"])},
    "media": {
        ("DataTalksClub/content", EXPECTED_REVISIONS["preferred_content"]),
        ("DataTalksClub/datatalksclub.github.io", EXPECTED_REVISIONS["legacy_main"]),
    },
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


@dataclass(frozen=True, slots=True)
class PodcastSeason:
    number: int
    episodes: tuple[dict[str, Any], ...]


def podcast_public_path(record: dict[str, Any]) -> str:
    """Return the checked canonical podcast detail path."""

    slug = record.get("slug")
    public_path = record.get("public_path")
    if (
        not isinstance(slug, str)
        or not slug
        or not isinstance(public_path, str)
        or public_path != f"/podcast/{slug}.html"
    ):
        raise ImproperlyConfigured("Public podcast canonical path is invalid.")
    return public_path


def _podcast_number(record: dict[str, Any], field: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ImproperlyConfigured(f"Public podcast {field} must be a positive integer.")
    return value


def ordered_podcasts(
    records: tuple[dict[str, Any], ...] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return the catalogue order without mutating the accepted projection order."""

    selected = list(public_projection()["podcasts"] if records is None else records)
    for record in selected:
        _podcast_number(record, "season")
        _podcast_number(record, "episode")
        if not isinstance(record.get("published"), str) or not isinstance(record.get("slug"), str):
            raise ImproperlyConfigured("Public podcast ordering metadata is invalid.")

    # Stable sorts make the mixed direction contract explicit: season and episode
    # descending, then published descending, then slug ascending.
    selected.sort(key=lambda record: record["slug"])
    selected.sort(key=lambda record: record["published"], reverse=True)
    selected.sort(key=lambda record: record["episode"], reverse=True)
    selected.sort(key=lambda record: record["season"], reverse=True)
    return tuple(selected)


def podcast_seasons(
    records: tuple[dict[str, Any], ...] | None = None,
) -> tuple[PodcastSeason, ...]:
    ordered = ordered_podcasts(records)
    if not ordered:
        raise ImproperlyConfigured("Public podcast catalogue must not be empty.")

    seasons: list[PodcastSeason] = []
    for episode in ordered:
        season_number = episode["season"]
        if not seasons or seasons[-1].number != season_number:
            seasons.append(PodcastSeason(number=season_number, episodes=(episode,)))
            continue
        previous = seasons[-1]
        seasons[-1] = PodcastSeason(
            number=previous.number,
            episodes=(*previous.episodes, episode),
        )
    return tuple(seasons)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured(f"Public projection cannot load {path.name}.") from exc


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ImproperlyConfigured(f"Public projection cannot read {path.name}.") from exc


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink() or path.name == "manifest.json":
            if path.is_symlink():
                raise ImproperlyConfigured("Public projection tree contains a symlink.")
            continue
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _editorial_route_manifest_digest(manifest: dict[str, Any]) -> str:
    return _canonical_json_sha256(
        {key: value for key, value in manifest.items() if key != "content_sha256"}
    )


@lru_cache(maxsize=1)
def _manifest_event_identity_snapshot() -> tuple[tuple[str, int, str], ...]:
    try:
        manifest = load_identity_manifest(EVENT_IDENTITY_MANIFEST)
    except EventIdentityError as exc:
        raise ImproperlyConfigured("Public Event identity manifest is invalid.") from exc
    return tuple((str(item.id), item.public_id, item.slug) for item in manifest.events)


def _runtime_event_identity_snapshot() -> tuple[tuple[str, int, str], ...]:
    """Return the database-owned event URL fields in one portable query.

    The tuple is also a safe cache key: when Studio imports or edits an event, the next request
    observes a different snapshot and rebuilds the adapted projection.
    """

    from events.models import Event

    try:
        rows = {
            str(event_id): (public_id, slug)
            for event_id, public_id, slug in Event.objects.order_by("id").values_list(
                "id", "public_id", "slug"
            )
        }
        resolved: list[tuple[str, int, str]] = []
        for event_id, expected_public_id, _manifest_slug in _manifest_event_identity_snapshot():
            runtime = rows.get(event_id)
            if runtime is None or runtime[0] != expected_public_id:
                raise ImproperlyConfigured("Public Event UUID/public-ID mapping is incomplete.")
            resolved.append((event_id, expected_public_id, runtime[1]))
        return tuple(resolved)
    except (AssertionError, DatabaseError, RuntimeError) as exc:
        if isinstance(exc, AssertionError) and "Database queries to" not in str(exc):
            raise
        if isinstance(exc, RuntimeError) and "Database access not allowed" not in str(exc):
            raise
        # A runtime without a usable database (query-forbidden tests, the DB-less liveness
        # container whose SQLite scratch directory is absent) serves the build-time manifest
        # identities instead of failing every public page.
        return _manifest_event_identity_snapshot()


def _apply_runtime_event_public_paths(
    projection: dict[str, Any],
    runtime_identities: tuple[tuple[str, int, str], ...] = (),
) -> None:
    """Replace UUID event paths with database-owned numeric public paths.

    The checked projection keeps the UUID path as source evidence, while the running site uses
    the stable numeric ``Event.public_id``.  UUIDs remain available in provenance and management
    APIs; this adapter only changes links exposed by public templates, feeds, and sitemaps.
    """

    replacements: dict[str, str] = {}
    # Resolve all identities in one portable query.  Calling ``canonical_detail_path`` for each
    # projected event turns a catalogue request into hundreds of database round trips (and made
    # sitemap/public-route checks unnecessarily slow).  The projection keeps the immutable UUID;
    # the database supplies only the public number and current title-derived slug.
    runtime_identity_map = {
        event_id: (public_id, slug) for event_id, public_id, slug in runtime_identities
    }
    events: list[dict[str, Any]] = []
    for raw_event in projection["events"]:
        event = dict(raw_event)
        identity_id = event.get("identity_id")
        if isinstance(identity_id, str):
            identity = runtime_identity_map.get(identity_id)
            if identity is None:
                raise ImproperlyConfigured("Public Event UUID/public-ID mapping is incomplete.")
            public_id, slug = identity
            public_path = f"/events/{public_id}/{slug}"
            replacements[event["public_path"]] = public_path
            event["public_path"] = public_path
            # Numeric public ID is already in the href.  Surface it so each row can
            # mint a unique kind-tip id without falling back to the date-group loop
            # counter or exposing the UUID.
            event["public_id"] = public_id
        events.append(event)
    projection["events"] = tuple(events)

    # Rebuild all event indexes after changing the paths so every public consumer observes the
    # same canonical URL.  The source-identity and UUID indexes intentionally remain keyed by
    # their private identities.
    projection["events_by_path"] = {event["public_path"]: event for event in events}
    projection["events_by_identity_id"] = {
        event["identity_id"]: event for event in events if event.get("identity_id")
    }
    projection["events_by_slug"] = {
        slug: projection["events_by_identity_id"].get(event.get("identity_id"), event)
        for slug, event in projection["events_by_slug"].items()
    }
    projection["events_by_source_identity"] = {
        (
            event["provenance"]["repository"],
            event["provenance"]["revision"],
            event["provenance"]["source_key"],
        ): event
        for event in events
    }

    for person in projection["people"]:
        relationships = person.get("relationships", ())
        person["relationships"] = tuple(
            {
                **relationship,
                "public_path": replacements.get(
                    relationship.get("public_path"), relationship.get("public_path", "")
                ),
            }
            for relationship in relationships
        )


def _expected_editorial_routes(
    projection: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    finals: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    for collection, prefix in EDITORIAL_ROUTE_COLLECTIONS.items():
        for record in projection[collection]:
            final_path = record["public_path"]
            clean_path = f"{prefix}/{record['slug']}"
            if final_path != f"{clean_path}.html":
                raise ImproperlyConfigured("Public projection editorial final mismatch.")
            finals.append(
                {
                    "collection": collection,
                    "record_key": record["slug"],
                    "final_path": final_path,
                    "source": dict(record["provenance"]),
                }
            )
            for source_path in (clean_path, f"{clean_path}/"):
                aliases.append(
                    {
                        "collection": collection,
                        "record_key": record["slug"],
                        "source_path": source_path,
                        "final_path": final_path,
                        "status_code": 301,
                        "query_policy": "preserve_raw",
                    }
                )
    finals.sort(key=lambda item: item["final_path"])
    aliases.sort(key=lambda item: item["source_path"])
    return finals, aliases


def _validate_editorial_route_manifest(
    route_manifest: Any,
    projection: dict[str, Any],
    artifact_digests: dict[str, str],
) -> None:
    if not isinstance(route_manifest, dict) or set(route_manifest) != {
        "schema_version",
        "schema",
        "provenance",
        "counts",
        "finals",
        "aliases",
        "content_sha256",
    }:
        raise ImproperlyConfigured("Public projection editorial route manifest shape mismatch.")
    expected_schema = {
        "path": "_docs/compatibility/editorial-route-migration.schema.json",
        "sha256": _sha256(EDITORIAL_ROUTE_MIGRATION_SCHEMA),
    }
    if route_manifest["schema_version"] != 1 or route_manifest["schema"] != expected_schema:
        raise ImproperlyConfigured("Public projection editorial route schema mismatch.")

    expected_source_artifacts = {
        f"{name}.json": artifact_digests[f"{name}.json"] for name in EDITORIAL_ROUTE_COLLECTIONS
    }
    source_revisions = sorted(
        {
            (record["provenance"]["repository"], record["provenance"]["revision"])
            for name in EDITORIAL_ROUTE_COLLECTIONS
            for record in projection[name]
        }
    )
    expected_provenance = {
        "builder": "scripts/build_public_projection.py",
        "projection_schema_version": 1,
        "projection_selection_mode": projection["manifest"]["selection_mode"],
        "source_artifacts": expected_source_artifacts,
        "source_revisions": [
            {"repository": repository, "revision": revision}
            for repository, revision in source_revisions
        ],
    }
    if route_manifest["provenance"] != expected_provenance:
        raise ImproperlyConfigured("Public projection editorial route provenance mismatch.")
    if route_manifest["counts"] != {
        "finals": EXPECTED_EDITORIAL_FINALS,
        "aliases": EXPECTED_EDITORIAL_ALIASES,
    }:
        raise ImproperlyConfigured("Public projection editorial route count mismatch.")

    finals = route_manifest["finals"]
    aliases = route_manifest["aliases"]
    if not isinstance(finals, list) or len(finals) != EXPECTED_EDITORIAL_FINALS:
        raise ImproperlyConfigured("Public projection editorial final count mismatch.")
    if not isinstance(aliases, list) or len(aliases) != EXPECTED_EDITORIAL_ALIASES:
        raise ImproperlyConfigured("Public projection editorial alias count mismatch.")
    try:
        final_paths = [item["final_path"] for item in finals]
        alias_paths = [item["source_path"] for item in aliases]
        alias_graph = {item["source_path"]: item["final_path"] for item in aliases}
    except (KeyError, TypeError) as exc:
        raise ImproperlyConfigured(
            "Public projection editorial route record shape mismatch."
        ) from exc
    if len(final_paths) != len(set(final_paths)) or len(alias_paths) != len(set(alias_paths)):
        raise ImproperlyConfigured("Public projection editorial route duplicate.")
    if set(final_paths) & set(alias_paths):
        raise ImproperlyConfigured("Public projection editorial route collision.")
    for source_path, target_path in alias_graph.items():
        visited = {source_path}
        cursor = target_path
        followed_alias = False
        while cursor in alias_graph:
            followed_alias = True
            if cursor in visited:
                raise ImproperlyConfigured("Public projection editorial redirect loop.")
            visited.add(cursor)
            cursor = alias_graph[cursor]
        if followed_alias:
            raise ImproperlyConfigured("Public projection editorial redirect chain.")
    final_path_set = set(final_paths)
    if any(target not in final_path_set for target in alias_graph.values()):
        raise ImproperlyConfigured("Public projection editorial redirect target mismatch.")

    expected_finals, expected_aliases = _expected_editorial_routes(projection)
    if finals != expected_finals or aliases != expected_aliases:
        raise ImproperlyConfigured("Public projection editorial route inventory is incomplete.")
    if route_manifest["content_sha256"] != _editorial_route_manifest_digest(route_manifest):
        raise ImproperlyConfigured("Public projection editorial route content digest mismatch.")


@lru_cache(maxsize=1)
def _checked_public_projection() -> dict[str, Any]:
    manifest = _read_json(PROJECTION_ROOT / "manifest.json")
    if manifest.get("schema_version") != 1 or manifest.get("selection_mode") != EXPECTED_SELECTION:
        raise ImproperlyConfigured("Unsupported public projection selection.")
    if manifest.get("counts") != EXPECTED_COUNTS:
        raise ImproperlyConfigured("Public projection count canaries do not match.")
    if manifest.get("tree_sha256") != _tree_sha256(PROJECTION_ROOT):
        raise ImproperlyConfigured("Public projection complete-tree digest mismatch.")
    sources = manifest.get("sources", {})
    if {key: value.get("revision") for key, value in sources.items()} != EXPECTED_REVISIONS:
        raise ImproperlyConfigured(
            "Public projection revisions do not match the accepted inventory."
        )
    if sources["preferred_content"].get("accepted") is not True:
        raise ImproperlyConfigured("Preferred projection source is not marked accepted.")
    if sources["fallback_selection"].get("accepted") is not False:
        raise ImproperlyConfigured("Fallback selection must remain explicitly unaccepted.")
    try:
        event_description_bridge = load_event_description_bridge()
        expected_bridge_binding = bridge_manifest_binding(event_description_bridge)
    except EventDescriptionBridgeError as exc:
        raise ImproperlyConfigured("Public event description bridge is invalid.") from exc
    projection_rules = manifest.get("projection_rules", {})
    if projection_rules.get("event_record_schema_version") != EVENT_RECORD_SCHEMA_VERSION:
        raise ImproperlyConfigured("Public event record schema version mismatch.")
    try:
        identity_manifest = load_identity_manifest(EVENT_IDENTITY_MANIFEST)
    except EventIdentityError as exc:
        raise ImproperlyConfigured("Public event identity manifest is invalid.") from exc
    # The checked projection retains its accepted UUID-era manifest digest as migration
    # evidence.  Runtime binds separately to the current schema-v2 numeric route manifest.
    current_identity_binding = {
        "path": "events/event_identity_manifest.json",
        "sha256": _sha256(EVENT_IDENTITY_MANIFEST),
        "schema_version": identity_manifest.schema_version,
        "counts": {
            "events": len(identity_manifest.events),
            "aliases": len(identity_manifest.aliases),
        },
    }
    if projection_rules.get("event_identity_manifest") not in (
        ACCEPTED_UUID_IDENTITY_BINDING,
        current_identity_binding,
    ):
        raise ImproperlyConfigured("Accepted UUID Event identity evidence binding mismatch.")
    identity_aliases_by_id = {
        str(item.id): tuple(alias for alias in item.aliases) for item in identity_manifest.events
    }
    identities_by_id = {str(item.id): item for item in identity_manifest.events}
    if projection_rules.get("event_description_bridge") != expected_bridge_binding:
        raise ImproperlyConfigured("Public event description bridge binding mismatch.")
    if (
        manifest.get("runtime_contract", {}).get("event_description_source")
        != "committed_safe_bridge_only"
    ):
        raise ImproperlyConfigured("Public event description runtime contract mismatch.")

    projection: dict[str, Any] = {"manifest": manifest}
    artifacts = manifest.get("artifacts", {})
    for name in COLLECTION_NAMES:
        filename = f"{name}.json"
        path = PROJECTION_ROOT / filename
        if _sha256(path) != artifacts.get(filename):
            raise ImproperlyConfigured(f"Public projection digest mismatch: {filename}.")
        records = _read_json(path)
        if not isinstance(records, list) or len(records) != EXPECTED_COUNTS[name]:
            raise ImproperlyConfigured(f"Public projection collection mismatch: {name}.")
        projection[name] = tuple(records)

    platform_path = PROJECTION_ROOT / PODCAST_PLATFORM_FILENAME
    if _sha256(platform_path) != artifacts.get(PODCAST_PLATFORM_FILENAME):
        raise ImproperlyConfigured("Public podcast platform artifact digest mismatch.")
    platforms = _read_json(platform_path)
    if (
        not isinstance(platforms, list)
        or tuple(item.get("provider") for item in platforms if isinstance(item, dict))
        != EXPECTED_PODCAST_PLATFORM_PROVIDERS
    ):
        raise ImproperlyConfigured("Public podcast platform inventory mismatch.")
    if any(
        not isinstance(item, dict)
        or set(item) != {"provider", "label", "url", "dot"}
        or not isinstance(item.get("label"), str)
        or not item["label"].strip()
        or not isinstance(item.get("url"), str)
        or not item["url"].startswith("https://")
        or not isinstance(item.get("dot"), str)
        for item in platforms
    ):
        raise ImproperlyConfigured("Public podcast platform record mismatch.")
    spotify_for_creators = platforms[-1]
    if (
        spotify_for_creators["label"] != "Spotify for Creators"
        or spotify_for_creators["url"] != "https://creators.spotify.com/pod/profile/datatalksclub/"
    ):
        raise ImproperlyConfigured("Spotify for Creators platform metadata mismatch.")
    projection["podcast_platforms"] = tuple(platforms)

    transcript_count = sum(bool(item.get("transcript")) for item in projection["podcasts"])
    if transcript_count != EXPECTED_COUNTS["transcripts"]:
        raise ImproperlyConfigured("Public projection transcript count mismatch.")
    ordered_podcasts(projection["podcasts"])
    try:
        for event in projection["events"]:
            validate_projected_event(event, event_description_bridge)
    except EventDescriptionBridgeError as exc:
        raise ImproperlyConfigured("Public event description record is invalid.") from exc
    described_event_count = sum(bool(event["description_html"]) for event in projection["events"])
    remaining_event_links = sum(len(event["links"]) for event in projection["events"])
    linked_event_count = sum(bool(event["links"]) for event in projection["events"])
    if described_event_count != EXPECTED_MATCH_COUNT:
        raise ImproperlyConfigured("Public event description coverage mismatch.")
    if remaining_event_links != EXPECTED_NON_LUMA_LINKS:
        raise ImproperlyConfigured("Public event link count mismatch.")
    if (
        linked_event_count != EXPECTED_EVENTS_WITH_LINKS
        or len(projection["events"]) - linked_event_count != EXPECTED_EVENTS_WITHOUT_LINKS
    ):
        raise ImproperlyConfigured("Public event link coverage mismatch.")

    for name in ("wiki_graph", "wiki_search"):
        filename = f"{name}.json"
        path = PROJECTION_ROOT / filename
        if _sha256(path) != artifacts.get(filename):
            raise ImproperlyConfigured(f"Public projection digest mismatch: {filename}.")
        projection[name] = _read_json(path)

    for name in COLLECTION_NAMES:
        records = projection[name]
        if any(
            (
                record.get("provenance", {}).get("repository"),
                record.get("provenance", {}).get("revision"),
            )
            not in EXPECTED_RECORD_SOURCES[name]
            for record in records
        ):
            raise ImproperlyConfigured(f"Public projection record provenance mismatch: {name}.")
        paths = [item["public_path"] for item in records]
        slugs = [item["slug"] for item in records]
        if len(paths) != len(set(paths)) or (name != "events" and len(slugs) != len(set(slugs))):
            raise ImproperlyConfigured(f"Public projection duplicate key: {name}.")
        by_slug = {
            item["slug"]: item
            for item in records
            if name != "events" or sum(other["slug"] == item["slug"] for other in records) == 1
        }
        if name == "events":
            for item in records:
                for alias in identity_aliases_by_id.get(item.get("identity_id"), ()):
                    if alias.kind != "legacy_date_path" or alias.source_path.endswith("/"):
                        continue
                    legacy_slug = alias.source_path.removeprefix("/events/")
                    if legacy_slug and legacy_slug not in by_slug:
                        by_slug[legacy_slug] = item
        projection[f"{name}_by_slug"] = by_slug
        projection[f"{name}_by_path"] = {item["public_path"]: item for item in records}
        if name == "events":
            identity_ids: set[str] = set()
            source_identities: set[tuple[str, str, str]] = set()
            by_source: dict[tuple[str, str, str], dict[str, Any]] = {}
            by_identity: dict[str, dict[str, Any]] = {}
            for event in records:
                identity_id = event.get("identity_id")
                if not isinstance(identity_id, str):
                    raise ImproperlyConfigured("Public event identity is missing.")
                try:
                    parsed_identity = uuid.UUID(identity_id)
                except ValueError as exc:
                    raise ImproperlyConfigured("Public event identity is invalid.") from exc
                if str(parsed_identity) != identity_id or parsed_identity.variant != uuid.RFC_4122:
                    raise ImproperlyConfigured("Public event identity is not lowercase RFC 4122.")
                if event.get("slug") != event_title_slug(event.get("title", "")):
                    raise ImproperlyConfigured("Public event slug is not title-derived.")
                identity = identities_by_id.get(identity_id)
                if identity is None:
                    raise ImproperlyConfigured("Public event route identity is unmapped.")
                accepted_uuid_path = f"/events/{identity_id}/{event['slug']}"
                if event.get("public_path") != accepted_uuid_path or accepted_uuid_path not in {
                    alias.source_path for alias in identity.aliases
                }:
                    raise ImproperlyConfigured("Accepted UUID Event route evidence mismatch.")
                provenance = event.get("provenance", {})
                source_identity = (
                    provenance.get("repository", ""),
                    provenance.get("revision", ""),
                    provenance.get("source_key", ""),
                )
                if identity_id in identity_ids or source_identity in source_identities:
                    raise ImproperlyConfigured("Public event identity collision.")
                identity_ids.add(identity_id)
                source_identities.add(source_identity)
                by_source[source_identity] = event
                by_identity[identity_id] = event
            projection["events_by_source_identity"] = by_source
            projection["events_by_identity_id"] = by_identity
    if any(
        podcast["transcript"]
        and (
            podcast.get("transcript_provenance", {}).get("repository") != "DataTalksClub/content"
            or podcast.get("transcript_provenance", {}).get("revision")
            != EXPECTED_REVISIONS["preferred_content"]
        )
        for podcast in projection["podcasts"]
    ):
        raise ImproperlyConfigured("Public projection transcript provenance mismatch.")
    route_path = PROJECTION_ROOT / EDITORIAL_ROUTE_MIGRATION_FILENAME
    if _sha256(route_path) != artifacts.get(EDITORIAL_ROUTE_MIGRATION_FILENAME):
        raise ImproperlyConfigured("Public projection editorial route artifact digest mismatch.")
    route_manifest = _read_json(route_path)
    _validate_editorial_route_manifest(route_manifest, projection, artifacts)
    projection["editorial_route_migration"] = route_manifest
    projection["editorial_route_aliases_by_path"] = {
        item["source_path"]: item for item in route_manifest["aliases"]
    }
    return projection


@lru_cache(maxsize=2)
def _adapted_public_projection(
    runtime_identities: tuple[tuple[str, int, str], ...],
) -> dict[str, Any]:
    """Build one immutable-by-convention runtime projection per event identity snapshot."""

    # Runtime URL adapters replace values in a handful of nested structures.  Copy only the
    # records they mutate rather than deep-copying the entire (large) content projection on every
    # request; the checked file-backed projection remains immutable and safely cached.
    source = _checked_public_projection()
    projection = dict(source)
    projection["events"] = tuple(dict(event) for event in source["events"])
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
    _apply_runtime_event_public_paths(projection, runtime_identities)
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

    The file-backed projection is cached, while the numeric event IDs come from the database.  A
    compact identity snapshot keeps repeated public requests fast and automatically invalidates
    the adapted projection after an event is imported or edited.
    """

    return _adapted_public_projection(_runtime_event_identity_snapshot())


def event_groups(now: datetime | None = None) -> EventGroups:
    current = now or timezone.now()
    if timezone.is_naive(current):
        current = timezone.make_aware(current)
    upcoming: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    for raw in public_projection()["events"]:
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
        "/slack",
        "/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026",
    }
    paths.update(
        record["public_path"]
        for name in ("articles", "podcasts", "books", "people", "events", "wiki")
        for record in projection[name]
    )
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
