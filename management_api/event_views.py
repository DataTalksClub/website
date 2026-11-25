"""Authenticated JSON admin API views for event operations."""

from __future__ import annotations

import uuid
from typing import Any

from community_base.kernel.services import ServiceContext
from django.http import JsonResponse

from events.admin_services import EventAdminError
from management_api.dispatch import admin_capability
from management_api.errors import APIError
from management_api.json_input import parse_json_object
from management_registry import CAPABILITY_REGISTRY


def _invoke(
    request: Any,
    key: str,
    payload: dict[str, Any],
    *,
    event_id: uuid.UUID | None = None,
    status: int = 200,
) -> JsonResponse:
    capability = CAPABILITY_REGISTRY.require(key)
    identity = request.api_identity
    context = ServiceContext.from_current(
        actor_ref=f"api_principal:{identity.principal.pk}",
        idempotency_key=request.headers.get("Idempotency-Key") or None,
    )
    try:
        if event_id is not None:
            result = capability.service(payload, event_id=event_id, context=context)
        else:
            result = capability.service(payload, context=context)
    except EventAdminError as exc:
        raise APIError(exc.status, exc.code, str(exc)) from exc
    return JsonResponse(result, status=status)


@admin_capability("events.manage.list")
def event_list(request: Any) -> JsonResponse:
    return _invoke(request, "events.manage.list", request.GET.dict())


@admin_capability("events.manage.create")
def event_create(request: Any) -> JsonResponse:
    return _invoke(request, "events.manage.create", parse_json_object(request), status=201)


@admin_capability("events.manage.detail")
def event_detail(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _invoke(request, "events.manage.detail", {}, event_id=event_id)


@admin_capability("events.manage.update")
def event_update(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _invoke(request, "events.manage.update", parse_json_object(request), event_id=event_id)


@admin_capability("events.manage.publish")
def event_publish(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _invoke(request, "events.manage.publish", {}, event_id=event_id)


@admin_capability("events.manage.cancel")
def event_cancel(request: Any, event_id: uuid.UUID) -> JsonResponse:
    payload = parse_json_object(request) if request.body else {}
    return _invoke(request, "events.manage.cancel", payload, event_id=event_id)


@admin_capability("events.manage.complete")
def event_complete(request: Any, event_id: uuid.UUID) -> JsonResponse:
    payload = parse_json_object(request) if request.body else {}
    return _invoke(request, "events.manage.complete", payload, event_id=event_id)


BANNER_MAX_JSON_BYTES = 7 * 1024 * 1024


@admin_capability("events.manage.banner")
def event_banner(request: Any, event_id: uuid.UUID) -> JsonResponse:
    payload = parse_json_object(request, max_bytes=BANNER_MAX_JSON_BYTES)
    if request.headers.get("X-Banner-Upload-Chunk") == "1":
        payload = {**payload, "_is_continuation_chunk": True}
    return _invoke(request, "events.manage.banner", payload, event_id=event_id)


@admin_capability("events.manage.registrants.export")
def event_registrants(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _invoke(
        request, "events.manage.registrants.export", request.GET.dict(), event_id=event_id
    )


@admin_capability("events.manage.attendance.import")
def event_attendance(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _invoke(
        request, "events.manage.attendance.import", parse_json_object(request), event_id=event_id
    )


@admin_capability("events.manage.message")
def event_message(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _invoke(request, "events.manage.message", parse_json_object(request), event_id=event_id)


@admin_capability("events.manage.reminders")
def event_reminders(request: Any, event_id: uuid.UUID) -> JsonResponse:
    payload = parse_json_object(request) if request.body else {}
    return _invoke(request, "events.manage.reminders", payload, event_id=event_id)


@admin_capability("events.manage.speakers.list")
def speaker_list(request: Any) -> JsonResponse:
    return _invoke(request, "events.manage.speakers.list", request.GET.dict())


@admin_capability("events.manage.speakers.create")
def speaker_create(request: Any) -> JsonResponse:
    return _invoke(request, "events.manage.speakers.create", parse_json_object(request), status=201)
