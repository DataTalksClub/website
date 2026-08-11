from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

PROJECTION_ROOT = Path(__file__).with_name("public_projection")
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


@dataclass(frozen=True, slots=True)
class PodcastSeason:
    number: int
    episodes: tuple[dict[str, Any], ...]


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
    seasons: list[PodcastSeason] = []
    for episode in ordered_podcasts(records):
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
def public_projection() -> dict[str, Any]:
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

    transcript_count = sum(bool(item.get("transcript")) for item in projection["podcasts"])
    if transcript_count != EXPECTED_COUNTS["transcripts"]:
        raise ImproperlyConfigured("Public projection transcript count mismatch.")
    ordered_podcasts(projection["podcasts"])

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
        if len(paths) != len(set(paths)) or len(slugs) != len(set(slugs)):
            raise ImproperlyConfigured(f"Public projection duplicate key: {name}.")
        projection[f"{name}_by_slug"] = {item["slug"]: item for item in records}
        projection[f"{name}_by_path"] = {item["public_path"]: item for item in records}
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
    upcoming.sort(key=lambda item: (item["starts_at_value"], item["slug"]))
    recent.sort(key=lambda item: (item["starts_at_value"], item["slug"]), reverse=True)
    return EventGroups(tuple(upcoming), tuple(recent))


def public_paths() -> tuple[str, ...]:
    projection = public_projection()
    paths = {
        "/",
        "/blog",
        "/podcast",
        "/books",
        "/events",
        "/courses",
        "/wiki",
        "/docs/",
        "/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
        "/faq/",
        "/faq/ai-dev-tools-zoomcamp.html",
        "/slack.html",
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
