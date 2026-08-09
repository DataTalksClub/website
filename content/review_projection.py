from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

PROJECTION_PATH = Path(__file__).with_name("review_projection.json")
REQUIRED_SOURCE_REVISIONS = {
    "main": "ee43d3fa0929faf691178d79f19528e6f15a83e5",
    "courses": "98a235283904b4ef9ad29e196298540756cf1bcc",
    "docs": "3f23e006ffdaa498bbc69697408853b6f5eb37dc",
    "faq": "c8da1deea9e24945922702994de101dd90a5380a",
    "podwiki": "988b79d0d655bf4755945c3118544cb9e0dbead6",
}
REQUIRED_PUBLIC_PATHS = {
    "/blog/ai-dev-tools-zoomcamp",
    "/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production",
    "/people/aleksandrkim",
    "/books/20250922-how-software-fails",
    "/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
    "/faq/ai-dev-tools-zoomcamp.html",
    "/wiki/ai-coding-tools",
    "/slack.html",
    "/courses/ai-dev-tools-zoomcamp",
}


@dataclass(frozen=True, slots=True)
class EventGroups:
    upcoming: tuple[dict[str, Any], ...]
    recent: tuple[dict[str, Any], ...]


def _public_paths(projection: dict[str, Any]) -> set[str]:
    return {
        record["public_path"]
        for key in (
            "article",
            "podcast",
            "book",
            "slack",
            "docs",
            "faq",
            "podwiki",
            "course",
        )
        for record in (projection[key],)
    } | {person["public_path"] for person in projection["people"].values()}


def _validate_projection(projection: dict[str, Any]) -> None:
    if projection.get("schema_version") != 1:
        raise ImproperlyConfigured("Unsupported review content projection schema.")
    actual_revisions = {
        key: value.get("revision") for key, value in projection.get("sources", {}).items()
    }
    if actual_revisions != REQUIRED_SOURCE_REVISIONS:
        raise ImproperlyConfigured("Review content projection revisions do not match inventory.")
    missing_paths = REQUIRED_PUBLIC_PATHS - _public_paths(projection)
    if missing_paths:
        raise ImproperlyConfigured(
            f"Review content projection is missing required paths: {sorted(missing_paths)!r}"
        )
    if projection["podcast"]["guest"] not in projection["people"]:
        raise ImproperlyConfigured("Podcast guest does not resolve to one projected Person.")
    if not any(event.get("speaker") == "aleksandrkim" for event in projection["events"]):
        raise ImproperlyConfigured("Aleksandr Kim event relationship is missing.")
    if not all(event.get("speaker_name") for event in projection["events"]):
        raise ImproperlyConfigured("Every projected event must resolve its speaker name.")
    platform_provenance = projection["course"].get("platform_provenance", {})
    if platform_provenance.get("source") != "courses" or not platform_provenance.get("source_path"):
        raise ImproperlyConfigured("Course projection must identify its adopted CMP model.")


@lru_cache(maxsize=1)
def review_projection() -> dict[str, Any]:
    try:
        projection = json.loads(PROJECTION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured("Review content projection cannot be loaded.") from exc
    _validate_projection(projection)
    return projection


def record_provenance(record: dict[str, Any]) -> dict[str, str]:
    projection = review_projection()
    source_key = record["source"]
    source = projection["sources"][source_key]
    return {
        "repository": source["repository"],
        "revision": source["revision"],
        "source_path": record["source_path"],
    }


def projected_events() -> tuple[dict[str, Any], ...]:
    events = []
    for raw_event in review_projection()["events"]:
        event = dict(raw_event)
        event["starts_at"] = datetime.fromisoformat(event["time"])
        local_start = event["starts_at"].astimezone(ZoneInfo("Europe/Berlin"))
        event["display_time"] = f"{local_start:%b} {local_start.day}, {local_start:%Y, %H:%M %Z}"
        event["public_path"] = f"/events/{event['id']}"
        event["provenance"] = record_provenance(event)
        events.append(event)
    events.sort(key=lambda event: event["starts_at"])
    return tuple(events)


def event_groups(now: datetime | None = None) -> EventGroups:
    current = now or timezone.now()
    if timezone.is_naive(current):
        current = timezone.make_aware(current)
    events = projected_events()
    return EventGroups(
        upcoming=tuple(event for event in events if event["starts_at"] >= current),
        recent=tuple(reversed([event for event in events if event["starts_at"] < current])),
    )


def projection_context(key: str) -> dict[str, Any]:
    record = review_projection()[key]
    return {key: record, "provenance": record_provenance(record)}
