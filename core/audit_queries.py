"""Bounded, side-effect-free audit browsing for management adapters."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import QuerySet
from django.http import QueryDict

from core.context import is_safe_external_context_id
from core.models import AuditEvent
from core.redaction import redact
from core.services import ServiceContext

AUDIT_FILTERS = frozenset({"action", "outcome", "target_type", "request_id", "correlation_id"})
AUDIT_DISPLAY_FIELDS = (
    "created_at",
    "action",
    "outcome",
    "target_type",
    "target_id",
    "target_label",
    "actor_snapshot",
    "request_id",
    "correlation_id",
    "changes",
    "metadata",
)
AUDIT_PAGE_SIZE = 20
AUDIT_MAX_PAGE_SIZE = 50
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class AuditQueryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AuditListQuery:
    filters: dict[str, str]
    page: int = 1
    page_size: int = AUDIT_PAGE_SIZE


@dataclass(frozen=True, slots=True)
class AuditPage:
    events: tuple[AuditEvent, ...]
    number: int
    total_pages: int
    total_count: int
    has_previous: bool
    has_next: bool


def parse_audit_list_query(query: QueryDict) -> AuditListQuery:
    allowed = AUDIT_FILTERS | {"page"}
    if set(query).difference(allowed) or any(len(query.getlist(key)) != 1 for key in query):
        raise AuditQueryError("invalid audit filter")
    filters: dict[str, str] = {}
    for key in AUDIT_FILTERS:
        if key not in query:
            continue
        value = query.get(key, "")
        if not isinstance(value, str):
            raise AuditQueryError("invalid audit filter")
        if not value:
            continue
        if key in {"action", "target_type"} and _IDENTIFIER.fullmatch(value) is None:
            raise AuditQueryError("invalid audit filter")
        if key == "outcome" and value not in AuditEvent.Outcome.values:
            raise AuditQueryError("invalid audit filter")
        if key in {"request_id", "correlation_id"} and not is_safe_external_context_id(value):
            raise AuditQueryError("invalid audit filter")
        filters[key] = value
    raw_page = query.get("page", "1")
    if not isinstance(raw_page, str):
        raise AuditQueryError("invalid audit page")
    if not raw_page.isascii() or not raw_page.isdecimal() or raw_page.startswith("0"):
        raise AuditQueryError("invalid audit page")
    page = int(raw_page)
    if page < 1 or page > 100_000:
        raise AuditQueryError("invalid audit page")
    return AuditListQuery(filters=filters, page=page)


def audit_object_scope(actor: Any, queryset: QuerySet[AuditEvent]) -> QuerySet[AuditEvent]:
    del actor
    return queryset.exclude(target_type__startswith="private.")


def audit_object_policy(actor: Any, event: AuditEvent) -> bool:
    del actor
    return not event.target_type.startswith("private.")


def audit_field_policy(actor: Any, field: str) -> bool:
    del actor
    return field in AUDIT_DISPLAY_FIELDS


def browse_audit_events(
    query: AuditListQuery,
    *,
    context: ServiceContext,
    actor: Any,
) -> AuditPage:
    del context
    page_size = min(max(query.page_size, 1), AUDIT_MAX_PAGE_SIZE)
    queryset = audit_object_scope(actor, AuditEvent.objects.all())
    queryset = queryset.filter(**query.filters).order_by("-created_at", "-id")
    paginator = Paginator(queryset, page_size)
    try:
        page = paginator.page(query.page)
    except EmptyPage:
        raise AuditQueryError("invalid audit page") from None
    return AuditPage(
        events=tuple(page.object_list),
        number=page.number,
        total_pages=paginator.num_pages,
        total_count=paginator.count,
        has_previous=page.has_previous(),
        has_next=page.has_next(),
    )


def get_audit_event(
    event_id: uuid.UUID,
    *,
    context: ServiceContext,
    actor: Any,
) -> AuditEvent | None:
    del context
    # Scope is applied in SQL before UUID lookup so excluded and absent objects share one path.
    return audit_object_scope(actor, AuditEvent.objects.all()).filter(id=event_id).first()


def _present_value(value: object) -> object:
    canaries = getattr(settings, "STUDIO_AUDIT_REDACTION_CANARIES", ())
    return redact(value, canaries=canaries)


def present_audit_event(event: AuditEvent) -> dict[str, str]:
    """Allowlist and re-redact storage, including legacy/directly inserted values."""

    raw: dict[str, object] = {
        "created_at": event.created_at.isoformat(),
        "action": event.action,
        "outcome": event.outcome,
        "target_type": event.target_type,
        "target_id": str(event.target_id) if event.target_id else "",
        "target_label": event.target_label,
        "actor_snapshot": event.actor_ref,
        "request_id": event.request_id,
        "correlation_id": event.correlation_id,
        "changes": event.changes,
        "metadata": event.metadata,
    }
    presented: dict[str, str] = {}
    for field in AUDIT_DISPLAY_FIELDS:
        redacted = _present_value(raw[field])
        if isinstance(redacted, (dict, list, tuple)):
            presented[field] = json.dumps(redacted, sort_keys=True, separators=(",", ":"))
        elif redacted is None:
            presented[field] = ""
        else:
            presented[field] = str(redacted)
    return presented
