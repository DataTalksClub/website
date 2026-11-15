"""Application services for managing events, speakers, banners, and registrants."""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import mistune
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
from core.security import UnsafeInputError, validate_url
from event_registrants.models import EventOperatorConfig, EventRegistrationMeta

from .identity import decode_event_tags, encode_event_tags, host_profile_url, package_kind_for_type

DEFAULT_REMINDER_INTERVALS: tuple[str, ...] = ("24h", "20m")
ALLOWED_BANNER_TYPES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
MAX_BANNER_BYTES = 5 * 1024 * 1024

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
    if event.public_id is None or event.status not in {"upcoming", "completed"}:
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
        "status": event.status,
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


def _sync_event_speakers(event: Event, speaker_shorts: list[str]) -> None:
    hosts = [_resolve_or_create_speaker(short) for short in speaker_shorts]
    EventHost.objects.filter(event=event).delete()
    for position, host in enumerate(hosts):
        EventHost.objects.create(event=event, host=host, position=position, role="speaker")


def _encode_all_tags(
    *,
    event_type: str | None,
    season: int | None,
    episode: int | None,
    display_tags: list[str],
) -> list[str]:
    base = encode_event_tags(event_type=event_type, season=season, episode=episode)
    extras = [str(tag).strip() for tag in display_tags if str(tag).strip()]
    return [*base, *extras]


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
    status = str(params.get("status") or "").strip()
    if status:
        qs = qs.filter(status=status)
    if str(params.get("upcoming") or "").lower() in {"1", "true", "yes"}:
        qs = qs.filter(status="upcoming", start_datetime__gte=timezone.now()).order_by(
            "start_datetime", "pk"
        )
    if str(params.get("past") or "").lower() in {"1", "true", "yes"}:
        qs = qs.filter(status__in={"completed", "upcoming"}, start_datetime__lt=timezone.now())
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
    del context
    data = dict(payload or {})
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

    event = Event.objects.create(
        content_id=uuid.uuid4(),
        slug=slug,
        title=title,
        description=description,
        description_html=_render_markdown(description),
        kind=kind,
        platform=platform,
        start_datetime=start_dt,
        end_datetime=end_dt,
        timezone=tz_name,
        location=location,
        zoom_join_url=zoom_join_url,
        recording_url=recording_url,
        tags=_encode_all_tags(
            event_type=event_type,
            season=season,
            episode=episode,
            display_tags=display_tags,
        ),
        materials=materials,
        status="draft",
    )

    speakers = data.get("speakers")
    if isinstance(speakers, list):
        _sync_event_speakers(event, [str(item) for item in speakers])

    reminders_enabled, reminder_intervals = _normalize_reminder_intervals(
        data.get("reminder_intervals")
    )
    EventOperatorConfig.objects.create(
        event=event,
        reminders_enabled=reminders_enabled,
        reminder_intervals=reminder_intervals,
        sponsor_sharing_enabled=bool(data.get("sponsor_sharing_enabled", False)),
        collect_company=bool(data.get("collect_company", False)),
        sponsor_name=str(data.get("sponsor_name") or "").strip(),
    )

    if publish_now:
        event = publish_event(event)

    return {"event": serialize_admin_event(event, include_registrations=True)}


@transaction.atomic
def update_event(
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
    if schedule_changed:
        event.ics_sequence += 1
    event.save()

    if "speakers" in data and data["speakers"] is not None:
        _sync_event_speakers(event, [str(item) for item in data["speakers"]])

    config, _created = EventOperatorConfig.objects.get_or_create(event=event)
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
        event = publish_event(event)

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
        event = publish_event(event)
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
    if event.status in {"cancelled", "archived"}:
        raise EventAdminError(f"Cannot complete an event in status '{event.status}'.")
    recording_url = str(data.get("recording_url") or "").strip()
    if recording_url:
        event.recording_url = recording_url
    event.status = "completed"
    event.save(update_fields=("status", "recording_url", "updated_at"))
    return {"event": serialize_admin_event(event, include_registrations=True)}


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
    content_type = str(data.get("content_type") or "image/png").strip().lower()
    if content_type not in ALLOWED_BANNER_TYPES:
        allowed = ", ".join(ALLOWED_BANNER_TYPES)
        raise EventAdminError(
            f"Unsupported banner content_type '{content_type}'. Allowed: {allowed}."
        )
    encoded = data.get("image_base64")
    if not isinstance(encoded, str) or not encoded.strip():
        raise EventAdminError("image_base64 is required.")
    try:
        raw_bytes = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EventAdminError("Invalid base64 image payload.") from exc
    if not raw_bytes or len(raw_bytes) > MAX_BANNER_BYTES:
        raise EventAdminError(f"Banner image must be between 1 byte and {MAX_BANNER_BYTES} bytes.")

    config, _created = EventOperatorConfig.objects.get_or_create(event=event)
    config.banner_image = raw_bytes
    config.banner_content_type = content_type
    config.banner_updated_at = timezone.now()
    config.save(
        update_fields=("banner_image", "banner_content_type", "banner_updated_at", "updated_at")
    )
    event.save(update_fields=("updated_at",))
    return {"event": serialize_admin_event(event), "size_bytes": len(raw_bytes)}


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
    config, _created = EventOperatorConfig.objects.get_or_create(event=event)
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
