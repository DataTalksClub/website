from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
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
from .event_speaker_bio_normalization import normalization_manifest_binding
from .event_speakers import event_speaker_records
from .podcast_routes import PODCAST_HIERARCHICAL_ONLY_SLUGS, podcast_canonical_path
from .public_text import strip_leaked_target_attributes, target_attribute_count

PROJECTION_ROOT = Path(__file__).with_name("public_projection")
REPOSITORY_ROOT = PROJECTION_ROOT.parents[1]
# Explicit prod-ingest helper location. Default runtime never reads here unless
# PUBLIC_PROJECTION_ROOT names it explicitly. See temporary/content/README.md.
MIGRATION_PROJECTION_ROOT = REPOSITORY_ROOT / "temporary" / "content" / "public_projection"
MIGRATION_IDENTITY_MANIFEST = REPOSITORY_ROOT / "temporary" / "content" / "event_identity_manifest.json"


def _resolve_projection_root() -> Path:
    """Return the projection root the default runtime reads.

    `content/public_projection/` holds no snapshot: the migration helper moved
    to `temporary/content/` and the release image excludes it, so a manifest
    here is absent and public catalogues render empty. `PUBLIC_PROJECTION_ROOT`
    lets explicit migration tooling point at the helper without changing the
    default.
    """

    override = os.getenv("PUBLIC_PROJECTION_ROOT", "").strip()
    return Path(override) if override else PROJECTION_ROOT


def _resolve_identity_manifest_path() -> Path:
    """Return the identity manifest the default runtime binds to.

    Absent by default (empty event catalogue): the helper at
    `temporary/content/event_identity_manifest.json` is used only when
    explicitly configured via `EVENT_IDENTITY_MANIFEST_PATH` (or when
    `PUBLIC_PROJECTION_ROOT` names the helper projection outright), or passed
    as `--identity-manifest` to `scripts/prod/import_events.py`.
    """

    override = os.getenv("EVENT_IDENTITY_MANIFEST_PATH", "").strip()
    if override:
        return Path(override)
    if os.getenv("PUBLIC_PROJECTION_ROOT", "").strip() == str(MIGRATION_PROJECTION_ROOT):
        return MIGRATION_IDENTITY_MANIFEST
    return PROJECTION_ROOT.parents[1] / "events" / "event_identity_manifest.json"


def is_projection_present(root: Path | None = None) -> bool:
    """Return True when a projection manifest exists at the resolved root."""

    candidate = root if root is not None else _resolve_projection_root()
    return (candidate / "manifest.json").is_file()
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
# How many articles, podcasts, transcripts, books, people, events, wiki pages, courses and
# media objects exist changes every time content is legitimately published or retired.  The
# manifest declares its own counts (see `_checked_public_projection`), and every artifact is
# checked against what the manifest itself declares plus a full-content digest, so nothing
# here pins an exact total that would need a hand-edit on every routine publish.
REQUIRED_COUNT_KEYS = frozenset(
    {
        "articles",
        "podcasts",
        "transcripts",
        "books",
        "people",
        "events",
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
# Media objects are served from an object store, so the complete-tree digest covers only
# the JSON artifacts and the wiki assets.  The manifest must declare that scope in
# machine-readable form: a manifest produced by an older whole-tree builder, or one that
# omits the declaration, fails closed instead of being silently accepted with a digest
# that would happen to match an unhydrated checkout.
MEDIA_TREE_PREFIX = "media/"
EXPECTED_TREE_DIGEST_SCOPE = (
    "projection artifacts and wiki assets; excludes manifest.json and media/"
)
# The media storage architecture is fixed; only how many objects are in it changes with
# content.  That "count" field is checked separately against the manifest's own declared
# media count, not pinned here.
EXPECTED_MEDIA_STORAGE_FIELDS = {
    "location": "object-store",
    "records": "media.json",
    "integrity": "per-record provenance.checksum",
}
EXPECTED_SELECTION = "preferred"
EDITORIAL_ROUTE_COLLECTIONS = {
    "articles": "/blog",
    "podcasts": "/podcast",
    "books": "/books",
    "people": "/people",
}
EXPECTED_REVISIONS = {
    "preferred_content": "1375c506dbce85c7c0e5e61f83c753128c5a48d1",
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


def safe_public_graph_url(value: Any) -> str:
    """Return a safe root-relative graph destination, or an empty destination.

    Empty URLs are valid for projected nodes that have no public page.  Every
    non-empty value must remain a path on this site. The graph's search nodes may
    carry one bounded ``q`` parameter and page links may carry a safe fragment;
    protocol-relative, absolute, credential-bearing, control-character and
    traversal values are rejected before a template can expose them as ``href``.
    """

    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        return ""
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return ""
    if "\\" in value or not value.startswith("/") or value.startswith("//"):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return ""
    if parsed.query:
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2)
        except ValueError:
            return ""
        if parsed.path != "/wiki/search" or set(query) != {"q"} or len(query["q"]) != 1:
            return ""
        if len(query["q"][0]) > 200 or any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in query["q"][0]
        ):
            return ""
    if parsed.fragment and re.fullmatch(r"[A-Za-z0-9._~%-]+", parsed.fragment) is None:
        return ""
    for index, character in enumerate(value):
        if character != "%":
            continue
        if (
            index + 2 >= len(value)
            or value[index + 1] not in "0123456789abcdefABCDEF"
            or value[index + 2] not in "0123456789abcdefABCDEF"
        ):
            return ""
    try:
        decoded = unquote(parsed.path, errors="strict")
        decoded_fragment = unquote(parsed.fragment, errors="strict")
    except UnicodeDecodeError:
        return ""
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in decoded) or any(
        segment == ".." for segment in decoded.split("/")
    ):
        return ""
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in decoded_fragment):
        return ""
    return value


def _validate_wiki_graph(graph: Any) -> None:
    """Validate graph references before the checked projection reaches a view."""

    if not isinstance(graph, dict):
        raise ImproperlyConfigured("Public wiki graph projection is invalid.")
    raw_nodes = graph.get("nodes")
    raw_links = graph.get("links")
    if not isinstance(raw_nodes, list) or not isinstance(raw_links, list):
        raise ImproperlyConfigured("Public wiki graph collections are invalid.")
    node_ids: set[str] = set()
    for node in raw_nodes:
        if not isinstance(node, dict):
            raise ImproperlyConfigured("Public wiki graph contains a malformed node.")
        node_id = node.get("id")
        if (
            not isinstance(node_id, str)
            or not node_id
            or node_id in node_ids
            or not isinstance(node.get("label"), str)
            or not node["label"]
            or not isinstance(node.get("title"), str)
            or not node["title"]
            or not isinstance(node.get("type"), str)
            or not node["type"]
            or not isinstance(node.get("url", ""), str)
            or safe_public_graph_url(node.get("url", "")) != node.get("url", "")
        ):
            raise ImproperlyConfigured("Public wiki graph node contract is invalid.")
        node_ids.add(node_id)
    for link in raw_links:
        if not isinstance(link, dict):
            raise ImproperlyConfigured("Public wiki graph contains a malformed link.")
        source = link.get("source")
        target = link.get("target")
        if (
            not isinstance(source, str)
            or source not in node_ids
            or not isinstance(target, str)
            or target not in node_ids
            or not isinstance(link.get("kind"), str)
            or not link["kind"]
            or isinstance(link.get("weight"), bool)
            or not isinstance(link.get("weight"), int)
            or link["weight"] < 1
        ):
            raise ImproperlyConfigured("Public wiki graph link contract is invalid.")


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
        or public_path != podcast_canonical_path(slug)
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
    """Digest the projection artifacts and wiki assets, excluding the media objects.

    Media objects live in an object store and are verified per record against
    ``provenance.checksum``, so they are outside the complete-tree digest.  The symlink
    rejection deliberately still covers ``media/``: a symlink anywhere below the root is
    a hard failure whether or not its bytes contribute to the digest.
    """

    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ImproperlyConfigured("Public projection tree contains a symlink.")
        relative_path = path.relative_to(root).as_posix()
        if path.name == "manifest.json" or relative_path.startswith(MEDIA_TREE_PREFIX):
            continue
        relative = relative_path.encode()
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
    # Absent manifest is a valid empty state (default runtime after the snapshot
    # moved to temporary/content/).  Only an explicit prod-ingest run names the
    # helper manifest; a present-but-invalid file still fails closed.
    manifest_path = _resolve_identity_manifest_path()
    if not manifest_path.is_file():
        return ()
    try:
        manifest = load_identity_manifest(manifest_path)
    except EventIdentityError as exc:
        raise ImproperlyConfigured("Public Event identity manifest is invalid.") from exc
    return tuple((str(item.id), item.public_id, item.slug) for item in manifest.events)


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
    projection["events_by_source_identity"] = {}
    projection["events_by_identity_id"] = {}
    projection["podcast_platforms"] = ()
    projection["wiki_graph"] = {"nodes": [], "links": [], "counts": {"nodes": 0, "links": 0}}
    projection["wiki_search"] = {"docs": []}
    projection["editorial_route_migration"] = {"finals": [], "aliases": []}
    projection["editorial_route_aliases_by_path"] = {}
    return projection


def _runtime_event_identity_snapshot() -> tuple[tuple[str, int, str], ...]:
    """Return the database-owned event URL fields in one portable query.

    The tuple is also a safe cache key: when Studio imports or edits an event, the next request
    observes a different snapshot and rebuilds the adapted projection.  The empty-table fallback
    below returns the manifest tuple, which differs from every populated snapshot, so the first
    request after an import still rebuilds.
    """

    from events.models import Event

    try:
        rows = {
            str(event_id): (public_id, slug)
            for event_id, public_id, slug in Event.objects.order_by("id").values_list(
                "id", "public_id", "slug"
            )
        }
        if not rows:
            # A usable but completely unpopulated Event table is the pre-import bootstrap, not
            # corruption: `prepare_deployment` imports the reviewed manifest after `migrate`, and
            # a freshly migrated database is simply not there yet.  Serve the build-time manifest
            # identities, exactly as the DB-less fallback below does, instead of 500ing every
            # public page.  This deliberately reads the whole table, not just the manifest UUIDs,
            # so "empty" means empty.
            return _manifest_event_identity_snapshot()
        resolved: list[tuple[str, int, str]] = []
        for event_id, expected_public_id, _manifest_slug in _manifest_event_identity_snapshot():
            runtime = rows.get(event_id)
            if runtime is None or runtime[0] != expected_public_id:
                # Any non-empty table must agree with the manifest, entry for entry.  The spec
                # makes identity import atomic - "Imports and replays preserve the checked
                # UUID/public-ID pair and reject missing, duplicate, ambiguous, or renumbered
                # mappings atomically" - so a table holding some manifest events but not others,
                # or holding one under a different public ID, is a state no supported import can
                # produce.  It is drift, and publication "fails closed without a public ID"
                # rather than serving manifest numbers the database would not resolve.
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
            expected_path = (
                podcast_canonical_path(record["slug"])
                if collection == "podcasts"
                else f"{clean_path}.html"
            )
            if final_path != expected_path:
                raise ImproperlyConfigured("Public projection editorial final mismatch.")
            finals.append(
                {
                    "collection": collection,
                    "record_key": record["slug"],
                    "final_path": final_path,
                    "source": dict(record["provenance"]),
                }
            )
            if collection == "podcasts" and record["slug"] in PODCAST_HIERARCHICAL_ONLY_SLUGS:
                continue
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


def _editorial_route_counts(projection: dict[str, Any]) -> tuple[int, int]:
    """Return the (finals, aliases) counts the loaded projection implies.

    Derived from the collections actually present, so publishing or retiring
    content never needs a matching hand-edit here.
    """

    finals_count = sum(len(projection[name]) for name in EDITORIAL_ROUTE_COLLECTIONS)
    hierarchical_only = sum(
        1
        for record in projection.get("podcasts", ())
        if record["slug"] in PODCAST_HIERARCHICAL_ONLY_SLUGS
    )
    aliases_count = 2 * (finals_count - hierarchical_only)
    return finals_count, aliases_count


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
    expected_finals_count, expected_aliases_count = _editorial_route_counts(projection)
    if route_manifest["counts"] != {
        "finals": expected_finals_count,
        "aliases": expected_aliases_count,
    }:
        raise ImproperlyConfigured("Public projection editorial route count mismatch.")

    finals = route_manifest["finals"]
    aliases = route_manifest["aliases"]
    if not isinstance(finals, list) or len(finals) != expected_finals_count:
        raise ImproperlyConfigured("Public projection editorial final count mismatch.")
    if not isinstance(aliases, list) or len(aliases) != expected_aliases_count:
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
    root = _resolve_projection_root()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImproperlyConfigured(
            "Public projection is absent (no manifest.json; default catalogues are empty)."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured("Public projection cannot load manifest.json.") from exc
    if manifest.get("schema_version") != 1 or manifest.get("selection_mode") != EXPECTED_SELECTION:
        raise ImproperlyConfigured("Unsupported public projection selection.")
    # The manifest declares its own counts for this build; they are not pinned to a fixed
    # inventory because publishing or retiring content legitimately moves every one of
    # them.  What must hold, regardless of size, is that the declaration is well-formed
    # and that every artifact and derived count actually matches what is declared -
    # checked below, collection by collection, as each artifact is loaded.
    counts = manifest.get("counts")
    if (
        not isinstance(counts, dict)
        or set(counts) != REQUIRED_COUNT_KEYS
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts.values()
        )
    ):
        raise ImproperlyConfigured("Public projection count declarations are malformed.")
    if manifest.get("tree_digest_scope") != EXPECTED_TREE_DIGEST_SCOPE:
        raise ImproperlyConfigured("Public projection tree digest scope is not declared.")
    media_storage = manifest.get("media_storage")
    if (
        not isinstance(media_storage, dict)
        or set(media_storage) != {"location", "records", "count", "integrity"}
        or {key: media_storage[key] for key in EXPECTED_MEDIA_STORAGE_FIELDS}
        != EXPECTED_MEDIA_STORAGE_FIELDS
        or media_storage["count"] != counts["media"]
    ):
        raise ImproperlyConfigured("Public projection media storage declaration mismatch.")
    if manifest.get("tree_sha256") != _tree_sha256(root):
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
        identity_manifest = load_identity_manifest(_resolve_identity_manifest_path())
    except EventIdentityError as exc:
        raise ImproperlyConfigured("Public event identity manifest is invalid.") from exc
    # The checked projection retains its accepted UUID-era manifest digest as migration
    # evidence.  Runtime binds separately to the current schema-v2 numeric route manifest.
    current_identity_binding = {
        "path": "events/event_identity_manifest.json",
        "sha256": _sha256(_resolve_identity_manifest_path()),
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
    try:
        expected_event_speaker_bio_binding = normalization_manifest_binding()
    except Exception as exc:
        raise ImproperlyConfigured(
            "Public event speaker-bio normalization plan is invalid."
        ) from exc
    if (
        projection_rules.get("event_speaker_bio_normalization")
        != expected_event_speaker_bio_binding
    ):
        raise ImproperlyConfigured("Public event speaker-bio normalization binding mismatch.")
    if (
        manifest.get("runtime_contract", {}).get("event_description_source")
        != "committed_safe_bridge_only"
    ):
        raise ImproperlyConfigured("Public event description runtime contract mismatch.")

    projection: dict[str, Any] = {"manifest": manifest}
    artifacts = manifest.get("artifacts", {})
    for name in COLLECTION_NAMES:
        filename = f"{name}.json"
        path = root / filename
        if _sha256(path) != artifacts.get(filename):
            raise ImproperlyConfigured(f"Public projection digest mismatch: {filename}.")
        records = _read_json(path)
        if not isinstance(records, list) or not records:
            raise ImproperlyConfigured(f"Public projection collection mismatch: {name}.")
        if len(records) != counts[name]:
            raise ImproperlyConfigured(f"Public projection declared count mismatch: {name}.")
        projection[name] = tuple(records)

    # Every media reference a content record carries must resolve to a real media
    # object.  This is the referential check that a fixed media-object total only ever
    # gestured at: it catches a broken or stale reference regardless of how many media
    # objects the projection happens to contain today.
    media_paths = {item["public_path"] for item in projection["media"]}
    for name in ("articles", "podcasts", "books", "people"):
        for record in projection[name]:
            image_path = record.get("image_path")
            if image_path and image_path not in media_paths:
                raise ImproperlyConfigured(
                    f"Public projection media reference does not resolve: {name}."
                )
            for block in record.get("blocks", ()) or ():
                if isinstance(block, dict) and block.get("kind") == "image":
                    if block.get("src") not in media_paths:
                        raise ImproperlyConfigured(
                            f"Public projection media reference does not resolve: {name}."
                        )

    platform_path = root / PODCAST_PLATFORM_FILENAME
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
        or set(item) != {"key", "provider", "label", "title", "url", "dot"}
        or item.get("key") != item.get("provider")
        or not isinstance(item.get("label"), str)
        or not item["label"].strip()
        or item.get("title") != item.get("label")
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
    if transcript_count != counts["transcripts"]:
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
        path = root / filename
        if _sha256(path) != artifacts.get(filename):
            raise ImproperlyConfigured(f"Public projection digest mismatch: {filename}.")
        projection[name] = _read_json(path)
    _validate_wiki_graph(projection["wiki_graph"])

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
    route_path = root / EDITORIAL_ROUTE_MIGRATION_FILENAME
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
    people_by_slug = {person["slug"]: person for person in projection["people"]}
    people_by_path = {person["public_path"]: person for person in projection["people"]}
    projection["events"] = tuple(
        {
            **event,
            "speakers": event_speaker_records(
                event.get("speakers", ()),
                people_by_slug=people_by_slug,
                people_by_path=people_by_path,
            ),
        }
        for event in projection["events"]
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

    When no projection manifest is present (the default after the snapshot moved to
    temporary/content/), returns an empty catalogue instead of raising: hubs render
    empty, detail lookups miss (404), sitemaps list only static paths.  The helper
    snapshot is read only when PUBLIC_PROJECTION_ROOT names it explicitly.
    """

    try:
        return _adapted_public_projection(_runtime_event_identity_snapshot())
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
