"""Editorial composition for the public event surfaces.

The events the site shows are database rows, read through
:mod:`events.queries`. What this module decides is how a reader sees them: which
are still to come and which have happened, what each start time says in the
site's own timezone, and which events share a displayed calendar date.

A database that publishes no events groups none, which is a normal state: the
hub renders its empty archive rather than failing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone

from events.lifecycle import has_started
from events.queries import published_event_records, published_event_records_by_path

from .event_banners import event_banner_url

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
    #: "Nov 30": the timeline's day heading.
    short_date: str = ""
    #: "Today", "Tomorrow" or "Yesterday" when the date is adjacent to the
    #: site-timezone today, otherwise "".
    relative_label: str = ""
    #: The year, only when it is not the current one (the archive spans years).
    year: str = ""


def event_groups(now: datetime | None = None) -> EventGroups:
    """Split the published events into what is coming and what has happened.

    With no explicit ``now`` the grouping is memoized for the request: the
    homepage, the hubs, the detail pages and the member home all group the
    same published set, and regrouping it per caller only re-spent the same
    read.  The frozen clock also keeps one request's surfaces consistent with
    each other.  An explicit ``now`` (tests pinning the boundary) always
    groups live.
    """

    if now is None:
        from core.request_cache import memoized

        return memoized("content.event_content.event_groups", lambda: _grouped(None))
    return _grouped(now)


def _grouped(now: datetime | None) -> EventGroups:

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
        event["display_weekday"] = f"{local_start:%a}"
        event["display_clock"] = f"{local_start:%H:%M %Z}"
        # The detail page's calendar tile and its long dateline beside it.
        event["display_month"] = f"{local_start:%b}"
        event["display_day"] = str(local_start.day)
        event["display_date_long"] = f"{local_start:%A, %B} {local_start.day}, {local_start:%Y}"
        # The event picture's own date label ("Mon 30 Nov 2026"), for the art
        # drawn in HTML by ``public/_event_art.html``.
        event["display_art_date"] = f"{local_start:%a} {local_start.day} {local_start:%b %Y}"
        event["display_clock_range"] = event["display_clock"]
        if raw.get("ends_at"):
            local_end = datetime.fromisoformat(raw["ends_at"]).astimezone(SITE_TIMEZONE)
            event["display_clock_range"] = f"{local_start:%H:%M} – {local_end:%H:%M %Z}"
        event["banner_url"] = event_banner_url(event)
        # A YouTube link on an event is its recording or live stream; the list
        # says "Recording on YouTube" only for an event that has happened.
        event["has_youtube_link"] = any("youtu" in link["url"] for link in event["links"])
        # The picture's palette: the first word of the type when it is one of
        # the four drawn kinds, else the webinar palette (as the picture does).
        kind = event["type"].split("·")[0].strip().casefold()
        event["art_kind"] = kind if kind in EVENT_TYPE_ICONS else "webinar"
        event["type_icon"] = EVENT_TYPE_ICONS.get(
            event["type"].casefold(),
            "fas fa-calendar-check",
        )
        # Upcoming until the start has passed, then listed with the recent events: the
        # shared ``events.lifecycle`` boundary, never the stored status.
        event["is_past"] = has_started(event["starts_at_value"], current)
        (recent if event["is_past"] else upcoming).append(event)

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
        event_date_groups(upcoming, now=current),
        event_date_groups(recent, descending=True, now=current),
    )


def event_timeline_groups(paths: Iterable[str]) -> tuple[EventDateGroup, ...]:
    """The published events these public paths address, as timeline day groups.

    For a list of chosen events drawn through ``public/_event_timeline.html``
    (a person's events, a topic's events).  Either path form an event answers
    to is accepted (see ``published_event_records_by_path``); a path this
    database publishes nothing for is skipped.  The events are the same
    decorated records /events draws, newest day first, upcoming and past mixed.
    """

    wanted = {record["public_path"] for record in published_event_records_by_path(paths).values()}
    if not wanted:
        return ()
    groups = event_groups()
    chosen = [
        event for event in (*groups.upcoming, *groups.recent) if event["public_path"] in wanted
    ]
    chosen.sort(key=lambda event: event["starts_at_value"], reverse=True)
    return event_date_groups(chosen, descending=True)


_RELATIVE_DAYS = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}


def event_date_groups(
    events: list[dict[str, Any]],
    *,
    descending: bool = False,
    now: datetime | None = None,
) -> tuple[EventDateGroup, ...]:
    """Group already-split events by the calendar date a reader sees."""

    today = (now or timezone.now()).astimezone(SITE_TIMEZONE).date()
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
            short_date=f"{local_date:%b} {local_date.day}",
            relative_label=_RELATIVE_DAYS.get((local_date - today).days, ""),
            year="" if local_date.year == today.year else str(local_date.year),
        )
        for local_date, items in sorted(grouped.items(), reverse=descending)
    )
