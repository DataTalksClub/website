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
from collections.abc import Iterable
from typing import Any

from django.db.models import Prefetch, Q

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
        # Where this event came from, carried from the identity row rather than
        # from the content row: provenance belongs to the identity, which is
        # frozen at import, and consumers match on it exactly.
        "provenance": {
            "repository": event.source_repository,
            "revision": event.source_revision,
            "source_key": event.source_key,
            "source_path": event.source_path,
            "checksum": event.source_checksum,
        },
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


def published_event_records_by_path(paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The published records these public paths address, keyed by the path given.

    An event answers to two paths: the canonical ``/events/<public id>/<slug>``
    it carries, and ``/events/<identity uuid>/<slug>``, which is what the
    catalogue's own cross-references were written with. A caller holding a
    mixture of both should not have to know which it has, so both forms are
    resolved here, and a path this database publishes nothing for is simply
    absent from the result.
    """

    public_ids: set[int] = set()
    identity_ids: set[uuid.UUID] = set()
    for path in paths:
        parts = path.split("/")
        if len(parts) < 3 or parts[1] != "events":
            continue
        token = parts[2]
        if token.isdigit():
            public_ids.add(int(token))
            continue
        try:
            identity_ids.add(uuid.UUID(token))
        except ValueError:
            continue
    if not public_ids and not identity_ids:
        return {}

    resolved: dict[str, dict[str, Any]] = {}
    for content in _published().filter(
        Q(event__public_id__in=public_ids) | Q(event_id__in=identity_ids)
    ):
        record = _record(content)
        resolved[record["public_path"]] = record
        resolved[f"/events/{record['identity_id']}/{record['slug']}"] = record
    return resolved
