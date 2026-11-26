"""Application services for managing events, speakers, banners, and registrants."""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import mistune
from community_base.events import registration as _package_registration
from community_base.events import services as _package_services
from community_base.events.models import Event, EventHost, EventRegistration, EventReminder, Host
from community_base.events.services import cancel_event, publish_event
from community_base.kernel.services import ServiceContext
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from content.catalogue import people_by_slug
from core.audit import AuditWriteContext, record_audit_event
from core.cyrillic import CYRILLIC_PATTERN
from core.models import AuditEvent
from core.security import UnsafeInputError, validate_url
from event_registrants.models import (
    EventBannerUploadChunk,
    EventOperatorConfig,
    EventRegistrationMeta,
)

from .identity import (
    EventIdentityError,
    create_event_identity,
    decode_event_tags,
    encode_event_tags,
    host_profile_url,
    package_kind_for_type,
)
from .lifecycle import (
    ARCHIVED,
    CANCELLED,
    DRAFT,
    PAST,
    UPCOMING,
    derived_status,
    is_published,
    status_q,
)
from .pictures import (
    ALLOWED_PICTURE_TYPES,
    MAX_PICTURE_BYTES,
    PICTURE_REQUIRED,
    EventPicture,
    EventPictureInvalid,
    EventPictureRequired,
)
from .publication import EventNotPublishable, require_publishable
from .sponsors import UnknownEventSponsor, set_event_sponsors, sponsor_entries

DEFAULT_REMINDER_INTERVALS: tuple[str, ...] = ("24h", "20m")
ALLOWED_BANNER_TYPES: dict[str, str] = ALLOWED_PICTURE_TYPES
MAX_BANNER_BYTES = MAX_PICTURE_BYTES

_MARKDOWN = mistune.create_markdown(escape=False)


class EventAdminError(ValueError):
    """Domain validation or lookup error mapped to HTTP status codes."""

    def __init__(self, message: str, *, status: int = 400, code: str = "validation_error") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(raw: Any, *, field: str) -> datetime:
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str) and raw.strip():
        parsed = parse_datetime(raw.strip())
        if parsed is None:
            raise EventAdminError(f"Invalid ISO 8601 datetime for {field}.")
        dt = parsed
    else:
        raise EventAdminError(f"{field} is required.")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _render_markdown(text: str) -> str:
    if not text:
        return ""
    return str(_MARKDOWN(text))


def _resolve_event(event_id: uuid.UUID | str | int) -> Event:
    if isinstance(event_id, uuid.UUID):
        event = Event.objects.filter(content_id=event_id).first()
    elif isinstance(event_id, int) or (isinstance(event_id, str) and event_id.isdigit()):
        event = Event.objects.filter(public_id=int(event_id)).first()
    elif isinstance(event_id, str):
        try:
            parsed = uuid.UUID(event_id)
        except ValueError:
            event = Event.objects.filter(slug=event_id).first()
        else:
            event = Event.objects.filter(content_id=parsed).first()
    else:
        event = None
    if event is None:
        raise EventAdminError("Event was not found.", status=404, code="event_not_found")
    return event


def _get_config(event: Event) -> EventOperatorConfig | None:
    try:
        return event.operator_config
    except EventOperatorConfig.DoesNotExist:
        return None


def _public_url(event: Event) -> str:
    if event.public_id is None or not is_published(event):
        return ""
    from django.conf import settings

    origin = str(getattr(settings, "CANONICAL_ORIGIN", "")).rstrip("/")
    path = f"/events/{event.public_id}/{event.slug}"
    return f"{origin}{path}" if origin else path


def _banner_url(event: Event, config: EventOperatorConfig | None) -> str:
    if config is not None and config.banner_image and event.public_id is not None:
        return f"/events/{event.public_id}/banner"
    return ""


def serialize_admin_event(event: Event, *, include_registrations: bool = False) -> dict[str, Any]:
    event_type, season, episode = decode_event_tags(event.tags)
    display_tags = [
        tag
        for tag in (event.tags or [])
        if isinstance(tag, str) and not tag.startswith(("dtc-type:", "dtc-season:", "dtc-episode:"))
    ]
    config = _get_config(event)
    links = [
        {"label": link.host.external_ref or link.host.slug, "host": link.host}
        for link in event.event_host_links.select_related("host").order_by("position", "pk")
    ]
    speakers = [
        {
            "id": item["host"].pk,
            "short": item["host"].external_ref or item["host"].slug,
            "name": item["host"].name,
            "role": item["host"].kind,
            "public_path": host_profile_url(item["host"]) or "",
        }
        for item in links
    ]
    reminders_enabled = config.reminders_enabled if config is not None else True
    reminder_intervals = (
        list(config.reminder_intervals)
        if config is not None and config.reminder_intervals
        else (list(DEFAULT_REMINDER_INTERVALS) if reminders_enabled else [])
    )
    payload: dict[str, Any] = {
        "id": str(event.content_id) if event.content_id else "",
        "pk": event.pk,
        "public_id": event.public_id,
        "slug": event.slug,
        "title": event.title,
        "description": event.description,
        "type": event_type or "",
        "kind": event.kind,
        "platform": event.platform,
        # Derived: draft/cancelled/archived as stored, otherwise upcoming,
        # in_progress or past from the schedule (``events.lifecycle``).
        "status": derived_status(event),
        "start_datetime": _iso(event.start_datetime),
        "end_datetime": _iso(event.end_datetime),
        "timezone": event.timezone,
        "location": event.location,
        "zoom_join_url": event.zoom_join_url,
        "recording_url": event.recording_url,
        "season": season,
        "episode": episode,
        "tags": display_tags,
        "materials": list(event.materials or []),
        "speakers": speakers,
        "public_path": (
            f"/events/{event.public_id}/{event.slug}" if event.public_id is not None else ""
        ),
        "public_url": _public_url(event),
        "banner_url": _banner_url(event, config),
        "reminders_enabled": reminders_enabled,
        "reminder_intervals": reminder_intervals,
        "sponsor_sharing_enabled": config.sponsor_sharing_enabled if config is not None else False,
        "collect_company": config.collect_company if config is not None else False,
        "sponsor_name": config.sponsor_name if config is not None else "",
        # Directory sponsors of a sponsored event (``events.sponsors``), in order.
        "sponsors": sponsor_entries(event),
        "published_at": _iso(event.published_at),
        "updated_at": _iso(event.updated_at),
    }
    if include_registrations:
        regs = EventRegistration.objects.filter(event=event)
        payload["registration_counts"] = {
            "total": regs.count(),
            "confirmed": regs.filter(status=EventRegistration.Status.CONFIRMED).count(),
            "attended": regs.filter(status=EventRegistration.Status.ATTENDED).count(),
            "cancelled": regs.filter(status=EventRegistration.Status.CANCELLED).count(),
            "pending_verification": regs.filter(
                status=EventRegistration.Status.PENDING_VERIFICATION
            ).count(),
        }
    return payload


def _resolve_or_create_speaker(short: str) -> Host:
    key = short.strip().lstrip("@")
    if not key:
        raise EventAdminError("Speaker short key cannot be blank.")
    existing = (
        Host.objects.filter(external_ref=key).first() or Host.objects.filter(slug=key).first()
    )
    if existing is not None:
        return existing
    catalogue_people = people_by_slug()
    person = catalogue_people.get(key)
    if person is None:
        raise EventAdminError(
            f"Speaker '{key}' not found in people catalogue. Create a profile first or run "
            f"`dtc events speakers create --short {key} --name '...'`.",
            status=400,
            code="speaker_not_found",
        )
    name = str(person.get("title") or person.get("name") or key)[:200]
    slug = slugify(key)[:200] or f"speaker-{uuid.uuid4().hex[:8]}"
    host, _created = Host.objects.get_or_create(
        slug=slug,
        defaults={
            "name": name,
            "kind": "speaker",
            "external_ref": key,
            "is_active": True,
        },
    )
    return host


def _publish(event: Event) -> Event:
    """Publish through the package, but only an event with a type and a speaker.

    See :mod:`events.publication`: a published event without either draws a
    poorer row on the public pages, so it is refused here rather than shown.
    """

    try:
        require_publishable(event)
    except EventNotPublishable as exc:
        raise EventAdminError(exc.messages[0], code="event_not_publishable") from exc
    return publish_event(event)


def _enroll_series_registrants_without_outer_join_lock(event: Event) -> Any:
    """Series enrolment for a site that has no event series.

    ``community_base``'s ``publish_event`` ends with
    ``enroll_series_registrants_in_event``, which runs
    ``select_for_update().select_related("event_series")``.  PostgreSQL refuses
    ``FOR UPDATE`` over the nullable side of that outer join, so on production
    every publish failed with ``internal_error`` (SQLite, which the tests use,
    ignores ``FOR UPDATE``).  Lock the event row alone and, when it belongs to no
    series (every event on this site), return the package's own "nothing to do"
    summary; only a series event falls through to the package implementation.
    """

    series_id = (
        Event.objects.select_for_update()
        .filter(pk=event.pk)
        .values_list("event_series_id", flat=True)
        .first()
    )
    if series_id is None:
        return _package_registration.SeriesEnrollmentSummary(0, 0, 0, 0, 1)
    return _package_registration.enroll_series_registrants_in_event(event)


_package_services.enroll_series_registrants_in_event = (
    _enroll_series_registrants_without_outer_join_lock
)


def _sync_event_speakers(event: Event, speaker_shorts: list[str]) -> None:
    hosts = [_resolve_or_create_speaker(short) for short in speaker_shorts]
    EventHost.objects.filter(event=event).delete()
    for position, host in enumerate(hosts):
        EventHost.objects.create(event=event, host=host, position=position, role="speaker")


def _reject_cyrillic(fields: Mapping[str, Any]) -> None:
    """Refuse author-entered public text containing Cyrillic (the site is English-only).

    ``fields`` maps a field name to a string or a list of strings.  The first
    offending field raises a 400 naming it and the Cyrillic characters found,
    so a single homoglyph (Cyrillic U+0441 for Latin "c") is easy to locate.
    """

    for field, value in fields.items():
        values = value if isinstance(value, list | tuple) else [value]
        found = sorted(
            {
                char
                for item in values
                if isinstance(item, str)
                for char in CYRILLIC_PATTERN.findall(item)
            }
        )
        if found:
            characters = ", ".join(f"'{char}' (U+{ord(char):04X})" for char in found)
            raise EventAdminError(
                f"Event {field} must be English: it contains Cyrillic characters "
                f"{characters}. Translate the text or replace them with Latin letters."
            )


def _encode_all_tags(
    *,
    event_type: str | None,
    season: int | None,
    episode: int | None,
    display_tags: list[str],
) -> list[str]:
    base = encode_event_tags(event_type=event_type, season=season, episode=episode)
    extras = [str(tag).strip() for tag in display_tags if str(tag).strip()]
    _reject_cyrillic({"tags": extras})
    return [*base, *extras]


EVENT_AUDIT_TARGET_TYPE = "events.event"


def _audit_snapshot(event: Event) -> dict[str, Any]:
    """Return the operator-editable state of ``event`` for the audit trail.

    The description is recorded as a length and digest so a large body never
    bloats the trail, and the Zoom join URL (an access link) only as a short
    digest.
    No registrant or other personal data is read.
    """

    config = _get_config(event)
    description = event.description or ""
    return {
        "title": event.title,
        "slug": event.slug,
        "description": {
            "length": len(description),
            "sha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
        },
        "kind": event.kind,
        "platform": event.platform,
        "status": event.status,
        "start_datetime": _iso(event.start_datetime),
        "end_datetime": _iso(event.end_datetime),
        "timezone": event.timezone,
        "location": event.location,
        # A short digest, never the access link: a replaced URL is visible as a
        # changed digest without the trail holding a joinable link.
        "zoom_join_url_sha256_12": (
            hashlib.sha256(event.zoom_join_url.encode("utf-8")).hexdigest()[:12]
            if event.zoom_join_url
            else ""
        ),
        "recording_url": event.recording_url,
        "tags": list(event.tags or []),
        "materials": list(event.materials or []),
        "speakers": list(
            EventHost.objects.filter(event=event)
            .order_by("position", "pk")
            .values_list("host__slug", flat=True)
        ),
        "reminders_enabled": config.reminders_enabled if config is not None else None,
        "reminder_intervals": list(config.reminder_intervals) if config is not None else None,
        "sponsor_sharing_enabled": (config.sponsor_sharing_enabled if config is not None else None),
        "collect_company": config.collect_company if config is not None else None,
        "sponsor_name": config.sponsor_name if config is not None else None,
        "sponsors": [item["key"] for item in sponsor_entries(event)],
    }


def _audit_event_write(
    *,
    action: str,
    event: Event,
    before: Mapping[str, Any] | None,
    context: ServiceContext | None,
) -> None:
    """Record a successful event write with the fields it changed."""

    after = _audit_snapshot(event)
    base = before or {}
    changes = {
        field: {"before": base.get(field), "after": value}
        for field, value in after.items()
        if before is None or base.get(field) != value
    }
    record_audit_event(
        action=action,
        target_type=EVENT_AUDIT_TARGET_TYPE,
        target_id=event.content_id,
        target_label=event.slug,
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=AuditWriteContext.for_actor_ref(context or ServiceContext.from_current()),
        changes=changes,
        metadata={"public_id": event.public_id},
    )


def _apply_sponsors(event: Event, raw: Any) -> None:
    """Replace the event's directory sponsors with the keys in ``raw`` (a list)."""

    if not isinstance(raw, list):
        raise EventAdminError("sponsors must be a list of sponsor directory keys.")
    try:
        set_event_sponsors(event, [str(item) for item in raw])
    except UnknownEventSponsor as exc:
        raise EventAdminError(str(exc), code="unknown_sponsor") from exc


def _validate_materials(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise EventAdminError("materials must be a list of {label, url} objects.")
    cleaned: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise EventAdminError("Each material must be an object with label and url.")
        label = str(item.get("label") or "").strip()
        url = str(item.get("url") or "").strip()
        if not label or not url:
            raise EventAdminError("Each material requires non-empty label and url.")
        if len(url) > 500:
            raise EventAdminError("Material url must be 500 characters or fewer.")
        try:
            url = validate_url(url)
        except UnsafeInputError as exc:
            raise EventAdminError(str(exc)) from exc
        cleaned.append({"label": label, "url": url})
    return cleaned


def _normalize_reminder_intervals(raw: Any) -> tuple[bool, list[str]]:
    if raw is None:
        return True, list(DEFAULT_REMINDER_INTERVALS)
    if isinstance(raw, str):
        parts = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, list):
        parts = [str(item).strip() for item in raw if str(item).strip()]
    else:
        raise EventAdminError("reminder_intervals must be a list or comma-separated string.")
    if parts == ["none"] or parts == []:
        return False, []
    return True, parts


def list_events(
    payload: Mapping[str, Any] | None = None, *, context: ServiceContext | None = None
) -> dict[str, Any]:
    del context
    params = dict(payload or {})
    qs = Event.objects.all().order_by("-start_datetime", "pk")
    now = timezone.now()
    status = str(params.get("status") or "").strip()
    if status:
        try:
            qs = qs.filter(status_q(status, now))
        except ValueError as exc:
            raise EventAdminError(f"Unknown event status '{status}'.") from exc
    if str(params.get("upcoming") or "").lower() in {"1", "true", "yes"}:
        qs = qs.filter(status_q(UPCOMING, now)).order_by("start_datetime", "pk")
    if str(params.get("past") or "").lower() in {"1", "true", "yes"}:
        qs = qs.filter(status_q(PAST, now))
    public_id = params.get("public_id")
    if public_id is not None and str(public_id).strip():
        qs = qs.filter(public_id=int(public_id))
    slug = str(params.get("slug") or "").strip()
    if slug:
        qs = qs.filter(slug=slug)
    items = [serialize_admin_event(event) for event in qs[:200]]
    return {"items": items, "total_count": len(items)}


def get_event_detail(
    payload: Mapping[str, Any] | None = None,
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    del context
    target = event_id if event_id is not None else (payload or {}).get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    return {"event": serialize_admin_event(event, include_registrations=True)}


@transaction.atomic
def create_event(
    payload: Mapping[str, Any], *, context: ServiceContext | None = None
) -> dict[str, Any]:
    data = dict(payload or {})
    if "picture_chunk" in data:
        # A chunk of the picture this create call will name by upload_id.
        return stage_event_picture_chunk(
            data["picture_chunk"], is_continuation=bool(data.get("_is_continuation_chunk"))
        )
    title = str(data.get("title") or "").strip()
    if not title:
        raise EventAdminError("title is required.")
    start_dt = _parse_dt(data.get("start_datetime"), field="start_datetime")
    end_dt = (
        _parse_dt(data.get("end_datetime"), field="end_datetime")
        if data.get("end_datetime")
        else None
    )
    if end_dt is not None and end_dt < start_dt:
        raise EventAdminError("end_datetime must not precede start_datetime.")

    event_type = str(data.get("type") or "").strip() or None
    kind = str(data.get("kind") or "").strip() or (
        package_kind_for_type(event_type) if event_type else "standard"
    )
    platform = str(data.get("platform") or "custom").strip()
    tz_name = str(data.get("timezone") or "Europe/Berlin").strip()
    description = str(data.get("description") or "")
    location = str(data.get("location") or "").strip()
    zoom_join_url = str(data.get("zoom_join_url") or "").strip()
    recording_url = str(data.get("recording_url") or "").strip()
    slug = (
        str(data.get("slug") or "").strip()
        or slugify(title)[:70]
        or f"event-{uuid.uuid4().hex[:8]}"
    )
    season = int(data["season"]) if data.get("season") is not None else None
    episode = int(data["episode"]) if data.get("episode") is not None else None
    display_tags = list(data.get("tags") or [])
    materials = _validate_materials(data.get("materials"))
    publish_now = bool(data.get("publish", True))
    sponsor_name = str(data.get("sponsor_name") or "").strip()
    _reject_cyrillic(
        {
            "title": title,
            "slug": slug,
            "description": description,
            "location": location,
            "materials": [item["label"] for item in materials],
            "sponsor_name": sponsor_name,
        }
    )

    # Every event has a type and a speaker from the moment it exists, draft or
    # not (events.identity.create_event_identity is the only creation path).
    if event_type is None:
        raise EventAdminError(
            "type is required: every event needs a type.", code="event_type_required"
        )
    speaker_shorts = data.get("speakers")
    if not isinstance(speaker_shorts, list) or not [
        item for item in speaker_shorts if str(item).strip()
    ]:
        raise EventAdminError(
            "speakers is required: every event needs at least one speaker.",
            code="event_speakers_required",
        )
    hosts = [_resolve_or_create_speaker(str(item)) for item in speaker_shorts if str(item).strip()]
    _reject_cyrillic({"tags": [str(tag).strip() for tag in display_tags if str(tag).strip()]})
    if not description.strip():
        raise EventAdminError(
            "description is required: every event needs a description.",
            code="event_description_required",
        )
    # ... and a picture, written in the same transaction as the event
    # (events.pictures): inline base64, or an upload staged in chunks first.
    picture = _picture_from_payload(data.get("picture"))

    reminders_enabled, reminder_intervals = _normalize_reminder_intervals(
        data.get("reminder_intervals")
    )
    try:
        event = create_event_identity(
            title=title,
            event_type=event_type,
            speakers=hosts,
            starts_at=start_dt,
            ends_at=end_dt,
            season=season,
            episode=episode,
            display_tags=display_tags,
            status="draft",
            description=description,
            picture=picture,
            operator_config={
                "reminders_enabled": reminders_enabled,
                "reminder_intervals": reminder_intervals,
                "sponsor_sharing_enabled": bool(data.get("sponsor_sharing_enabled", False)),
                "collect_company": bool(data.get("collect_company", False)),
                "sponsor_name": sponsor_name,
            },
            fields={
                "slug": slug,
                "description_html": _render_markdown(description),
                "kind": kind,
                "platform": platform,
                "timezone": tz_name,
                "location": location,
                "zoom_join_url": zoom_join_url,
                "recording_url": recording_url,
                "materials": materials,
            },
        )
    except EventIdentityError as exc:
        raise EventAdminError(str(exc), code=str(exc)) from exc
    _consume_staged_picture(data.get("picture"))
    if data.get("sponsors") is not None:
        _apply_sponsors(event, data["sponsors"])

    if publish_now:
        event = _publish(event)

    _audit_event_write(action="events.admin.created", event=event, before=None, context=context)
    return {"event": serialize_admin_event(event, include_registrations=True)}


@transaction.atomic
def update_event(
    payload: Mapping[str, Any],
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    data = dict(payload or {})
    target = event_id if event_id is not None else data.get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    _require_picture_config(event)
    before = _audit_snapshot(event)

    event_type, season, episode = decode_event_tags(event.tags)
    display_tags = [
        tag
        for tag in (event.tags or [])
        if isinstance(tag, str) and not tag.startswith(("dtc-type:", "dtc-season:", "dtc-episode:"))
    ]
    schedule_changed = False

    if "title" in data and data["title"] is not None:
        title = str(data["title"]).strip()
        if not title:
            raise EventAdminError("title cannot be blank.")
        event.title = title
    if "slug" in data and data["slug"] is not None:
        event.slug = str(data["slug"]).strip() or event.slug
    if "description" in data and data["description"] is not None:
        event.description = str(data["description"])
        event.description_html = _render_markdown(event.description)
    if "type" in data:
        event_type = str(data["type"] or "").strip() or None
    if "kind" in data and data["kind"] is not None:
        event.kind = str(data["kind"]).strip()
    if "platform" in data and data["platform"] is not None:
        event.platform = str(data["platform"]).strip()
    if "start_datetime" in data and data["start_datetime"] is not None:
        event.start_datetime = _parse_dt(data["start_datetime"], field="start_datetime")
        schedule_changed = True
    if "end_datetime" in data:
        event.end_datetime = (
            _parse_dt(data["end_datetime"], field="end_datetime") if data["end_datetime"] else None
        )
        schedule_changed = True
    if event.end_datetime is not None and event.end_datetime < event.start_datetime:
        raise EventAdminError("end_datetime must not precede start_datetime.")
    if "timezone" in data and data["timezone"] is not None:
        event.timezone = str(data["timezone"]).strip()
    if "location" in data and data["location"] is not None:
        event.location = str(data["location"]).strip()
        schedule_changed = True
    if "zoom_join_url" in data and data["zoom_join_url"] is not None:
        event.zoom_join_url = str(data["zoom_join_url"]).strip()
    if "recording_url" in data and data["recording_url"] is not None:
        event.recording_url = str(data["recording_url"]).strip()
    if "season" in data:
        season = int(data["season"]) if data["season"] is not None else None
    if "episode" in data:
        episode = int(data["episode"]) if data["episode"] is not None else None
    if "tags" in data and data["tags"] is not None:
        display_tags = list(data["tags"])
    if "materials" in data:
        event.materials = _validate_materials(data["materials"])

    event.tags = _encode_all_tags(
        event_type=event_type,
        season=season,
        episode=episode,
        display_tags=display_tags,
    )
    # The saved state must be English, so a legacy row with Cyrillic in a field
    # is editable once that field is fixed in the same request.
    existing_config = _get_config(event)
    sponsor_name = (
        str(data["sponsor_name"]).strip()
        if data.get("sponsor_name") is not None
        else (existing_config.sponsor_name if existing_config is not None else "")
    )
    _reject_cyrillic(
        {
            "title": event.title,
            "slug": event.slug,
            "description": event.description,
            "location": event.location,
            "materials": [
                str(item.get("label") or "")
                for item in (event.materials or [])
                if isinstance(item, dict)
            ],
            "sponsor_name": sponsor_name,
        }
    )
    # The rule create_event applies, held on every edit too: no save leaves
    # an event without a description (the database refuses it as well).
    if not (event.description or "").strip():
        raise EventAdminError(
            "description is required: every event needs a description.",
            code="event_description_required",
        )
    if schedule_changed:
        event.ics_sequence += 1
    event.save()

    if "speakers" in data and data["speakers"] is not None:
        _sync_event_speakers(event, [str(item) for item in data["speakers"]])
    if "sponsors" in data and data["sponsors"] is not None:
        _apply_sponsors(event, data["sponsors"])

    config = _require_picture_config(event)
    if "reminder_intervals" in data:
        enabled, intervals = _normalize_reminder_intervals(data["reminder_intervals"])
        config.reminders_enabled = enabled
        config.reminder_intervals = intervals
    if "sponsor_sharing_enabled" in data and data["sponsor_sharing_enabled"] is not None:
        config.sponsor_sharing_enabled = bool(data["sponsor_sharing_enabled"])
    if "collect_company" in data and data["collect_company"] is not None:
        config.collect_company = bool(data["collect_company"])
    if "sponsor_name" in data and data["sponsor_name"] is not None:
        config.sponsor_name = str(data["sponsor_name"]).strip()
    config.save()

    if data.get("publish") is True and event.status == "draft":
        event = _publish(event)
    else:
        # An edit may not strip the type or the last speaker from any event,
        # draft or published; the transaction rolls the whole edit back.
        try:
            require_publishable(event)
        except EventNotPublishable as exc:
            raise EventAdminError(exc.messages[0], code="event_not_publishable") from exc

    _audit_event_write(action="events.admin.updated", event=event, before=before, context=context)
    return {"event": serialize_admin_event(event, include_registrations=True)}


@transaction.atomic
def publish_event_action(
    payload: Mapping[str, Any] | None = None,
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    del context
    target = event_id if event_id is not None else (payload or {}).get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    try:
        event = _publish(event)
    except DjangoValidationError as exc:
        raise EventAdminError("; ".join(exc.messages)) from exc
    return {"event": serialize_admin_event(event, include_registrations=True)}


@transaction.atomic
def cancel_event_action(
    payload: Mapping[str, Any] | None = None,
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    del context
    data = dict(payload or {})
    target = event_id if event_id is not None else data.get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    reason = str(data.get("reason") or "").strip()
    if derived_status(event) == PAST:
        # The package refuses only a stored ``completed``; a past event is past
        # whatever its stored status, and cancelling it would mail registrants.
        raise EventAdminError("A past event cannot be cancelled.")
    try:
        event = cancel_event(event, reason=reason)
    except DjangoValidationError as exc:
        raise EventAdminError("; ".join(exc.messages)) from exc
    return {"event": serialize_admin_event(event, include_registrations=True)}


@transaction.atomic
def complete_event_action(
    payload: Mapping[str, Any] | None = None,
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    del context
    data = dict(payload or {})
    target = event_id if event_id is not None else data.get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    # Completion is derived from the schedule, never stored: this action only
    # confirms the event is over and attaches its recording.
    state = derived_status(event)
    if state in {CANCELLED, ARCHIVED}:
        raise EventAdminError(f"Cannot complete an event in status '{state}'.")
    if state == DRAFT:
        raise EventAdminError("Publish the event before completing it.")
    if state != PAST:
        raise EventAdminError("The event has not ended yet; it becomes past when it ends.")
    recording_url = str(data.get("recording_url") or "").strip()
    if recording_url:
        event.recording_url = recording_url
        event.save(update_fields=("recording_url", "updated_at"))
    return {"event": serialize_admin_event(event, include_registrations=True)}


MAX_BANNER_CHUNKS = 2000
BANNER_UPLOAD_TTL = timedelta(hours=1)


def _validated_picture(raw_bytes: bytes, content_type: str) -> EventPicture:
    try:
        return EventPicture.from_bytes(raw_bytes, content_type)
    except EventPictureRequired as exc:
        raise EventAdminError(
            "A picture is required: an event cannot be left without one.",
            code=PICTURE_REQUIRED,
        ) from exc
    except EventPictureInvalid as exc:
        raise EventAdminError(str(exc), code=exc.code) from exc


def _save_event_banner_bytes(event: Event, raw_bytes: bytes, content_type: str) -> dict[str, Any]:
    """Replace the event's picture.  It can be replaced, never cleared."""

    picture = _validated_picture(raw_bytes, content_type)
    config = _get_config(event)
    if config is None:
        # An event written before the picture rule: this upload gives it one.
        config = EventOperatorConfig(event=event)
    config.banner_image = picture.data
    config.banner_content_type = picture.content_type
    config.banner_updated_at = timezone.now()
    if config.pk is None:
        config.save()
    else:
        config.save(
            update_fields=("banner_image", "banner_content_type", "banner_updated_at", "updated_at")
        )
    event.save(update_fields=("updated_at",))
    raw_bytes = picture.data
    return {"event": serialize_admin_event(event), "size_bytes": len(raw_bytes), "complete": True}


def _chunks_for(event: Event | None, upload_id: uuid.UUID) -> Any:
    """The chunks of one upload: bound to ``event``, or staged before creation."""

    if event is None:
        return EventBannerUploadChunk.objects.filter(event__isnull=True, upload_id=upload_id)
    return EventBannerUploadChunk.objects.filter(event=event, upload_id=upload_id)


def _receive_banner_chunk(
    event: Event | None,
    data: Mapping[str, Any],
    *,
    content_type: str,
) -> tuple[uuid.UUID, int, list[EventBannerUploadChunk]]:
    """Store one chunk; return the upload id, the chunk count and the chunks so far.

    ``event`` is ``None`` for a picture staged before the event exists
    (:func:`stage_event_picture_chunk`).
    """

    raw_upload_id = data.get("upload_id")
    try:
        upload_id = uuid.UUID(str(raw_upload_id))
    except (TypeError, ValueError) as exc:
        raise EventAdminError("upload_id must be a valid UUID.") from exc

    raw_index = data.get("chunk_index")
    raw_count = data.get("chunk_count")
    if (
        isinstance(raw_index, bool)
        or isinstance(raw_count, bool)
        or not isinstance(raw_index, int)
        or not isinstance(raw_count, int)
    ):
        raise EventAdminError("chunk_index and chunk_count must be integers.")
    if raw_count < 1 or raw_count > MAX_BANNER_CHUNKS:
        raise EventAdminError(f"chunk_count must be between 1 and {MAX_BANNER_CHUNKS}.")
    if raw_index < 0 or raw_index >= raw_count:
        raise EventAdminError("chunk_index must be between 0 and chunk_count - 1.")

    if data.get("_is_continuation_chunk"):
        if raw_index == 0:
            raise EventAdminError(
                "Continuation banner chunk header is not allowed on chunk_index 0."
            )
        if not _chunks_for(event, upload_id).filter(chunk_index=0).exists():
            raise EventAdminError(
                "Banner upload session was not initialized with chunk 0.",
                status=400,
                code="banner_upload_not_initialized",
            )

    encoded = data.get("chunk_base64")
    if not isinstance(encoded, str) or not encoded.strip():
        raise EventAdminError("chunk_base64 is required for chunked banner uploads.")
    try:
        chunk_bytes = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EventAdminError("Invalid base64 chunk payload.") from exc
    if not chunk_bytes or len(chunk_bytes) > MAX_BANNER_BYTES:
        raise EventAdminError(f"Banner chunk must be between 1 byte and {MAX_BANNER_BYTES} bytes.")

    cutoff = timezone.now() - BANNER_UPLOAD_TTL
    if event is None:
        EventBannerUploadChunk.objects.filter(event__isnull=True, created_at__lt=cutoff).delete()
    else:
        EventBannerUploadChunk.objects.filter(event=event, created_at__lt=cutoff).delete()

    existing = EventBannerUploadChunk.objects.filter(upload_id=upload_id).first()
    if existing is not None and existing.event_id != (event.pk if event is not None else None):
        raise EventAdminError("upload_id belongs to another upload; use a fresh one.")

    EventBannerUploadChunk.objects.update_or_create(
        upload_id=upload_id,
        chunk_index=raw_index,
        defaults={
            "event": event,
            "chunk_count": raw_count,
            "content_type": content_type,
            "data": chunk_bytes,
        },
    )

    chunks = list(_chunks_for(event, upload_id).order_by("chunk_index"))
    total_bytes = sum(len(bytes(item.data)) for item in chunks)
    if total_bytes > MAX_BANNER_BYTES:
        _chunks_for(event, upload_id).delete()
        raise EventAdminError(f"Banner image must be between 1 byte and {MAX_BANNER_BYTES} bytes.")
    return upload_id, raw_count, chunks


def _assembled(chunks: list[EventBannerUploadChunk], chunk_count: int) -> bytes:
    actual_indices = [item.chunk_index for item in chunks]
    if actual_indices != list(range(chunk_count)):
        raise EventAdminError("Banner upload chunks are inconsistent; restart the upload.")
    return b"".join(bytes(item.data) for item in chunks)


def _upload_event_banner_chunk(
    event: Event,
    data: dict[str, Any],
    *,
    content_type: str,
) -> dict[str, Any]:
    upload_id, chunk_count, chunks = _receive_banner_chunk(event, data, content_type=content_type)
    if len(chunks) < chunk_count:
        return {
            "upload_id": str(upload_id),
            "chunk_index": data.get("chunk_index"),
            "received_chunks": len(chunks),
            "chunk_count": chunk_count,
            "complete": False,
        }
    combined = _assembled(chunks, chunk_count)
    _chunks_for(event, upload_id).delete()
    return _save_event_banner_bytes(event, combined, content_type)


def _content_type_or_error(raw: Any) -> str:
    content_type = str(raw or "image/png").strip().lower()
    if content_type not in ALLOWED_BANNER_TYPES:
        allowed = ", ".join(ALLOWED_BANNER_TYPES)
        raise EventAdminError(
            f"Unsupported banner content_type '{content_type}'. Allowed: {allowed}."
        )
    return content_type


@transaction.atomic
def stage_event_picture_chunk(
    chunk: Mapping[str, Any], *, is_continuation: bool = False
) -> dict[str, Any]:
    """Stage one chunk of a picture for an event that is about to be created.

    The edge WAF caps a JSON request body at 8 KB, so ``dtc events create``
    sends the picture in chunks first and then the create call names the
    staged ``upload_id`` (``"picture": {"upload_id": ...}``).  Staged chunks
    belong to no event and expire after :data:`BANNER_UPLOAD_TTL`.
    """

    if not isinstance(chunk, Mapping):
        raise EventAdminError("picture_chunk must be an object.")
    data = dict(chunk)
    if is_continuation:
        data["_is_continuation_chunk"] = True
    content_type = _content_type_or_error(data.get("content_type"))
    upload_id, chunk_count, chunks = _receive_banner_chunk(None, data, content_type=content_type)
    return {
        "picture_upload": {
            "upload_id": str(upload_id),
            "received_chunks": len(chunks),
            "chunk_count": chunk_count,
            "complete": len(chunks) == chunk_count,
        }
    }


def _picture_from_payload(raw: Any) -> EventPicture:
    """The create call's picture: ``{"image_base64", "content_type"}`` or ``{"upload_id"}``."""

    if not isinstance(raw, Mapping) or not raw:
        raise EventAdminError(
            "picture is required: every event needs a picture. Send "
            '"picture": {"image_base64": ..., "content_type": ...} or the upload_id '
            "of a picture staged in chunks.",
            code=PICTURE_REQUIRED,
        )
    if raw.get("upload_id") is not None:
        try:
            upload_id = uuid.UUID(str(raw["upload_id"]))
        except (TypeError, ValueError) as exc:
            raise EventAdminError("picture.upload_id must be a valid UUID.") from exc
        chunks = list(_chunks_for(None, upload_id).order_by("chunk_index"))
        if not chunks:
            raise EventAdminError(
                "No staged picture has that upload_id (it may have expired); upload it again.",
                code=PICTURE_REQUIRED,
            )
        combined = _assembled(chunks, chunks[0].chunk_count)
        return _validated_picture(combined, chunks[0].content_type)
    encoded = raw.get("image_base64")
    if not isinstance(encoded, str) or not encoded.strip():
        raise EventAdminError(
            "picture.image_base64 or picture.upload_id is required.", code=PICTURE_REQUIRED
        )
    try:
        raw_bytes = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EventAdminError("Invalid base64 picture payload.") from exc
    content_type = str(raw.get("content_type") or "").strip().lower()
    if content_type:
        content_type = _content_type_or_error(content_type)
    return _validated_picture(raw_bytes, content_type)


def _consume_staged_picture(raw: Any) -> None:
    if isinstance(raw, Mapping) and raw.get("upload_id") is not None:
        _chunks_for(None, uuid.UUID(str(raw["upload_id"]))).delete()


def _require_picture_config(event: Event) -> EventOperatorConfig:
    """The event's picture row; an event without a picture cannot be edited until it has one."""

    config = _get_config(event)
    if config is None or not config.banner_image:
        raise EventAdminError(
            "This event has no picture. Upload one first "
            "(dtc events upload-banner, or POST .../banner); no other edit is accepted until then.",
            status=409,
            code=PICTURE_REQUIRED,
        )
    return config


@transaction.atomic
def upload_event_banner(
    payload: Mapping[str, Any],
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    del context
    data = dict(payload or {})
    target = event_id if event_id is not None else data.get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    content_type = _content_type_or_error(data.get("content_type"))
    if data.get("upload_id") is not None:
        return _upload_event_banner_chunk(event, data, content_type=content_type)
    if data.get("_is_continuation_chunk"):
        raise EventAdminError("upload_id is required when using chunked banner upload.")

    encoded = data.get("image_base64")
    if not isinstance(encoded, str) or not encoded.strip():
        raise EventAdminError(
            "image_base64 is required: a picture can be replaced, never cleared.",
            code=PICTURE_REQUIRED,
        )
    try:
        raw_bytes = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EventAdminError("Invalid base64 image payload.") from exc
    return _save_event_banner_bytes(event, raw_bytes, content_type)


def export_event_registrants(
    payload: Mapping[str, Any] | None = None,
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    del context
    params = dict(payload or {})
    target = event_id if event_id is not None else params.get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    newsletter_only = str(params.get("newsletter_only") or "").lower() in {"1", "true", "yes"}
    sponsor_only = str(params.get("sponsor_consented_only") or "").lower() in {"1", "true", "yes"}

    qs = (
        EventRegistration.objects.filter(event=event)
        .select_related("meta")
        .order_by("created_at", "id")
    )
    if newsletter_only:
        qs = qs.filter(newsletter_consent=True)

    rows: list[dict[str, Any]] = []
    for reg in qs:
        try:
            meta = reg.meta
        except EventRegistrationMeta.DoesNotExist:
            meta = None
        sponsor_consent = meta.sponsor_sharing_consent if meta is not None else None
        if sponsor_only and sponsor_consent is not True:
            continue
        rows.append(
            {
                "id": str(reg.pk),
                "email": reg.normalized_email,
                "display_name": reg.display_name,
                "status": reg.status,
                "newsletter_consent": reg.newsletter_consent,
                "company": meta.company if meta is not None else "",
                "sponsor_sharing_consent": sponsor_consent,
                "created_at": _iso(reg.created_at),
                "verified_at": _iso(reg.verified_at),
                "attended_at": _iso(reg.attended_at),
            }
        )
    return {
        "event_id": str(event.content_id) if event.content_id else "",
        "public_id": event.public_id,
        "title": event.title,
        "total_count": len(rows),
        "items": rows,
    }


@transaction.atomic
def import_event_attendance(
    payload: Mapping[str, Any],
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    del context
    data = dict(payload or {})
    target = event_id if event_id is not None else data.get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    raw_emails = data.get("emails")
    if not isinstance(raw_emails, list) or not raw_emails:
        raise EventAdminError("emails must be a non-empty list of email addresses.")
    target_status = str(data.get("status") or EventRegistration.Status.ATTENDED).strip()
    if target_status not in {
        EventRegistration.Status.ATTENDED,
        EventRegistration.Status.NO_SHOW,
    }:
        raise EventAdminError("status must be 'attended' or 'no_show'.")

    normalized = sorted({str(email).strip().lower() for email in raw_emails if str(email).strip()})
    now = timezone.now()
    matched = EventRegistration.objects.filter(event=event, normalized_email__in=normalized)
    matched_emails = set(matched.values_list("normalized_email", flat=True))
    update_fields: dict[str, Any] = {"status": target_status, "updated_at": now}
    if target_status == EventRegistration.Status.ATTENDED:
        update_fields["attended_at"] = now
    updated_count = matched.update(**update_fields)
    unknown = [email for email in normalized if email not in matched_emails]
    return {
        "event_id": str(event.content_id) if event.content_id else "",
        "status": target_status,
        "submitted": len(normalized),
        "updated": updated_count,
        "unknown_count": len(unknown),
    }


def message_event_registrants(
    payload: Mapping[str, Any],
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    del context
    data = dict(payload or {})
    target = event_id if event_id is not None else data.get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if not subject or not body:
        raise EventAdminError("subject and body are required.")
    _reject_cyrillic({"message subject": subject, "message body": body})
    audience = str(data.get("audience") or "confirmed").strip()
    if audience == "confirmed":
        statuses = [EventRegistration.Status.CONFIRMED, EventRegistration.Status.ATTENDED]
    elif audience == "attended":
        statuses = [EventRegistration.Status.ATTENDED]
    elif audience == "all_active":
        statuses = [
            EventRegistration.Status.PENDING_VERIFICATION,
            EventRegistration.Status.CONFIRMED,
            EventRegistration.Status.ATTENDED,
        ]
    else:
        raise EventAdminError("audience must be confirmed, attended, or all_active.")

    dry_run = bool(data.get("dry_run", False))
    recipients = list(
        EventRegistration.objects.filter(event=event, status__in=statuses)
        .values_list("normalized_email", flat=True)
        .order_by("normalized_email")
    )
    sent = 0
    if not dry_run:
        from django.conf import settings

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@datatalks.club")
        for recipient in recipients:
            send_mail(subject, body, from_email, [recipient], fail_silently=False)
            sent += 1
    return {
        "event_id": str(event.content_id) if event.content_id else "",
        "dry_run": dry_run,
        "audience": audience,
        "recipient_count": len(recipients),
        "sent": sent,
    }


@transaction.atomic
def manage_event_reminders(
    payload: Mapping[str, Any] | None = None,
    *,
    event_id: uuid.UUID | str | int | None = None,
    context: ServiceContext | None = None,
) -> dict[str, Any]:
    del context
    data = dict(payload or {})
    target = event_id if event_id is not None else data.get("event_id")
    if target is None:
        raise EventAdminError("event_id is required.")
    event = _resolve_event(target)
    config = _require_picture_config(event)
    if "reminder_intervals" in data:
        enabled, intervals = _normalize_reminder_intervals(data["reminder_intervals"])
        config.reminders_enabled = enabled
        config.reminder_intervals = intervals
        config.save(update_fields=("reminders_enabled", "reminder_intervals", "updated_at"))
        event.save(update_fields=("updated_at",))
    reminders_qs = EventReminder.objects.filter(registration__event=event)
    return {
        "event_id": str(event.content_id) if event.content_id else "",
        "reminders_enabled": config.reminders_enabled,
        "reminder_intervals": (
            list(config.reminder_intervals)
            if config.reminder_intervals
            else (list(DEFAULT_REMINDER_INTERVALS) if config.reminders_enabled else [])
        ),
        "counts": {
            "pending": reminders_qs.filter(status=EventReminder.Status.PENDING).count(),
            "sent": reminders_qs.filter(status=EventReminder.Status.SENT).count(),
            "skipped": reminders_qs.filter(status=EventReminder.Status.SKIPPED).count(),
            "failed": reminders_qs.filter(status=EventReminder.Status.FAILED).count(),
        },
    }


def list_speakers(
    payload: Mapping[str, Any] | None = None, *, context: ServiceContext | None = None
) -> dict[str, Any]:
    del context
    search = str((payload or {}).get("search") or "").strip().lower()
    qs = Host.objects.filter(is_active=True).order_by("name", "pk")
    items = []
    for host in qs:
        short = host.external_ref or host.slug
        if search and search not in short.lower() and search not in host.name.lower():
            continue
        items.append(
            {
                "id": host.pk,
                "short": short,
                "name": host.name,
                "kind": host.kind,
                "title": host.title,
                "public_path": host_profile_url(host) or "",
            }
        )
    return {"items": items[:200], "total_count": len(items)}


@transaction.atomic
def create_speaker(
    payload: Mapping[str, Any], *, context: ServiceContext | None = None
) -> dict[str, Any]:
    del context
    data = dict(payload or {})
    short = str(data.get("short") or "").strip().lstrip("@")
    name = str(data.get("name") or "").strip()
    if not short or not name:
        raise EventAdminError("short and name are required.")
    _reject_cyrillic(
        {
            "speaker short": short,
            "speaker name": name,
            "speaker title": str(data.get("title") or ""),
            "speaker bio": str(data.get("bio") or ""),
        }
    )
    slug = slugify(short)[:200] or f"speaker-{uuid.uuid4().hex[:8]}"
    host, _created = Host.objects.update_or_create(
        slug=slug,
        defaults={
            "name": name[:200],
            "kind": "speaker",
            "external_ref": short[:255],
            "title": str(data.get("title") or "").strip()[:200],
            "bio": str(data.get("bio") or "").strip(),
            "photo_url": str(data.get("photo_url") or "").strip()[:500],
            "is_active": True,
        },
    )
    return {
        "speaker": {
            "id": host.pk,
            "short": host.external_ref or host.slug,
            "name": host.name,
            "kind": host.kind,
            "title": host.title,
            "public_path": host_profile_url(host) or "",
        }
    }
