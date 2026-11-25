"""The sponsors of an event, linked to the sponsor directory (``core.Sponsor``).

An event's sponsors are ``event_registrants.EventSponsor`` rows.  This module is
the one place that reads them into the shape the public pages, the admin API
and the picture renderer use, and the one place that replaces them.
"""

from __future__ import annotations

from typing import Any

from community_base.events.models import Event
from django.db.models import Prefetch
from django.utils.text import slugify

from core.models import Sponsor, SponsorPlacementAssignment
from core.sponsors import SPONSOR_PLACEMENT_PUBLIC_DIRECTORY
from event_registrants.models import EventSponsor


class UnknownEventSponsor(ValueError):
    """A sponsor key that names no row in the sponsor directory."""


def sponsor_links_prefetch() -> Prefetch:
    """Prefetch an event's sponsor links with what :func:`sponsor_entries` reads."""

    return Prefetch(
        "event_sponsors",
        queryset=EventSponsor.objects.select_related("sponsor")
        .prefetch_related(
            Prefetch(
                "sponsor__assignments",
                queryset=SponsorPlacementAssignment.objects.filter(
                    placement_key=SPONSOR_PLACEMENT_PUBLIC_DIRECTORY, enabled=True
                ),
            )
        )
        .order_by("position", "id"),
    )


def _page_url(sponsor: Sponsor) -> str:
    """The sponsor's entry on ``/sponsors`` when it is listed there, else its own site."""

    listed = sponsor.lifecycle == Sponsor.Lifecycle.ACTIVE and any(
        item.placement_key == SPONSOR_PLACEMENT_PUBLIC_DIRECTORY and item.enabled
        for item in sponsor.assignments.all()
    )
    if listed:
        return f"/sponsors#{slugify(sponsor.name)}"
    return sponsor.url


def sponsor_entry(sponsor: Sponsor) -> dict[str, Any]:
    page_url = _page_url(sponsor)
    return {
        "key": sponsor.key,
        "name": sponsor.name,
        "logo_url": sponsor.logo_url,
        "url": page_url,
        # An outside link carries rel="sponsored"; the /sponsors entry does not.
        "external": bool(page_url) and not page_url.startswith("/"),
    }


def sponsor_entries(event: Event) -> list[dict[str, Any]]:
    """The event's sponsors in order: key, name, logo URL and where to link."""

    return [sponsor_entry(link.sponsor) for link in event.event_sponsors.all()]


def set_event_sponsors(event: Event, keys: list[str]) -> None:
    """Replace the event's sponsors with the directory entries ``keys`` name, in order."""

    wanted: list[str] = []
    for raw in keys:
        key = str(raw).strip()
        if key and key not in wanted:
            wanted.append(key)
    found = {sponsor.key: sponsor for sponsor in Sponsor.objects.filter(key__in=wanted)}
    missing = [key for key in wanted if key not in found]
    if missing:
        raise UnknownEventSponsor(
            f"Unknown sponsor key(s): {', '.join(missing)}. "
            "Add the sponsor to the sponsor directory first."
        )
    EventSponsor.objects.filter(event=event).delete()
    EventSponsor.objects.bulk_create(
        EventSponsor(event=event, sponsor=found[key], position=index)
        for index, key in enumerate(wanted)
    )
