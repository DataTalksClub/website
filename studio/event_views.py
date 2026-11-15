"""Studio adapters for event management capabilities."""

from __future__ import annotations

import json
import uuid
from typing import Any

from community_base.kernel.services import ServiceContext
from django.http import JsonResponse

from events.admin_services import EventAdminError
from management_registry import CAPABILITY_REGISTRY
from studio.auth import capability_required


def _payload(request: Any) -> dict[str, Any]:
    if request.content_type == "application/json" and request.body:
        decoded = json.loads(request.body.decode("utf-8"))
        return decoded if isinstance(decoded, dict) else {}
    return {k: v for k, v in request.POST.items() if k != "csrfmiddlewaretoken"}


def _run(
    request: Any,
    key: str,
    payload: dict[str, Any],
    *,
    event_id: uuid.UUID | None = None,
    status: int = 200,
) -> JsonResponse:
    capability = CAPABILITY_REGISTRY.require(key)
    context = ServiceContext.from_current(actor_ref=f"staff:{request.user.pk}")
    try:
        if event_id is not None:
            result = capability.service(payload, event_id=event_id, context=context)
        else:
            result = capability.service(payload, context=context)
    except EventAdminError as exc:
        return JsonResponse({"error": {"code": exc.code, "message": str(exc)}}, status=exc.status)
    return JsonResponse(result, status=status)


@capability_required("events.manage.list")
def event_list(request: Any) -> JsonResponse:
    return _run(request, "events.manage.list", request.GET.dict())


@capability_required("events.manage.create")
def event_create(request: Any) -> JsonResponse:
    return _run(request, "events.manage.create", _payload(request), status=201)


@capability_required("events.manage.detail")
def event_detail(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.detail", {}, event_id=event_id)


@capability_required("events.manage.update")
def event_update(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.update", _payload(request), event_id=event_id)


@capability_required("events.manage.publish")
def event_publish(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.publish", {}, event_id=event_id)


@capability_required("events.manage.cancel")
def event_cancel(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.cancel", _payload(request), event_id=event_id)


@capability_required("events.manage.complete")
def event_complete(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.complete", _payload(request), event_id=event_id)


@capability_required("events.manage.banner")
def event_banner(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.banner", _payload(request), event_id=event_id)


@capability_required("events.manage.registrants.export")
def event_registrants(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.registrants.export", request.GET.dict(), event_id=event_id)


@capability_required("events.manage.attendance.import")
def event_attendance(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.attendance.import", _payload(request), event_id=event_id)


@capability_required("events.manage.message")
def event_message(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.message", _payload(request), event_id=event_id)


@capability_required("events.manage.reminders")
def event_reminders(request: Any, event_id: uuid.UUID) -> JsonResponse:
    return _run(request, "events.manage.reminders", _payload(request), event_id=event_id)


@capability_required("events.manage.speakers.list")
def speaker_list(request: Any) -> JsonResponse:
    return _run(request, "events.manage.speakers.list", request.GET.dict())


@capability_required("events.manage.speakers.create")
def speaker_create(request: Any) -> JsonResponse:
    return _run(request, "events.manage.speakers.create", _payload(request), status=201)
