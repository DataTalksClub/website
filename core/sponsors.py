"""Database-owned sponsor directory shared by Studio, the admin API, and public pages."""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.db import DatabaseError, IntegrityError
from django.db.models import Prefetch
from django.utils.dateparse import parse_datetime

from core.audit import AuditWriteContext, record_audit_event
from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from core.idempotency import (
    IdempotencyResult,
    JsonObject,
    JsonValue,
    execute_idempotent,
    hash_idempotency_key,
)
from core.models import (
    AuditEvent,
    RevisionConflict,
    Sponsor,
    SponsorPlacementAssignment,
    SponsorRevision,
)
from core.security import UnsafeInputError, neutralize_csv_formula, validate_url
from core.services import ServiceContext, validate_actor_ref

logger = logging.getLogger(__name__)

SPONSOR_PLACEMENT_EVENTS_HUB = "events_hub"
SPONSOR_PLACEMENTS = (SPONSOR_PLACEMENT_EVENTS_HUB,)
SPONSOR_SOURCES = frozenset({"studio", "admin_api"})
SPONSOR_LIFECYCLES = frozenset(Sponsor.Lifecycle.values)
SPONSOR_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SPONSOR_NAME_MAX = 120
SPONSOR_TAGLINE_MAX = 200
SPONSOR_URL_MAX = 500
SPONSOR_REASON_MAX = 200
SPONSOR_ACTIVE_PER_PLACEMENT = 24
SPONSOR_EXPORT_MAX_ROWS = 1000
SPONSOR_DEFAULT_PAGE_SIZE = 20
SPONSOR_MAX_PAGE_SIZE = 100
SPONSOR_READ_PERMISSION = "core.read_sponsors"
SPONSOR_WRITE_PERMISSION = "core.change_sponsors"
SPONSOR_EXPORT_PERMISSION = "core.export_sponsors"
SPONSOR_FILTER_FIELDS = ("key", "lifecycle", "placement")
SPONSOR_SORT_FIELDS = ("key", "lifecycle", "created_at", "updated_at")
SPONSOR_EXPORT_COLUMNS = (
    "key",
    "name",
    "url",
    "placement",
    "position",
    "lifecycle",
    "revision",
    "created_at",
    "updated_at",
)
_WRITE_REDACTED = (
    "authorization",
    "body",
    "cookie",
    "csrfmiddlewaretoken",
    "name",
    "tagline",
    "token",
    "url",
)
_EXPORT_REDACTED = (
    "authorization",
    "body",
    "cookie",
    "csrfmiddlewaretoken",
    "csv",
    "name",
    "tagline",
    "token",
    "url",
)


class InvalidSponsor(ValueError):
    """A complete sponsor command failed before mutation."""

    def __init__(self, message: str, *, fields: dict[str, str] | None = None) -> None:
        self.fields = fields or {}
        super().__init__(message)


class SponsorNotFound(LookupError):
    """The authorized queryset did not contain the requested sponsor."""


class SponsorRevisionConflict(RuntimeError):
    """The submitted revision no longer matches the stored record."""

    def __init__(self, *, sponsor_id: uuid.UUID, expected: int, actual: int) -> None:
        self.sponsor_id = sponsor_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"sponsor {sponsor_id} expected revision {expected}, found {actual}")


class SponsorPlacementFull(InvalidSponsor):
    """Activating another sponsor would exceed the per-placement cap."""


@dataclass(frozen=True, slots=True)
class NormalizedAssignment:
    placement_key: str
    position: int
    enabled: bool

    def as_dict(self) -> JsonObject:
        return {
            "placement": self.placement_key,
            "position": self.position,
            "enabled": self.enabled,
        }


@dataclass(frozen=True, slots=True)
class NormalizedSponsorPayload:
    key: str | None
    name: str
    url: str
    tagline: str
    lifecycle: str
    assignments: tuple[NormalizedAssignment, ...]


@dataclass(frozen=True, slots=True)
class SponsorCommandResult:
    sponsor: JsonObject
    replayed: bool

    def as_dict(self) -> JsonObject:
        return {"sponsor": self.sponsor, "replayed": self.replayed}


@dataclass(frozen=True, slots=True)
class SponsorExportResult:
    filename: str
    row_count: int
    csv: str
    replayed: bool

    def as_dict(self) -> JsonObject:
        return {
            "filename": self.filename,
            "row_count": self.row_count,
            "csv": self.csv,
            "replayed": self.replayed,
        }


def _reject_control_and_markup(value: str, *, field: str) -> str:
    if any(
        character in "\r\n\t"
        or unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise InvalidSponsor(
            f"{field} must be one safe line",
            fields={field: f"Enter one plain-text {field}."},
        )
    if "<" in value or ">" in value:
        raise InvalidSponsor(
            f"{field} cannot contain markup",
            fields={field: f"Enter plain text without markup in {field}."},
        )
    return value


def _normalize_plain_text(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise InvalidSponsor(
            f"{field} must be a string",
            fields={field: f"Enter a valid {field}."},
        )
    normalized = _reject_control_and_markup(value, field=field).strip()
    if len(normalized) < minimum or len(normalized) > maximum:
        raise InvalidSponsor(
            f"{field} length is invalid",
            fields={field: f"Enter a {field} of {minimum} to {maximum} characters."},
        )
    return normalized


def _normalize_key(value: object) -> str:
    if not isinstance(value, str) or SPONSOR_KEY_PATTERN.fullmatch(value) is None:
        raise InvalidSponsor(
            "sponsor key is invalid",
            fields={"key": "Enter a lowercase key of letters, numbers, and hyphens."},
        )
    return value


def _normalize_url(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InvalidSponsor("sponsor URL is invalid", fields={"url": "Enter an https URL."})
    trimmed = value.strip()
    if not trimmed:
        return ""
    if len(trimmed) > SPONSOR_URL_MAX:
        raise InvalidSponsor(
            "sponsor URL is too long",
            fields={"url": "Enter an https URL of 500 characters or fewer."},
        )
    try:
        validated = validate_url(trimmed)
    except UnsafeInputError as error:
        raise InvalidSponsor(
            "sponsor URL is invalid",
            fields={"url": "Enter an https URL without credentials."},
        ) from error
    parsed = urlsplit(validated)
    if parsed.scheme.casefold() != "https":
        raise InvalidSponsor(
            "sponsor URL must use https",
            fields={"url": "Enter an https URL."},
        )
    return validated


def _normalize_lifecycle(value: object, *, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise InvalidSponsor(
            "sponsor lifecycle is invalid",
            fields={"lifecycle": "Choose a valid lifecycle."},
        )
    return value


def _normalize_assignments(value: object) -> tuple[NormalizedAssignment, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > len(SPONSOR_PLACEMENTS):
        raise InvalidSponsor(
            "sponsor assignments are invalid",
            fields={"assignments": "Submit at most one assignment per placement."},
        )
    normalized: list[NormalizedAssignment] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise InvalidSponsor(
                "sponsor assignments are invalid",
                fields={"assignments": "Each assignment must be an object."},
            )
        allowed_fields = {"placement", "position", "enabled"}
        if set(item) - allowed_fields or not {"placement", "position", "enabled"}.issubset(item):
            raise InvalidSponsor(
                "sponsor assignments are invalid",
                fields={
                    "assignments": "Each assignment must contain placement, position, and enabled.",
                },
            )
        placement = item.get("placement")
        position = item.get("position")
        enabled = item.get("enabled")
        if placement not in SPONSOR_PLACEMENTS or placement in seen:
            raise InvalidSponsor(
                "sponsor placement is invalid",
                fields={"assignments": "Choose a registered placement once."},
            )
        if isinstance(position, bool) or not isinstance(position, int) or position < 1:
            raise InvalidSponsor(
                "sponsor position is invalid",
                fields={"assignments": "Enter a unique position of 1 or greater."},
            )
        if not isinstance(enabled, bool):
            raise InvalidSponsor(
                "sponsor assignment enabled is invalid",
                fields={"assignments": "Enabled must be true or false."},
            )
        seen.add(placement)
        normalized.append(
            NormalizedAssignment(
                placement_key=placement,
                position=position,
                enabled=enabled,
            )
        )
    return tuple(normalized)


def _normalize_payload(
    payload: object,
    *,
    require_key: bool,
    allowed_lifecycles: frozenset[str],
) -> NormalizedSponsorPayload:
    if not isinstance(payload, dict):
        raise InvalidSponsor("sponsor payload is invalid")
    key = _normalize_key(payload.get("key")) if require_key else None
    if not require_key and "key" in payload:
        raise InvalidSponsor(
            "sponsor key cannot be changed",
            fields={"key": "Create a new sponsor instead of renaming the key."},
        )
    return NormalizedSponsorPayload(
        key=key,
        name=_normalize_plain_text(
            payload.get("name"),
            field="name",
            minimum=1,
            maximum=SPONSOR_NAME_MAX,
        ),
        url=_normalize_url(payload.get("url", "")),
        tagline=_normalize_plain_text(
            payload.get("tagline", ""),
            field="tagline",
            minimum=0,
            maximum=SPONSOR_TAGLINE_MAX,
        ),
        lifecycle=_normalize_lifecycle(
            payload.get("lifecycle"),
            allowed=allowed_lifecycles,
        ),
        assignments=_normalize_assignments(payload.get("assignments", [])),
    )


def _normalize_reason(value: object) -> str:
    return _normalize_plain_text(
        value,
        field="reason",
        minimum=1,
        maximum=SPONSOR_REASON_MAX,
    )


def _normalize_page(
    page: object,
    page_size: object,
    sort: object,
    filters: object,
) -> tuple[int, int, tuple[str, ...], dict[str, str]]:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise InvalidSponsor("page is invalid")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= SPONSOR_MAX_PAGE_SIZE
    ):
        raise InvalidSponsor("page size is invalid")
    selected_sort = tuple(sort) if isinstance(sort, (list, tuple)) else ()
    if any(
        not isinstance(part, str) or part.removeprefix("-") not in SPONSOR_SORT_FIELDS
        for part in selected_sort
    ):
        raise InvalidSponsor("sort fields are invalid")
    if filters is None:
        parsed_filters: dict[str, str] = {}
    elif not isinstance(filters, dict):
        raise InvalidSponsor("filters are invalid")
    else:
        parsed_filters = {}
        for key, value in filters.items():
            if key not in SPONSOR_FILTER_FIELDS or not isinstance(value, str):
                raise InvalidSponsor("filters are invalid")
            parsed_filters[key] = value
    if "lifecycle" in parsed_filters and parsed_filters["lifecycle"] not in SPONSOR_LIFECYCLES:
        raise InvalidSponsor("filters are invalid")
    if "placement" in parsed_filters and parsed_filters["placement"] not in SPONSOR_PLACEMENTS:
        raise InvalidSponsor("filters are invalid")
    if "key" in parsed_filters and SPONSOR_KEY_PATTERN.fullmatch(parsed_filters["key"]) is None:
        raise InvalidSponsor("filters are invalid")
    return page, page_size, selected_sort, parsed_filters


def _iso(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed.isoformat()
        return value
    raise InvalidSponsor("sponsor timestamp is invalid")


def _assignment_dicts(assignments: object) -> list[JsonObject]:
    if not isinstance(assignments, list):
        return []
    items: list[JsonObject] = []
    for item in assignments:
        if isinstance(item, NormalizedAssignment):
            items.append(item.as_dict())
        elif isinstance(item, dict):
            placement = item.get("placement") or item.get("placement_key")
            position = item.get("position")
            enabled = item.get("enabled")
            if (
                isinstance(placement, str)
                and isinstance(position, int)
                and not isinstance(position, bool)
                and isinstance(enabled, bool)
            ):
                items.append(
                    {
                        "placement": placement,
                        "position": position,
                        "enabled": enabled,
                    }
                )
    return items


def serialize_sponsor(sponsor: Sponsor) -> JsonObject:
    assignments: list[JsonValue] = [
        {
            "placement": assignment.placement_key,
            "position": assignment.position,
            "enabled": assignment.enabled,
        }
        for assignment in sorted(
            sponsor.assignments.all(),
            key=lambda item: (item.placement_key, item.position, str(item.id)),
        )
    ]
    return {
        "id": str(sponsor.id),
        "key": sponsor.key,
        "name": sponsor.name,
        "url": sponsor.url,
        "tagline": sponsor.tagline,
        "lifecycle": sponsor.lifecycle,
        "source": sponsor.source,
        "revision": sponsor.revision,
        "assignments": assignments,
        "created_at": _iso(sponsor.created_at),
        "updated_at": _iso(sponsor.updated_at),
    }


def _validate_stored_sponsor(payload: JsonObject) -> JsonObject:
    try:
        normalized = _normalize_payload(
            {
                "key": payload.get("key"),
                "name": payload.get("name"),
                "url": payload.get("url", ""),
                "tagline": payload.get("tagline", ""),
                "lifecycle": payload.get("lifecycle"),
                "assignments": payload.get("assignments", []),
            },
            require_key=True,
            allowed_lifecycles=SPONSOR_LIFECYCLES,
        )
    except InvalidSponsor:
        raise
    if payload.get("source") not in SPONSOR_SOURCES:
        raise InvalidSponsor("stored sponsor source is invalid")
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise InvalidSponsor("stored sponsor revision is invalid")
    return {
        **payload,
        "name": normalized.name,
        "url": normalized.url,
        "tagline": normalized.tagline,
        "assignments": [item.as_dict() for item in normalized.assignments],
    }


def _authorized_queryset(*, using: str):
    return (
        Sponsor.objects.using(using)
        .prefetch_related(
            Prefetch(
                "assignments",
                queryset=SponsorPlacementAssignment.objects.using(using).order_by(
                    "placement_key",
                    "position",
                    "id",
                ),
            )
        )
        .order_by("key")
    )


def _query_from_args(query: object) -> tuple[int, int, tuple[str, ...], dict[str, str]]:
    if query is None:
        return _normalize_page(1, SPONSOR_DEFAULT_PAGE_SIZE, (), {})
    page = getattr(query, "page", 1)
    page_size = getattr(query, "page_size", SPONSOR_DEFAULT_PAGE_SIZE)
    sort = getattr(query, "sort", ())
    filters = getattr(query, "filters", {})
    return _normalize_page(page, page_size, sort, filters)


def list_sponsors(
    query: object = None,
    *,
    context: ServiceContext | None = None,
    using: str = "default",
) -> JsonObject:
    """Return one bounded, filtered management page of the sponsor directory."""

    del context
    page, page_size, sort, filters = _query_from_args(query)
    queryset = _authorized_queryset(using=using)
    if "key" in filters:
        queryset = queryset.filter(key=filters["key"])
    if "lifecycle" in filters:
        queryset = queryset.filter(lifecycle=filters["lifecycle"])
    if "placement" in filters:
        queryset = queryset.filter(assignments__placement_key=filters["placement"]).distinct()
    order = sort or ("key",)
    queryset = queryset.order_by(*order)
    total = queryset.count()
    offset = (page - 1) * page_size
    items = [
        _validate_stored_sponsor(serialize_sponsor(sponsor))
        for sponsor in queryset[offset : offset + page_size]
    ]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total_count": total,
    }


def get_sponsor(
    sponsor_id: object,
    *,
    context: ServiceContext | None = None,
    using: str = "default",
) -> JsonObject | None:
    """Fetch one sponsor through the authorized queryset."""

    del context
    if isinstance(sponsor_id, uuid.UUID):
        identifier = sponsor_id
    elif isinstance(sponsor_id, str):
        try:
            identifier = uuid.UUID(sponsor_id)
        except ValueError as error:
            raise InvalidSponsor("sponsor id is invalid") from error
    else:
        raise InvalidSponsor("sponsor id is invalid")
    sponsor = _authorized_queryset(using=using).filter(pk=identifier).first()
    if sponsor is None:
        return None
    return _validate_stored_sponsor(serialize_sponsor(sponsor))


def resolve_public_sponsors(
    placement: str = SPONSOR_PLACEMENT_EVENTS_HUB,
    *,
    using: str = "default",
) -> tuple[JsonObject, ...]:
    """Return enabled active sponsors for one placement in position order."""

    if placement not in SPONSOR_PLACEMENTS:
        raise InvalidSponsor("sponsor placement is invalid")
    rows = list(
        SponsorPlacementAssignment.objects.using(using)
        .select_related("sponsor")
        .filter(
            placement_key=placement,
            enabled=True,
            sponsor__lifecycle=Sponsor.Lifecycle.ACTIVE,
        )
        .order_by("position", "sponsor__key")[:SPONSOR_ACTIVE_PER_PLACEMENT]
    )
    resolved: list[JsonObject] = []
    for assignment in rows:
        stored = _validate_stored_sponsor(serialize_sponsor(assignment.sponsor))
        if stored["lifecycle"] != Sponsor.Lifecycle.ACTIVE:
            raise InvalidSponsor("stored sponsor lifecycle is invalid")
        resolved.append(
            {
                "name": stored["name"],
                "url": stored["url"],
                "tagline": stored["tagline"],
            }
        )
    return tuple(resolved)


def public_events_hub_sponsors(*, using: str = "default") -> tuple[JsonObject, ...]:
    """Fail closed to no public sponsor section when resolution is unavailable."""

    try:
        return resolve_public_sponsors(SPONSOR_PLACEMENT_EVENTS_HUB, using=using)
    except (DatabaseError, InvalidSponsor) as error:
        logger.warning(
            "Public events hub sponsors are unavailable (%s).",
            type(error).__name__,
        )
        return ()


def _idempotency_scope(capability_key: str, actor_ref: str) -> str:
    scope = f"{capability_key}:{actor_ref}"
    if len(scope) > 128:
        raise InvalidSponsor("sponsor actor scope is invalid")
    return scope


def _require_source(source: object) -> str:
    if source not in SPONSOR_SOURCES:
        raise InvalidSponsor("sponsor source is invalid")
    return str(source)


def _require_actor_ref(actor_ref: object, context: ServiceContext | None) -> str:
    if not isinstance(actor_ref, str) or not actor_ref:
        raise InvalidSponsor("sponsor actor is invalid")
    try:
        validate_actor_ref(actor_ref)
    except ValueError as error:
        raise InvalidSponsor("sponsor actor is invalid") from error
    if context is not None and context.actor_ref != actor_ref:
        raise InvalidSponsor("sponsor actor context is invalid")
    return actor_ref


def _require_confirmation(value: object) -> None:
    if value is not True:
        raise InvalidSponsor(
            "confirmation is required",
            fields={"confirmed": "Confirm this action before continuing."},
        )


def _placement_assignments(placement: str, *, using: str) -> list[SponsorPlacementAssignment]:
    return list(
        SponsorPlacementAssignment.objects.using(using)
        .select_related("sponsor")
        .filter(placement_key=placement)
        .order_by("position", "id")
    )


def _assert_assignment_constraints(
    *,
    sponsor_id: uuid.UUID | None,
    lifecycle: str,
    assignments: tuple[NormalizedAssignment, ...],
    using: str,
) -> None:
    for assignment in assignments:
        if not assignment.enabled:
            continue
        current = _placement_assignments(assignment.placement_key, using=using)
        taken_positions = {
            item.position for item in current if item.enabled and item.sponsor_id != sponsor_id
        }
        if assignment.position in taken_positions:
            raise InvalidSponsor(
                "sponsor position is already used",
                fields={"assignments": "Choose a unique position in this placement."},
            )
        if lifecycle != Sponsor.Lifecycle.ACTIVE:
            continue
        active_count = sum(
            1
            for item in current
            if item.enabled
            and item.sponsor.lifecycle == Sponsor.Lifecycle.ACTIVE
            and item.sponsor_id != sponsor_id
        )
        if active_count >= SPONSOR_ACTIVE_PER_PLACEMENT:
            raise SponsorPlacementFull(
                "this placement already has 24 active sponsors",
                fields={
                    "assignments": "This placement already has 24 active sponsors.",
                },
            )


def _assert_stored_active_cap(sponsor: Sponsor, *, using: str) -> None:
    if sponsor.lifecycle != Sponsor.Lifecycle.ACTIVE:
        return
    for assignment in sponsor.assignments.all():
        if not assignment.enabled:
            continue
        active_count = (
            SponsorPlacementAssignment.objects.using(using)
            .filter(
                placement_key=assignment.placement_key,
                enabled=True,
                sponsor__lifecycle=Sponsor.Lifecycle.ACTIVE,
            )
            .count()
        )
        if active_count > SPONSOR_ACTIVE_PER_PLACEMENT:
            raise SponsorPlacementFull(
                "this placement already has 24 active sponsors",
                fields={
                    "assignments": "This placement already has 24 active sponsors.",
                },
            )


def _replace_assignments(
    sponsor: Sponsor,
    assignments: tuple[NormalizedAssignment, ...],
    *,
    using: str,
) -> None:
    SponsorPlacementAssignment.objects.using(using).filter(sponsor=sponsor).delete()
    for assignment in assignments:
        SponsorPlacementAssignment.objects.using(using).create(
            sponsor=sponsor,
            placement_key=assignment.placement_key,
            position=assignment.position,
            enabled=assignment.enabled,
        )


def _write_revision(
    sponsor: Sponsor,
    *,
    audit_event: AuditEvent,
    context: AuditWriteContext,
    using: str,
) -> None:
    SponsorRevision.objects.using(using).create(
        sponsor=sponsor,
        key=sponsor.key,
        name=sponsor.name,
        url=sponsor.url,
        tagline=sponsor.tagline,
        lifecycle=sponsor.lifecycle,
        source=sponsor.source,
        revision=sponsor.revision,
        assignments=_assignment_dicts(serialize_sponsor(sponsor)["assignments"]),
        changed_by_id=context.actor_id,
        changed_by_ref=context.actor_ref,
        audit_event=audit_event,
    )


def _record_mutation(
    *,
    action: str,
    sponsor: Sponsor,
    before_lifecycle: str | None,
    before_revision: int,
    context: AuditWriteContext,
    using: str,
) -> AuditEvent:
    return record_audit_event(
        action=action,
        target_type="core.sponsor",
        target_id=sponsor.id,
        target_label=sponsor.key,
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=context,
        changes={
            "lifecycle": {"before": before_lifecycle, "after": sponsor.lifecycle},
            "revision": {"before": before_revision, "after": sponsor.revision},
        },
        metadata={
            "source": sponsor.source,
            "placements": [assignment.placement_key for assignment in sponsor.assignments.all()],
        },
        using=using,
    )


def _apply_create(
    payload: NormalizedSponsorPayload,
    *,
    source: str,
    context: AuditWriteContext,
    using: str,
) -> JsonObject:
    assert payload.key is not None
    _assert_assignment_constraints(
        sponsor_id=None,
        lifecycle=payload.lifecycle,
        assignments=payload.assignments,
        using=using,
    )
    try:
        sponsor = Sponsor.objects.using(using).create(
            key=payload.key,
            name=payload.name,
            url=payload.url,
            tagline=payload.tagline,
            lifecycle=payload.lifecycle,
            source=source,
            revision=1,
        )
    except IntegrityError as error:
        raise InvalidSponsor(
            "sponsor key already exists",
            fields={"key": "A sponsor with this key already exists."},
        ) from error
    try:
        _replace_assignments(sponsor, payload.assignments, using=using)
    except IntegrityError as error:
        raise InvalidSponsor(
            "sponsor assignment conflicts",
            fields={"assignments": "Choose a unique position in this placement."},
        ) from error
    sponsor = _authorized_queryset(using=using).get(pk=sponsor.pk)
    _assert_stored_active_cap(sponsor, using=using)
    audit_event = _record_mutation(
        action="core.sponsor.created",
        sponsor=sponsor,
        before_lifecycle=None,
        before_revision=0,
        context=context,
        using=using,
    )
    _write_revision(sponsor, audit_event=audit_event, context=context, using=using)
    return serialize_sponsor(sponsor)


def _load_for_update(sponsor_id: uuid.UUID, *, using: str) -> Sponsor:
    sponsor = Sponsor.objects.using(using).filter(pk=sponsor_id).first()
    if sponsor is None:
        raise SponsorNotFound("sponsor was not found")
    return sponsor


def _apply_update(
    sponsor_id: uuid.UUID,
    payload: NormalizedSponsorPayload,
    *,
    expected_revision: int,
    source: str,
    context: AuditWriteContext,
    using: str,
) -> JsonObject:
    sponsor = _load_for_update(sponsor_id, using=using)
    if sponsor.revision != expected_revision:
        raise SponsorRevisionConflict(
            sponsor_id=sponsor.id,
            expected=expected_revision,
            actual=sponsor.revision,
        )
    if sponsor.lifecycle == Sponsor.Lifecycle.ARCHIVED:
        raise InvalidSponsor(
            "archived sponsors cannot be edited",
            fields={"lifecycle": "Reactivate this sponsor before editing it."},
        )
    _assert_assignment_constraints(
        sponsor_id=sponsor.id,
        lifecycle=payload.lifecycle,
        assignments=payload.assignments,
        using=using,
    )
    before_lifecycle = sponsor.lifecycle
    before_revision = sponsor.revision
    sponsor.name = payload.name
    sponsor.url = payload.url
    sponsor.tagline = payload.tagline
    sponsor.lifecycle = payload.lifecycle
    sponsor.source = source
    sponsor.revision += 1
    try:
        sponsor.save(
            using=using,
            update_fields=(
                "name",
                "url",
                "tagline",
                "lifecycle",
                "source",
                "revision",
                "updated_at",
            ),
        )
        _replace_assignments(sponsor, payload.assignments, using=using)
    except RevisionConflict as error:
        raise SponsorRevisionConflict(
            sponsor_id=sponsor.id,
            expected=error.expected,
            actual=error.actual,
        ) from error
    except IntegrityError as error:
        raise InvalidSponsor(
            "sponsor assignment conflicts",
            fields={"assignments": "Choose a unique position in this placement."},
        ) from error
    sponsor = _authorized_queryset(using=using).get(pk=sponsor.pk)
    _assert_stored_active_cap(sponsor, using=using)
    audit_event = _record_mutation(
        action="core.sponsor.updated",
        sponsor=sponsor,
        before_lifecycle=before_lifecycle,
        before_revision=before_revision,
        context=context,
        using=using,
    )
    _write_revision(sponsor, audit_event=audit_event, context=context, using=using)
    return serialize_sponsor(sponsor)


def _apply_lifecycle(
    sponsor_id: uuid.UUID,
    *,
    target: str,
    expected_revision: int,
    source: str,
    action: str,
    context: AuditWriteContext,
    using: str,
) -> JsonObject:
    sponsor = _load_for_update(sponsor_id, using=using)
    if sponsor.revision != expected_revision:
        raise SponsorRevisionConflict(
            sponsor_id=sponsor.id,
            expected=expected_revision,
            actual=sponsor.revision,
        )
    current = sponsor.lifecycle
    if target == Sponsor.Lifecycle.ARCHIVED and current not in {
        Sponsor.Lifecycle.DRAFT,
        Sponsor.Lifecycle.ACTIVE,
    }:
        raise InvalidSponsor(
            "sponsor cannot be archived",
            fields={"lifecycle": "Only draft or active sponsors can be archived."},
        )
    if target == Sponsor.Lifecycle.ACTIVE and current != Sponsor.Lifecycle.ARCHIVED:
        raise InvalidSponsor(
            "sponsor cannot be reactivated",
            fields={"lifecycle": "Only archived sponsors can be reactivated."},
        )
    assignments = tuple(
        NormalizedAssignment(
            placement_key=item.placement_key,
            position=item.position,
            enabled=item.enabled,
        )
        for item in sponsor.assignments.all()
    )
    _assert_assignment_constraints(
        sponsor_id=sponsor.id,
        lifecycle=target,
        assignments=assignments,
        using=using,
    )
    before_lifecycle = sponsor.lifecycle
    before_revision = sponsor.revision
    sponsor.lifecycle = target
    sponsor.source = source
    sponsor.revision += 1
    try:
        sponsor.save(
            using=using,
            update_fields=("lifecycle", "source", "revision", "updated_at"),
        )
    except RevisionConflict as error:
        raise SponsorRevisionConflict(
            sponsor_id=sponsor.id,
            expected=error.expected,
            actual=error.actual,
        ) from error
    sponsor = _authorized_queryset(using=using).get(pk=sponsor.pk)
    _assert_stored_active_cap(sponsor, using=using)
    audit_event = _record_mutation(
        action=action,
        sponsor=sponsor,
        before_lifecycle=before_lifecycle,
        before_revision=before_revision,
        context=context,
        using=using,
    )
    _write_revision(sponsor, audit_event=audit_event, context=context, using=using)
    return serialize_sponsor(sponsor)


def _command_result(result: IdempotencyResult) -> SponsorCommandResult:
    payload = result.value
    sponsor = payload.get("sponsor") if isinstance(payload, dict) else None
    if not isinstance(sponsor, dict):
        raise InvalidSponsor("sponsor replay result is invalid")
    return SponsorCommandResult(
        sponsor=_validate_stored_sponsor(sponsor),
        replayed=result.replayed,
    )


def _execute_command(
    *,
    capability_key: str,
    actor_ref: str,
    actor_id: Any | None,
    api_principal_id: uuid.UUID | None,
    context: ServiceContext | None,
    idempotency_key: str,
    request: JsonObject,
    command,
    using: str,
) -> SponsorCommandResult:
    actor = _require_actor_ref(actor_ref, context)
    service_context = context or ServiceContext.from_current(actor_ref=actor)
    scope = _idempotency_scope(capability_key, actor)
    audit_context = AuditWriteContext.from_service_context(
        service_context,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        idempotency_key_hash=hash_idempotency_key(scope, idempotency_key),
    )
    result = execute_idempotent(
        scope=scope,
        key=idempotency_key,
        request=request,
        command=lambda: {"sponsor": command(audit_context)},
        using=using,
    )
    return _command_result(result)


def create_sponsor(
    *,
    payload: object,
    source: str,
    idempotency_key: str,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> SponsorCommandResult:
    """Create one sponsor after complete-payload validation."""

    source_value = _require_source(source)
    normalized = _normalize_payload(
        payload,
        require_key=True,
        allowed_lifecycles=frozenset({Sponsor.Lifecycle.DRAFT, Sponsor.Lifecycle.ACTIVE}),
    )
    request: JsonObject = {
        "key": normalized.key,
        "name": normalized.name,
        "url": normalized.url,
        "tagline": normalized.tagline,
        "lifecycle": normalized.lifecycle,
        "assignments": [item.as_dict() for item in normalized.assignments],
        "source": source_value,
    }
    return _execute_command(
        capability_key="site.sponsors.write",
        actor_ref=actor_ref,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        context=context,
        idempotency_key=idempotency_key,
        request=request,
        command=lambda audit_context: _apply_create(
            normalized,
            source=source_value,
            context=audit_context,
            using=using,
        ),
        using=using,
    )


def update_sponsor(
    *,
    sponsor_id: object,
    payload: object,
    expected_revision: object,
    source: str,
    idempotency_key: str,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> SponsorCommandResult:
    """Revision-guard an existing sponsor after complete-payload validation."""

    expected = _require_expected_revision(expected_revision)
    identifier = _require_sponsor_id(sponsor_id)
    source_value = _require_source(source)
    normalized = _normalize_payload(
        payload,
        require_key=False,
        allowed_lifecycles=frozenset({Sponsor.Lifecycle.DRAFT, Sponsor.Lifecycle.ACTIVE}),
    )
    request: JsonObject = {
        "id": str(identifier),
        "name": normalized.name,
        "url": normalized.url,
        "tagline": normalized.tagline,
        "lifecycle": normalized.lifecycle,
        "assignments": [item.as_dict() for item in normalized.assignments],
        "expected_revision": expected,
        "source": source_value,
    }
    return _execute_command(
        capability_key="site.sponsors.update",
        actor_ref=actor_ref,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        context=context,
        idempotency_key=idempotency_key,
        request=request,
        command=lambda audit_context: _apply_update(
            identifier,
            normalized,
            expected_revision=expected,
            source=source_value,
            context=audit_context,
            using=using,
        ),
        using=using,
    )


def _require_expected_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidSponsor(
            "expected revision is invalid",
            fields={"expected_revision": "Enter the current revision."},
        )
    return value


def _require_sponsor_id(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError as error:
            raise InvalidSponsor("sponsor id is invalid") from error
    raise InvalidSponsor("sponsor id is invalid")


def archive_sponsor(
    *,
    sponsor_id: object,
    confirmed: object,
    expected_revision: object,
    source: str,
    idempotency_key: str,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> SponsorCommandResult:
    """Archive one sponsor after explicit confirmation."""

    _require_confirmation(confirmed)
    expected = _require_expected_revision(expected_revision)
    identifier = _require_sponsor_id(sponsor_id)
    source_value = _require_source(source)
    request: JsonObject = {
        "id": str(identifier),
        "confirmed": True,
        "expected_revision": expected,
        "source": source_value,
    }
    return _execute_command(
        capability_key="site.sponsors.archive",
        actor_ref=actor_ref,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        context=context,
        idempotency_key=idempotency_key,
        request=request,
        command=lambda audit_context: _apply_lifecycle(
            identifier,
            target=Sponsor.Lifecycle.ARCHIVED,
            expected_revision=expected,
            source=source_value,
            action="core.sponsor.archived",
            context=audit_context,
            using=using,
        ),
        using=using,
    )


def reactivate_sponsor(
    *,
    sponsor_id: object,
    confirmed: object,
    expected_revision: object,
    source: str,
    idempotency_key: str,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> SponsorCommandResult:
    """Reactivate one archived sponsor after explicit confirmation."""

    _require_confirmation(confirmed)
    expected = _require_expected_revision(expected_revision)
    identifier = _require_sponsor_id(sponsor_id)
    source_value = _require_source(source)
    request: JsonObject = {
        "id": str(identifier),
        "confirmed": True,
        "expected_revision": expected,
        "source": source_value,
    }
    return _execute_command(
        capability_key="site.sponsors.reactivate",
        actor_ref=actor_ref,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        context=context,
        idempotency_key=idempotency_key,
        request=request,
        command=lambda audit_context: _apply_lifecycle(
            identifier,
            target=Sponsor.Lifecycle.ACTIVE,
            expected_revision=expected,
            source=source_value,
            action="core.sponsor.reactivated",
            context=audit_context,
            using=using,
        ),
        using=using,
    )


def _export_rows(filters: dict[str, str], *, using: str) -> list[dict[str, str]]:
    queryset = _authorized_queryset(using=using).order_by("key")
    if "lifecycle" in filters:
        queryset = queryset.filter(lifecycle=filters["lifecycle"])
    if "placement" in filters:
        queryset = queryset.filter(assignments__placement_key=filters["placement"]).distinct()
    rows: list[dict[str, str]] = []
    for sponsor in queryset[: SPONSOR_EXPORT_MAX_ROWS + 1]:
        stored = _validate_stored_sponsor(serialize_sponsor(sponsor))
        assignments = _assignment_dicts(stored["assignments"])
        if not assignments:
            rows.append(
                {
                    "key": str(stored["key"]),
                    "name": str(stored["name"]),
                    "url": str(stored["url"]),
                    "placement": "",
                    "position": "",
                    "lifecycle": str(stored["lifecycle"]),
                    "revision": str(stored["revision"]),
                    "created_at": str(stored["created_at"]),
                    "updated_at": str(stored["updated_at"]),
                }
            )
            continue
        for assignment in assignments:
            rows.append(
                {
                    "key": str(stored["key"]),
                    "name": str(stored["name"]),
                    "url": str(stored["url"]),
                    "placement": str(assignment["placement"]),
                    "position": str(assignment["position"]),
                    "lifecycle": str(stored["lifecycle"]),
                    "revision": str(stored["revision"]),
                    "created_at": str(stored["created_at"]),
                    "updated_at": str(stored["updated_at"]),
                }
            )
    if len(rows) > SPONSOR_EXPORT_MAX_ROWS:
        raise InvalidSponsor("sponsor export exceeds the bounded row limit")
    return rows


def _render_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(SPONSOR_EXPORT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {column: neutralize_csv_formula(row[column]) for column in SPONSOR_EXPORT_COLUMNS}
        )
    return buffer.getvalue()


def export_sponsor_directory(
    *,
    confirmed: object,
    reason: object,
    filters: object = None,
    idempotency_key: str,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> SponsorExportResult:
    """Export the public-safe sponsor directory as a formula-safe CSV."""

    _require_confirmation(confirmed)
    normalized_reason = _normalize_reason(reason)
    _page, _page_size, _sort, parsed_filters = _normalize_page(
        1,
        SPONSOR_DEFAULT_PAGE_SIZE,
        (),
        filters or {},
    )
    unexpected = set(parsed_filters) - {"lifecycle", "placement"}
    if unexpected:
        raise InvalidSponsor("export filters are invalid")
    actor = _require_actor_ref(actor_ref, context)
    service_context = context or ServiceContext.from_current(actor_ref=actor)
    scope = _idempotency_scope("site.sponsors.export", actor)
    audit_context = AuditWriteContext.from_service_context(
        service_context,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        idempotency_key_hash=hash_idempotency_key(scope, idempotency_key),
    )
    request: JsonObject = {
        "confirmed": True,
        "reason": normalized_reason,
        "filters": dict(parsed_filters),
    }

    def command() -> JsonObject:
        rows = _export_rows(parsed_filters, using=using)
        rendered = _render_csv(rows)
        record_audit_event(
            action="core.sponsor_directory.exported",
            target_type="core.sponsor_directory",
            target_label="sponsor-directory",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=audit_context,
            changes={},
            metadata={
                "reason_length": len(normalized_reason),
                "filter_scope": parsed_filters,
                "count": len(rows),
            },
            using=using,
        )
        return {
            "filename": "sponsor-directory.csv",
            "row_count": len(rows),
            "csv": rendered,
        }

    result = execute_idempotent(
        scope=scope,
        key=idempotency_key,
        request=request,
        command=command,
        using=using,
    )
    payload = result.value
    filename = payload.get("filename")
    row_count = payload.get("row_count")
    csv_text = payload.get("csv")
    if (
        filename != "sponsor-directory.csv"
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count < 0
        or not isinstance(csv_text, str)
    ):
        raise InvalidSponsor("sponsor export replay result is invalid")
    return SponsorExportResult(
        filename=filename,
        row_count=row_count,
        csv=csv_text,
        replayed=result.replayed,
    )


def _list_factory() -> JsonObject:
    return {"items": [], "page": 1, "page_size": SPONSOR_DEFAULT_PAGE_SIZE, "total_count": 0}


def _sponsor_factory() -> JsonObject:
    return {
        "id": "00000000-0000-4000-8000-000000000001",
        "key": "example",
        "name": "Example",
        "url": "",
        "tagline": "",
        "lifecycle": "draft",
        "source": "studio",
        "revision": 1,
        "assignments": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _export_factory() -> JsonObject:
    return {
        "filename": "sponsor-directory.csv",
        "row_count": 0,
        "csv": ",".join(SPONSOR_EXPORT_COLUMNS) + "\n",
        "replayed": False,
    }


def _sponsor_field_policy(_actor: object, field: str) -> bool:
    return field in {
        "key",
        "name",
        "url",
        "tagline",
        "lifecycle",
        "assignments",
        "expected_revision",
        "confirmed",
        "reason",
        "placement",
        "filters",
    }


def _sponsor_object_policy(_actor: object, _sponsor: object) -> bool:
    return True


def _sponsor_object_scope(_actor: object, queryset: object) -> object:
    return queryset


def _capability(
    *,
    key: str,
    description: str,
    service,
    permission: str,
    studio_route: str,
    studio_method: str,
    api_route: str,
    api_method: str,
    request_schema: str | None,
    result_schema: str,
    writable_fields: tuple[str, ...] = (),
    filter_fields: tuple[str, ...] = (),
    sort_fields: tuple[str, ...] = (),
    paginated: bool = False,
    success_status: int = 200,
    idempotency: IdempotencyPolicy,
    concurrency: ConcurrencyPolicy,
    audit_action: str,
    redacted_fields: tuple[str, ...],
    test_factory,
    object_scoped: bool = False,
) -> Capability:
    command = studio_method not in {"GET", "HEAD"} or api_method not in {"GET", "HEAD"}

    def field_policy(actor: object, field: str) -> bool:
        del actor
        return field in writable_fields

    return Capability(
        key=key,
        description=description,
        service_kind=ServiceKind.COMMAND if command else ServiceKind.QUERY,
        service=service,
        django_permission=permission,
        studio=AdapterMetadata(
            route=studio_route,
            method=studio_method,
            operation_id=f"{key}.html",
            writable_fields=writable_fields,
            filter_fields=filter_fields,
            sort_fields=sort_fields,
            paginated=paginated,
        ),
        admin_api=AdapterMetadata(
            route=api_route,
            method=api_method,
            operation_id=key,
            scopes=(key,),
            request_schema=request_schema,
            result_schema=result_schema,
            writable_fields=writable_fields,
            filter_fields=filter_fields,
            sort_fields=sort_fields,
            paginated=paginated,
            rate_class="write" if command else "read",
            rate_cost=1,
            success_status=success_status,
        ),
        idempotency=idempotency,
        concurrency=concurrency,
        audit_action=audit_action,
        redacted_fields=redacted_fields,
        test_factory=test_factory,
        field_policy=field_policy if writable_fields else None,
        object_policy=_sponsor_object_policy if object_scoped else None,
        object_scope=_sponsor_object_scope if object_scoped else None,
    )


SPONSOR_LIST = _capability(
    key="site.sponsors.read",
    description="List the database-owned sponsor directory",
    service=list_sponsors,
    permission=SPONSOR_READ_PERMISSION,
    studio_route="studio:sponsor-list",
    studio_method="GET",
    api_route="/api/v1/admin/sponsors",
    api_method="GET",
    request_schema=None,
    result_schema="SponsorList",
    filter_fields=SPONSOR_FILTER_FIELDS,
    sort_fields=SPONSOR_SORT_FIELDS,
    paginated=True,
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="core.sponsor.listed",
    redacted_fields=("authorization", "cookie", "token"),
    test_factory=_list_factory,
)

SPONSOR_CREATE = _capability(
    key="site.sponsors.write",
    description="Create a database-owned sponsor",
    service=create_sponsor,
    permission=SPONSOR_WRITE_PERMISSION,
    studio_route="studio:sponsor-list",
    studio_method="POST",
    api_route="/api/v1/admin/sponsors",
    api_method="POST",
    request_schema="SponsorCreateRequest",
    result_schema="SponsorCommandResult",
    writable_fields=("key", "name", "url", "tagline", "lifecycle", "assignments"),
    success_status=201,
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.REVISION,
    audit_action="core.sponsor.created",
    redacted_fields=_WRITE_REDACTED,
    test_factory=_sponsor_factory,
)

SPONSOR_DETAIL = _capability(
    key="site.sponsors.detail",
    description="Inspect one database-owned sponsor",
    service=get_sponsor,
    permission=SPONSOR_READ_PERMISSION,
    studio_route="studio:sponsor-detail",
    studio_method="GET",
    api_route="/api/v1/admin/sponsors/{sponsor_id}",
    api_method="GET",
    request_schema=None,
    result_schema="Sponsor",
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="core.sponsor.viewed",
    redacted_fields=("authorization", "cookie", "token"),
    test_factory=_sponsor_factory,
    object_scoped=True,
)

SPONSOR_UPDATE = _capability(
    key="site.sponsors.update",
    description="Update a database-owned sponsor",
    service=update_sponsor,
    permission=SPONSOR_WRITE_PERMISSION,
    studio_route="studio:sponsor-detail",
    studio_method="POST",
    api_route="/api/v1/admin/sponsors/{sponsor_id}",
    api_method="PATCH",
    request_schema="SponsorUpdateRequest",
    result_schema="SponsorCommandResult",
    writable_fields=("name", "url", "tagline", "lifecycle", "assignments", "expected_revision"),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.IF_MATCH,
    audit_action="core.sponsor.updated",
    redacted_fields=_WRITE_REDACTED,
    test_factory=_sponsor_factory,
    object_scoped=True,
)

SPONSOR_ARCHIVE = _capability(
    key="site.sponsors.archive",
    description="Archive a database-owned sponsor",
    service=archive_sponsor,
    permission=SPONSOR_WRITE_PERMISSION,
    studio_route="studio:sponsor-archive",
    studio_method="POST",
    api_route="/api/v1/admin/sponsors/{sponsor_id}/archive",
    api_method="POST",
    request_schema="SponsorActionRequest",
    result_schema="SponsorCommandResult",
    writable_fields=("confirmed", "expected_revision"),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.REVISION,
    audit_action="core.sponsor.archived",
    redacted_fields=_WRITE_REDACTED,
    test_factory=_sponsor_factory,
    object_scoped=True,
)

SPONSOR_REACTIVATE = _capability(
    key="site.sponsors.reactivate",
    description="Reactivate a database-owned sponsor",
    service=reactivate_sponsor,
    permission=SPONSOR_WRITE_PERMISSION,
    studio_route="studio:sponsor-reactivate",
    studio_method="POST",
    api_route="/api/v1/admin/sponsors/{sponsor_id}/reactivate",
    api_method="POST",
    request_schema="SponsorActionRequest",
    result_schema="SponsorCommandResult",
    writable_fields=("confirmed", "expected_revision"),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.REVISION,
    audit_action="core.sponsor.reactivated",
    redacted_fields=_WRITE_REDACTED,
    test_factory=_sponsor_factory,
    object_scoped=True,
)

SPONSOR_EXPORT = _capability(
    key="site.sponsors.export",
    description="Export the public-safe sponsor directory",
    service=export_sponsor_directory,
    permission=SPONSOR_EXPORT_PERMISSION,
    studio_route="studio:sponsor-export",
    studio_method="POST",
    api_route="/api/v1/admin/sponsor-directory-exports",
    api_method="POST",
    request_schema="SponsorDirectoryExportRequest",
    result_schema="SponsorDirectoryExport",
    writable_fields=("confirmed", "reason", "filters", "lifecycle", "placement"),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="core.sponsor_directory.exported",
    redacted_fields=_EXPORT_REDACTED,
    test_factory=_export_factory,
)

SPONSOR_CAPABILITIES = (
    SPONSOR_LIST,
    SPONSOR_CREATE,
    SPONSOR_DETAIL,
    SPONSOR_UPDATE,
    SPONSOR_ARCHIVE,
    SPONSOR_REACTIVATE,
    SPONSOR_EXPORT,
)
