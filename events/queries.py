"""Read public event records from the database.

The public event pages read one record shape no matter where the rows live.
Since #412 the rows are ``community_base.events.Event``: identity, schedule and
description are one row, speakers are ``Host`` links resolved to site profile
paths at read time, and the DTC display vocabulary (event type, podcast season
and episode) plus the ordered link list ride in the row's ``tags`` and
``materials`` JSON.  This module is the one place that turns a row into the
record shape the views and templates read.

Only publicly visible statuses are published: draft and cancelled rows never
appear, and a page that would have to invent a start time is impossible by
construction -- the shared row cannot exist without one.  An empty database
therefore lists no events, which is a normal state and not a failure.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from community_base.events.models import Event, EventHost
from django.db.models import Prefetch, Q

from content.models import EventSource

from .identity import EventIdentityNotFound, decode_event_tags, host_profile_url

#: The statuses a public page may show, matching the published/completed split
#: the former site ``Lifecycle`` carried.
PUBLIC_STATUSES = ("upcoming", "completed")


def _record(event: Event) -> dict[str, Any]:
    event_type, season, episode = decode_event_tags(event.tags)
    public_path = f"/events/{event.public_id}/{event.slug}" if event.public_id is not None else ""
    return {
        "identity_id": str(event.content_id),
        "public_id": event.public_id,
        "slug": event.slug,
        "title": event.title,
        "public_path": public_path,
        # A manifest-only row has no DTC type yet; the page treats "" as "no
        # type stated" rather than inventing one.
        "type": event_type or "",
        "starts_at": event.start_datetime.isoformat(),
        "ends_at": event.end_datetime.isoformat() if event.end_datetime is not None else "",
        "season": season,
        "episode": episode,
        "description_html": event.description_html,
        "description_text": event.description,
        "speakers": [
            {
                "key": link.host.external_ref,
                "name": link.host.name,
                "public_path": host_profile_url(link.host),
            }
            for link in event.event_host_links.all()
        ],
        "links": [
            {"label": entry.get("label", ""), "url": entry.get("url", "")}
            for entry in (event.materials or [])
            if isinstance(entry, dict)
        ],
        # Where this event came from.  Provenance belongs to the identity, and
        # the reviewed tuple the imports resolve by lives on the site-owned
        # ``content.EventSource`` row.
        "provenance": _provenance(event),
    }


def _provenance(event: Event) -> dict[str, Any]:
    try:
        source = event.source_identity
    except EventSource.DoesNotExist:
        source = None
    return {
        "repository": source.repository if source else (event.source_repo or ""),
        "revision": source.revision if source else (event.source_commit or ""),
        "source_key": source.source_key if source else "",
        "source_path": source.source_path if source else (event.source_path or ""),
        "checksum": source.source_checksum if source else "",
    }


def _published() -> Any:
    return (
        Event.objects.filter(status__in=PUBLIC_STATUSES)
        .select_related("source_identity")
        .prefetch_related(
            Prefetch(
                "event_host_links",
                queryset=EventHost.objects.select_related("host").order_by("position", "pk"),
            )
        )
    )


def published_event_records() -> tuple[dict[str, Any], ...]:
    """Every published event, newest first, as the record the pages read."""

    return tuple(_record(event) for event in _published().order_by("-start_datetime", "pk"))


def published_event_record(event_id: uuid.UUID | str) -> dict[str, Any] | None:
    """One published event's record, or ``None`` when it publishes none."""

    event = _published().filter(content_id=event_id).first()
    return None if event is None else _record(event)


def event_public_record(event: Event) -> dict[str, Any]:
    """Return the public record for an Event, read from its own row.

    An event row always carries its schedule, so unlike the split identity /
    content rows this replaces, a resolved identity always publishes.  A row
    whose status is not public raises, keeping the "404 rather than a guess"
    contract the former content-less identity had.
    """

    if event.status not in PUBLIC_STATUSES:
        raise EventIdentityNotFound("event_content_unavailable")
    return _record(event)


def published_event_records_by_path(paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The published records these public paths address, keyed by the path given.

    An event answers to two paths: the canonical ``/events/<public id>/<slug>``
    it carries, and ``/events/<identity uuid>/<slug>``, which is what the
    catalogue's own cross-references were written with.  A caller holding a
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
    for event in _published().filter(Q(public_id__in=public_ids) | Q(content_id__in=identity_ids)):
        record = _record(event)
        resolved[record["public_path"]] = record
        resolved[f"/events/{record['identity_id']}/{record['slug']}"] = record
    return resolved
