"""What a published event must carry: a type and at least one speaker.

Every row on the public events pages draws the event's type pill and its
speakers.  A row without them is a different, poorer shape -- which is how the
events index came to mix full rows with bare "N registered" ones -- so a
published event without either is a data defect, not a display choice.  The
owner's rule: it must not be possible to have one.

The rule is enforced where events are written, never patched over where they
are read:

* ``events.identity.create_event_identity`` is the only code that creates an
  event, and it refuses one without a type or a speaker -- draft or not -- and
  writes the row and its speakers in one transaction
  (``events/tests/test_event_creation_paths.py`` keeps it the only path);
* Studio and the management API (``events.admin_services``) create through it,
  and refuse an edit that would strip either from any event;
* provider discovery (``scripts/prod/import_events.py``) creates an event only
  when a reviewed source names its type and speakers, and reports the rest;
* the event ingest pipeline fails its whole run if any published event is
  incomplete, and ``scripts/verify_local_dataset.py`` refuses such a dataset --
  which is how rows written before the rule are found.

The type rides in ``Event.tags`` and the speakers are ``EventHost`` rows on a
model the shared ``community_base`` package owns, so no column constraint can
express the rule on its own.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from .identity import decode_event_tags
from .lifecycle import PUBLISHED_STATUSES

TYPE: str = "type"
SPEAKERS: str = "speakers"


class EventNotPublishable(ValidationError):
    """A published event would lack its type, its speakers, or both."""

    def __init__(self, gaps: tuple[str, ...]) -> None:
        self.gaps = gaps
        super().__init__(
            "A published event needs a type and at least one speaker; missing: "
            + ", ".join(gaps)
            + ".",
            code="event_not_publishable",
        )


def publication_gaps(event: Any) -> tuple[str, ...]:
    """Which of ``type`` and ``speakers`` the event is missing, in that order."""

    event_type, _season, _episode = decode_event_tags(event.tags)
    gaps: list[str] = []
    if not event_type:
        gaps.append(TYPE)
    if event.pk is None or not event.event_host_links.exists():
        gaps.append(SPEAKERS)
    return tuple(gaps)


def is_publishable(event: Any) -> bool:
    return not publication_gaps(event)


def require_publishable(event: Any) -> None:
    """Raise :class:`EventNotPublishable` unless the event has a type and a speaker."""

    gaps = publication_gaps(event)
    if gaps:
        raise EventNotPublishable(gaps)


def incomplete_published_events() -> list[dict[str, Any]]:
    """Every published event that lacks a type or a speaker, for a refusal report.

    Identified by row id, public id and title only -- never by anything about
    the people registered for it.
    """

    from community_base.events.models import Event

    incomplete: list[dict[str, Any]] = []
    events = (
        Event.objects.filter(status__in=PUBLISHED_STATUSES)
        .prefetch_related("event_host_links")
        .order_by("start_datetime", "pk")
    )
    for event in events:
        event_type, _season, _episode = decode_event_tags(event.tags)
        gaps = [TYPE] if not event_type else []
        if not event.event_host_links.all():
            gaps.append(SPEAKERS)
        if gaps:
            incomplete.append(
                {
                    "id": event.pk,
                    "public_id": event.public_id,
                    "title": event.title,
                    "starts_at": event.start_datetime.isoformat(),
                    "missing": gaps,
                }
            )
    return incomplete


# --------------------------------------------------------------------------
# The full completeness rule: type, speakers, description and picture
# --------------------------------------------------------------------------
#
# The owner's rule is wider than what the write paths above refuse today: an
# event must also carry a non-empty description and a picture (the banner in
# ``event_registrants.EventOperatorConfig.banner_image``).  Production still
# holds events without a description or a picture, to be backfilled from Luma,
# so the write paths do not refuse those two gaps yet.  What does exist now:
#
# * :func:`incomplete_events` reports every event, in any status, missing any
#   of the four -- the ``dtc_events.W001`` system check and ``/health/ready`` read it;
# * ``event_registrants`` migration 0005 installs PostgreSQL constraint
#   triggers (``DEFERRABLE INITIALLY DEFERRED``) that reject committing an event
#   missing any of the four.  They are installed *disabled*; enabling them is a
#   follow-up schema migration once :func:`incomplete_events` is empty on
#   production.  Keep :data:`COMPLETENESS_RULE_SQL` and the Python rule below in
#   step: ``events/tests/test_event_completeness.py`` checks both.

DESCRIPTION: str = "description"
PICTURE: str = "picture"
REQUIRED_PARTS: tuple[str, ...] = (TYPE, SPEAKERS, DESCRIPTION, PICTURE)


def completeness_gaps(event: Any) -> tuple[str, ...]:
    """Which of type, speakers, description and picture the event is missing."""

    gaps = list(publication_gaps(event))
    if not (event.description or "").strip():
        gaps.append(DESCRIPTION)
    if not _has_picture(event):
        gaps.append(PICTURE)
    return tuple(part for part in REQUIRED_PARTS if part in gaps)


def _has_picture(event: Any) -> bool:
    if event.pk is None:
        return False
    from event_registrants.models import EventOperatorConfig

    return (
        EventOperatorConfig.objects.filter(event_id=event.pk, banner_image__isnull=False)
        .exclude(banner_image=b"")
        .exists()
    )


def incomplete_events() -> list[dict[str, Any]]:
    """Every event, in any status, missing a type, speaker, description or picture.

    Identified by row id, public id, title and status only.
    """

    from community_base.events.models import Event

    from event_registrants.models import EventOperatorConfig

    with_picture = set(
        EventOperatorConfig.objects.filter(banner_image__isnull=False)
        .exclude(banner_image=b"")
        .values_list("event_id", flat=True)
    )
    with_speakers = set(
        Event.objects.filter(event_host_links__isnull=False).values_list("pk", flat=True)
    )
    incomplete: list[dict[str, Any]] = []
    for row in Event.objects.order_by("start_datetime", "pk").values(
        "pk", "public_id", "title", "status", "tags", "description"
    ):
        event_type, _season, _episode = decode_event_tags(row["tags"])
        gaps = []
        if not event_type:
            gaps.append(TYPE)
        if row["pk"] not in with_speakers:
            gaps.append(SPEAKERS)
        if not (row["description"] or "").strip():
            gaps.append(DESCRIPTION)
        if row["pk"] not in with_picture:
            gaps.append(PICTURE)
        if gaps:
            incomplete.append(
                {
                    "id": row["pk"],
                    "public_id": row["public_id"],
                    "title": row["title"],
                    "status": row["status"],
                    "missing": gaps,
                }
            )
    return incomplete


def completeness_summary(limit: int = 50) -> dict[str, Any]:
    """Counts per missing part plus the first ``limit`` offenders' public ids."""

    rows = incomplete_events()
    return {
        "incomplete": len(rows),
        "missing": {part: sum(part in row["missing"] for row in rows) for part in REQUIRED_PARTS},
        "events": [
            {"public_id": row["public_id"], "id": row["id"], "missing": row["missing"]}
            for row in rows[:limit]
        ],
    }
