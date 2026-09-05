"""Editorial composition for the public event surfaces.

The events the site shows are database rows, read through
:mod:`events.queries`. What this module decides is how a reader sees them: which
are still to come and which have happened, what each start time says in the
site's own timezone, and which events share a displayed calendar date.

A database that publishes no events groups none, which is a normal state: the
hub renders its empty archive rather than failing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone

from events.queries import published_event_records

#: The site shows event times in one established timezone, so grouping happens
#: after conversion rather than by slicing the stored UTC string.
SITE_TIMEZONE = ZoneInfo("Europe/Berlin")

#: The icon each kind of event is drawn with. Presentation, not a record: the
#: database owns which kind an event is, this owns what that looks like, and an
#: unlisted kind still draws a calendar rather than nothing.
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

    ``key`` is deliberately a stable ISO date used only for accessible DOM
    identifiers.
    """

    key: str
    date: date
    display_date: str
    weekday: str
    events: tuple[dict[str, Any], ...]


def event_groups(now: datetime | None = None) -> EventGroups:
    """Split the published events into what is coming and what has happened."""

    current = now or timezone.now()
    if timezone.is_naive(current):
        current = timezone.make_aware(current)
    upcoming: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    for raw in published_event_records():
        event = {**raw, "starts_at_value": datetime.fromisoformat(raw["starts_at"])}
        local_start = event["starts_at_value"].astimezone(SITE_TIMEZONE)
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
        event_date_groups(upcoming),
        event_date_groups(recent, descending=True),
    )


def event_date_groups(
    events: list[dict[str, Any]], *, descending: bool = False
) -> tuple[EventDateGroup, ...]:
    """Group already-split events by the calendar date a reader sees."""

    grouped: dict[date, list[dict[str, Any]]] = {}
    for event in events:
        local_date = event["starts_at_value"].astimezone(SITE_TIMEZONE).date()
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
