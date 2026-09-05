"""Read public event records from the database.

The public event pages used to read a built projection file keyed by source
provenance. Everything those pages show now lives in ``Event`` (identity) and
``EventContent`` with its speakers and links (what the page says), so this
module is the one place that turns those rows into the record shape the views
and templates read.

An event with no content row yet is not published: identity is imported first
and content follows, and a page that would have to invent a start time is a 404
rather than a guess. An empty database therefore lists no events, which is a
normal state and not a failure.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import Prefetch

from .models import Event, EventContent, EventLink, EventSpeaker


def _record(content: EventContent) -> dict[str, Any]:
    event = content.event
    public_path = f"/events/{event.public_id}/{event.slug}" if event.public_id is not None else ""
    return {
        "identity_id": str(event.id),
        "public_id": event.public_id,
        "slug": event.slug,
        "title": event.title,
        "public_path": public_path,
        "type": content.type,
        "starts_at": content.starts_at.isoformat(),
        "ends_at": content.ends_at.isoformat() if content.ends_at is not None else "",
        "season": content.season,
        "episode": content.episode,
        "description_html": content.description_html,
        "description_text": content.description_text,
        "speakers": [
            {"key": speaker.key, "name": speaker.name, "public_path": speaker.public_path}
            for speaker in content.speakers.all()
        ],
        "links": [{"label": link.label, "url": link.url} for link in content.links.all()],
    }


def _published() -> Any:
    return (
        EventContent.objects.select_related("event")
        .filter(event__lifecycle__in=(Event.Lifecycle.PUBLISHED, Event.Lifecycle.COMPLETED))
        .prefetch_related(
            Prefetch("speakers", queryset=EventSpeaker.objects.order_by("position")),
            Prefetch("links", queryset=EventLink.objects.order_by("position")),
        )
    )


def published_event_records() -> tuple[dict[str, Any], ...]:
    """Every published event, newest first, as the record the pages read."""

    return tuple(_record(content) for content in _published().order_by("-starts_at", "event_id"))


def published_event_record(event_id: uuid.UUID | str) -> dict[str, Any] | None:
    """One published event's record, or ``None`` when it publishes none."""

    content = _published().filter(event_id=event_id).first()
    return None if content is None else _record(content)
