"""Bounded audit browsing and export services for management adapters."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import unicodedata
import uuid
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import QuerySet
from django.http import QueryDict

from core.audit import AuditWriteContext, record_audit_event
from core.context import is_safe_external_context_id
from core.idempotency import JsonObject, execute_idempotent, hash_idempotency_key
from core.models import AuditEvent
from core.redaction import is_sensitive_text, redact
from core.security import neutralize_csv_formula
from core.services import ServiceContext, validate_actor_ref

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
AUDIT_EXPORT_MAX_ROWS = 1_000
AUDIT_EXPORT_REASON_MAX_LENGTH = 200
AUDIT_EXPORT_MAX_BYTES = 32 * 1024
_AUDIT_EXPORT_STORED_BYTES = 48 * 1024


class AuditQueryError(ValueError):
    pass


class AuditExportError(ValueError):
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


@dataclass(frozen=True, slots=True)
class AuditExportResult:
    filename: str
    row_count: int
    csv: str
    replayed: bool


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


def _normalize_export_filters(filters: object) -> dict[str, str]:
    if filters is None:
        return {}
    if not isinstance(filters, Mapping) or any(not isinstance(key, str) for key in filters):
        raise AuditExportError("audit export filters are invalid")
    unexpected = set(filters).difference(AUDIT_FILTERS)
    if unexpected:
        raise AuditExportError("audit export filters are invalid")
    normalized: dict[str, str] = {}
    for key in sorted(AUDIT_FILTERS & set(filters)):
        value = filters[key]
        if not isinstance(value, str) or not value:
            raise AuditExportError("audit export filters are invalid")
        if key in {"action", "target_type"} and _IDENTIFIER.fullmatch(value) is None:
            raise AuditExportError("audit export filters are invalid")
        if key == "outcome" and value not in AuditEvent.Outcome.values:
            raise AuditExportError("audit export filters are invalid")
        if key in {"request_id", "correlation_id"} and not is_safe_external_context_id(value):
            raise AuditExportError("audit export filters are invalid")
        normalized[key] = value
    return normalized


def _normalize_export_reason(reason: object) -> str:
    if not isinstance(reason, str):
        raise AuditExportError("audit export reason must be a string")
    if any(
        character in "\r\n\t"
        or unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in reason
    ):
        raise AuditExportError("audit export reason must be one safe line")
    if "<" in reason or ">" in reason:
        raise AuditExportError("audit export reason cannot contain markup")
    normalized = reason.strip()
    if (
        not normalized
        or len(normalized) > AUDIT_EXPORT_REASON_MAX_LENGTH
        or is_sensitive_text(normalized)
    ):
        raise AuditExportError("audit export reason is invalid")
    return normalized


def _render_audit_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(AUDIT_DISPLAY_FIELDS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {field: neutralize_csv_formula(row[field]) for field in AUDIT_DISPLAY_FIELDS}
        )
    rendered = buffer.getvalue()
    if len(rendered.encode()) > AUDIT_EXPORT_MAX_BYTES:
        raise AuditExportError("audit export exceeds its bounded output size")
    return rendered


def _store_audit_csv(rendered: str) -> tuple[str, str]:
    raw = rendered.encode()
    compressed = base64.b64encode(zlib.compress(raw, level=9))
    if len(compressed) > _AUDIT_EXPORT_STORED_BYTES:
        raise AuditExportError("audit export exceeds its durable replay bound")
    return compressed.decode("ascii"), hashlib.sha256(raw).hexdigest()


def _load_audit_csv(stored_csv: object, expected_digest: object) -> str:
    if (
        not isinstance(stored_csv, str)
        or not isinstance(expected_digest, str)
        or len(expected_digest) != 64
    ):
        raise AuditExportError("audit export replay payload is invalid")
    try:
        rendered_bytes = zlib.decompress(base64.b64decode(stored_csv, validate=True))
    except (ValueError, OSError, zlib.error):
        raise AuditExportError("audit export replay payload is invalid") from None
    if len(rendered_bytes) > AUDIT_EXPORT_MAX_BYTES:
        raise AuditExportError("audit export replay payload exceeds its bound")
    digest = hashlib.sha256(rendered_bytes).hexdigest()
    if digest != expected_digest:
        raise AuditExportError("audit export replay integrity check failed")
    return rendered_bytes.decode()


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


def export_audit_events(
    *,
    confirmed: object,
    reason: object,
    filters: object = None,
    idempotency_key: str,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
) -> AuditExportResult:
    """Export a bounded, re-redacted audit snapshot as a formula-safe CSV."""

    if confirmed is not True:
        raise AuditExportError("confirmation is required")
    normalized_reason = _normalize_export_reason(reason)
    parsed_filters = _normalize_export_filters(filters)
    try:
        validate_actor_ref(actor_ref)
    except ValueError as error:
        raise AuditExportError("audit export actor is invalid") from error
    if context is not None and context.actor_ref != actor_ref:
        raise AuditExportError("audit export actor context is invalid")
    service_context = context or ServiceContext.from_current(actor_ref=actor_ref)
    scope = f"studio.audit.export:{actor_ref}"
    if len(scope) > 128:
        raise AuditExportError("audit export actor scope is invalid")
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
        queryset = (
            audit_object_scope(None, AuditEvent.objects.all())
            .filter(**parsed_filters)
            .order_by("-created_at", "-id")
        )
        rows = [present_audit_event(event) for event in queryset[: AUDIT_EXPORT_MAX_ROWS + 1]]
        if len(rows) > AUDIT_EXPORT_MAX_ROWS:
            raise AuditExportError("audit export exceeds the bounded row limit")
        rendered = _render_audit_csv(rows)
        stored_csv, csv_sha256 = _store_audit_csv(rendered)
        record_audit_event(
            action="core.audit.exported",
            target_type="core.audit_export",
            target_label="audit-export",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=audit_context,
            changes={},
            metadata={
                "count": len(rows),
                "filter_scope": parsed_filters,
                "reason_length": len(normalized_reason),
            },
        )
        return {
            "filename": "audit-events.csv",
            "row_count": len(rows),
            "csv_gzip_base64": stored_csv,
            "csv_sha256": csv_sha256,
        }

    result = execute_idempotent(
        scope=scope,
        key=idempotency_key,
        request=request,
        command=command,
    )
    payload = result.value
    filename = payload.get("filename")
    row_count = payload.get("row_count")
    stored_csv = payload.get("csv_gzip_base64")
    csv_sha256 = payload.get("csv_sha256")
    if (
        filename != "audit-events.csv"
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count < 0
        or not isinstance(stored_csv, str)
        or not isinstance(csv_sha256, str)
    ):
        raise AuditExportError("audit export replay result is invalid")
    return AuditExportResult(
        filename=str(filename),
        row_count=row_count,
        csv=_load_audit_csv(stored_csv, csv_sha256),
        replayed=result.replayed,
    )


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
