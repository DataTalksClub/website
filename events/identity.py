"""The DTC event identity policy, carried over from the former site ``events`` app.

The ``events`` Django app label now belongs to ``community_base.events`` (#412);
this module is the site adapter that keeps DTC's identity policy on top of the
package model: the never-reused public ID allocator, the immutable
``content_id`` UUID (the site identity that preceded the shared app), the
source-identity tuple the reviewed imports resolve by, and the canonical path
builders every live route, Studio page and ingest script shares.

What this module is NOT: the package's own event product logic. Registration,
reminders, studio CRUD and the package's slug/URL conventions live in
``community_base.events``; DTC public pages keep their own routes and read the
package rows through :mod:`events.queries`.

Provenance that has no package column (the reviewed ``source_key`` and
``source_checksum``, which the description bridge re-checks on every import)
lives in the site-owned ``content.EventSource`` row keyed by the same event;
the DTC display type, season and episode ride in the row's ``tags`` JSON.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from time import sleep
from typing import Any

from community_base.events.models import Event, EventPublicIdSequence
from community_base.events.services import allocate_public_id, reserve_public_id
from django.db import OperationalError, transaction
from django.db.models import Max
from django.utils import timezone

from content.models import EventSource

from .slugs import event_title_slug

MAX_PUBLIC_ID = 2_147_483_647
_PUBLIC_ID_ALLOCATION_ATTEMPTS = 5

#: The shared row cannot exist without a schedule.  Callers that mint an
#: identity before any source has stated a start -- the Q&A and registrant
#: test factories, provider discovery before the export is read -- get this
#: standing instant; every caller with real schedule data passes it and the
#: content import reconciles it.
DEFAULT_STARTS_AT = datetime(2026, 1, 1, tzinfo=UTC)

#: The package owns its own lifecycle vocabulary.  The site map is total: every
#: DTC lifecycle value lands on the package status with the same public meaning.
STATUS_BY_LIFECYCLE: dict[str, str] = {
    "draft": "draft",
    "published": "upcoming",
    "completed": "completed",
    "cancelled": "cancelled",
    "archived": "archived",
}

#: Package event kinds the DTC type maps onto directly.  Podcast, webinar and
#: conference have no package kind; they keep their exact DTC value in ``tags``
#: and the public pages read it from there.
_KINDS_WITH_PACKAGE_EQUIVALENT = frozenset({"workshop"})


class EventIdentityError(ValueError):
    """A bounded manifest or exact-identity failure."""


class EventIdentityNotFound(LookupError):
    """An unknown UUID, public ID, or source identity was requested."""


# --------------------------------------------------------------------------
# The DTC display vocabulary carried in the package row's JSON fields
# --------------------------------------------------------------------------

_TYPE_TAG = "dtc-type:"
_SEASON_TAG = "dtc-season:"
_EPISODE_TAG = "dtc-episode:"


def encode_event_tags(
    *,
    event_type: str | None,
    season: int | None,
    episode: int | None,
) -> list[str]:
    """Encode the DTC display vocabulary the package schema has no column for."""

    tags: list[str] = []
    if event_type:
        tags.append(f"{_TYPE_TAG}{event_type}")
    if season is not None:
        tags.append(f"{_SEASON_TAG}{season}")
    if episode is not None:
        tags.append(f"{_EPISODE_TAG}{episode}")
    return tags


def decode_event_tags(tags: list[str] | None) -> tuple[str | None, int | None, int | None]:
    """Return the ``(type, season, episode)`` triple encoded in an event's tags."""

    event_type: str | None = None
    season: int | None = None
    episode: int | None = None
    for tag in tags or []:
        if not isinstance(tag, str):
            continue
        if tag.startswith(_TYPE_TAG):
            event_type = tag[len(_TYPE_TAG) :]
        elif tag.startswith(_SEASON_TAG) and tag[len(_SEASON_TAG) :].isdigit():
            season = int(tag[len(_SEASON_TAG) :])
        elif tag.startswith(_EPISODE_TAG) and tag[len(_EPISODE_TAG) :].isdigit():
            episode = int(tag[len(_EPISODE_TAG) :])
    return event_type, season, episode


def package_kind_for_type(event_type: str | None) -> str:
    """The package ``kind`` a DTC event type maps onto."""

    if event_type in _KINDS_WITH_PACKAGE_EQUIVALENT:
        return event_type  # type: ignore[return-value]
    return "standard"


# --------------------------------------------------------------------------
# Identity: allocation, lookup, and canonical-path building
# --------------------------------------------------------------------------


def ensure_public_id_sequence() -> int:
    """Park the package allocator above every public ID that already exists.

    The package sequence is a one-row table like the site's was, so something
    has to put the row there and keep it ahead of the rows an import wrote or
    reserved.  That belongs with the code that writes events, not in a
    migration: on an empty database a migration point is *before* the manifest
    import, which would leave the allocator handing out an ID the import had
    already used.

    Returns the ``next_public_id`` the allocator will hand out.
    """

    latest = Event.objects.aggregate(value=Max("public_id"))["value"] or 0
    row, created = EventPublicIdSequence.objects.get_or_create(
        pk=1,
        defaults={"next_public_id": latest + 1},
    )
    if not created and row.next_public_id <= latest:
        EventPublicIdSequence.objects.filter(pk=1, next_public_id=row.next_public_id).update(
            next_public_id=latest + 1,
            updated_at=timezone.now(),
        )
        return latest + 1
    return row.next_public_id


def _create_event_identity_atomic(
    *,
    title: str,
    starts_at: Any = None,
    source_repository: str,
    source_revision: str,
    source_key: str,
    source_path: str = "",
    source_checksum: str = "",
    ends_at: Any = None,
    event_type: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    status: str = "upcoming",
    event_id: uuid.UUID | None = None,
    public_id: int | None = None,
) -> Event:
    with transaction.atomic():
        event = Event(
            content_id=event_id or uuid.uuid4(),
            title=title,
            slug=event_title_slug(title),
            status=status,
            start_datetime=starts_at if starts_at is not None else DEFAULT_STARTS_AT,
            end_datetime=ends_at,
            kind=package_kind_for_type(event_type),
            tags=encode_event_tags(event_type=event_type, season=season, episode=episode),
            source_repo=source_repository,
            source_path=source_path,
            source_commit=source_revision,
        )
        # The row must exist before either allocator service will take it: both
        # lock and reload by primary key.
        event.save()
        if public_id is None:
            allocated = allocate_public_id(event)
            if allocated > MAX_PUBLIC_ID:
                raise EventIdentityError("event_public_id_allocator_invalid")
        else:
            if public_id < 1 or public_id > MAX_PUBLIC_ID:
                raise EventIdentityError("event_public_id_out_of_range")
            allocated = reserve_public_id(event, public_id)
        event.public_id = allocated
        EventSource.objects.create(
            event=event,
            repository=source_repository,
            revision=source_revision,
            source_key=source_key,
            source_path=source_path,
            source_checksum=source_checksum,
        )
        # Event creation owns Q&A provisioning.  Keep the import local so this
        # module does not import the Q&A implementation at module load time.
        from event_qna.services import ensure_event_qna

        ensure_event_qna(event.content_id)
        return event


def create_event_identity(
    *,
    title: str,
    starts_at: Any = None,
    source_repository: str,
    source_revision: str,
    source_key: str,
    source_path: str = "",
    source_checksum: str = "",
    ends_at: Any = None,
    event_type: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    status: str = "upcoming",
    event_id: uuid.UUID | None = None,
    public_id: int | None = None,
) -> Event:
    """Create one event with a portable, never-reused public route identifier.

    ``public_id`` is how the reviewed manifest import preserves the identifier
    an event has always been addressed by; provider discovery leaves it ``None``
    and takes the next allocated one.
    """

    for attempt in range(_PUBLIC_ID_ALLOCATION_ATTEMPTS):
        try:
            return _create_event_identity_atomic(
                title=title,
                starts_at=starts_at,
                source_repository=source_repository,
                source_revision=source_revision,
                source_key=source_key,
                source_path=source_path,
                source_checksum=source_checksum,
                ends_at=ends_at,
                event_type=event_type,
                season=season,
                episode=episode,
                status=status,
                event_id=event_id,
                public_id=public_id,
            )
        except OperationalError:
            if attempt == _PUBLIC_ID_ALLOCATION_ATTEMPTS - 1:
                raise
            sleep(0.01 * (2**attempt))
    raise AssertionError("public ID allocation retry loop exhausted without returning")


# The three helpers below back both a live path (``content.public_views`` and
# the studio identity pages) and an ingestion one
# (``scripts.prod.registrant_import.ExistingEventIndex``).
_PROVIDER_EVENT_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_CANONICAL_SOURCE_KEY_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")


def normalize_event_title(title: str) -> str:
    """Collapse whitespace and fold case so two titles compare exactly."""

    return " ".join(title.split()).casefold()


def canonical_event_date(source_key: str) -> str | None:
    """The event's date, read from its canonical ``YYYY-MM-DD-slug`` source key.

    Some events have no date component in their source key at all: the older
    podcast-style legacy entries, and every event minted from a provider export
    (whose source key is the provider's own opaque event id).  Those never enter
    a duplicate-creation guard built from this, so a provider event can neither
    match one nor match a duplicate a previous buggy run created -- replay
    idempotency stays the source identity's job, not that guard's.
    """

    match = _CANONICAL_SOURCE_KEY_DATE.match(source_key)
    return match.group(1) if match else None


def provider_event_date(start_at: str) -> str | None:
    """The calendar date a provider export's start timestamp names, if any."""

    match = _PROVIDER_EVENT_DATE.match(start_at)
    return match.group(1) if match else None


def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        if value.variant != uuid.RFC_4122:
            raise EventIdentityNotFound("unknown_event")
        return value
    if not isinstance(value, str):
        raise EventIdentityNotFound("unknown_event")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise EventIdentityNotFound("unknown_event") from exc
    if str(parsed) != value or parsed.variant != uuid.RFC_4122:
        raise EventIdentityNotFound("unknown_event")
    return parsed


def resolve_uuid(event_id: uuid.UUID | str) -> Event:
    """Resolve the immutable site identity UUID, kept as the package ``content_id``."""

    parsed = _coerce_uuid(event_id)
    try:
        return Event.objects.get(content_id=parsed)
    except Event.DoesNotExist as exc:
        raise EventIdentityNotFound("unknown_event") from exc


def resolve_public_id(public_id: int | str) -> Event:
    """Resolve the stable numeric identifier used only by public event routes."""

    if isinstance(public_id, bool):
        raise EventIdentityNotFound("unknown_event")
    if isinstance(public_id, int):
        parsed = public_id
    elif isinstance(public_id, str) and re.fullmatch(r"[1-9][0-9]*", public_id):
        parsed = int(public_id)
    else:
        raise EventIdentityNotFound("unknown_event")
    if parsed < 1 or parsed > MAX_PUBLIC_ID:
        raise EventIdentityNotFound("unknown_event")
    try:
        return Event.objects.get(public_id=parsed)
    except Event.DoesNotExist as exc:
        raise EventIdentityNotFound("unknown_event") from exc


def resolve_source_identity(*, repository: str, revision: str, source_key: str) -> Event:
    try:
        source = EventSource.objects.select_related("event").get(
            repository=repository,
            revision=revision,
            source_key=source_key,
        )
    except EventSource.DoesNotExist as exc:
        raise EventIdentityNotFound("source_identity_unmapped") from exc
    except EventSource.MultipleObjectsReturned as exc:
        raise EventIdentityError("source_identity_ambiguous") from exc
    return source.event


def current_slug(event_id: uuid.UUID | str) -> str:
    return resolve_uuid(event_id).slug


def canonical_detail_path(event_id: uuid.UUID | str) -> str:
    event = resolve_uuid(event_id)
    if event.public_id is None:
        raise EventIdentityNotFound("event_public_id_unavailable")
    return f"/events/{event.public_id}/{event.slug}"


def canonical_detail_url(event_id: uuid.UUID | str) -> str:
    from core.runtime_config import get_str_setting

    return f"{get_str_setting('site.origin.canonical')}{canonical_detail_path(event_id)}"


def canonical_registration_path(event_id: uuid.UUID | str) -> str:
    return f"{canonical_detail_path(event_id)}/register"


def redirect_for_supplied_slug(event_id: uuid.UUID | str, supplied_slug: str) -> str | None:
    event = resolve_uuid(event_id)
    return None if supplied_slug == event.slug else canonical_detail_path(event.content_id)


def serialize_event_identity(event: Event) -> dict[str, Any]:
    """Serialize the authorized identity view without public or attendee data."""

    try:
        source = event.source_identity
    except EventSource.DoesNotExist:
        source = None
    return {
        "id": str(event.content_id),
        "public_id": event.public_id,
        "public_url": canonical_detail_url(event.content_id),
        "title": event.title,
        "slug": event.slug,
        "canonical_path": canonical_detail_path(event.content_id),
        "registration_path": canonical_registration_path(event.content_id),
        "provenance": {
            "repository": source.repository if source else event.source_repo,
            "revision": source.revision if source else event.source_commit,
            "source_key": source.source_key if source else "",
            "source_path": source.source_path if source else (event.source_path or ""),
            "source_checksum": source.source_checksum if source else "",
        },
    }


def list_event_identities(*, page: int = 1, page_size: int = 100) -> dict[str, Any]:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("event_identity_page_invalid")
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100:
        raise ValueError("event_identity_page_size_invalid")
    queryset = Event.objects.select_related("source_identity").order_by("pk")
    total = queryset.count()
    offset = (page - 1) * page_size
    return {
        "items": [
            serialize_event_identity(event) for event in queryset[offset : offset + page_size]
        ],
        "page": page,
        "page_size": page_size,
        "total_count": total,
    }


def get_event_identity(event_id: uuid.UUID | str) -> dict[str, Any]:
    return serialize_event_identity(resolve_uuid(event_id))


def host_profile_url(host: Any) -> str:
    """The ``HOST_PROFILE_RESOLVER`` callback: a package host's site profile path.

    The package stores speakers as ``Host(kind="speaker", external_ref=<key>)``
    rows; the site joins that key to its own people catalogue at request time,
    so editing a profile updates every event page that credits the person
    without copying anything onto the host row.  An unknown key resolves to no
    path rather than an invented one.
    """

    from content.catalogue import person_paths_by_slug

    key = getattr(host, "external_ref", "")
    if not key:
        return ""
    return person_paths_by_slug().get(key, "")
