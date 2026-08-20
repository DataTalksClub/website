"""Studio presentation adapters for Event-linked Q&A."""

from __future__ import annotations

import uuid
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from core.audit import AuditWriteContext
from studio.auth import capability_required

from .errors import QnaError
from .services import (
    EventQnaSession,
    admin_event_qna,
    create_cohost,
    retry_event_qna_provision,
    revoke_cohost,
    update_question,
    update_session,
)


def _audit_context(request: HttpRequest) -> AuditWriteContext:
    user = request.user
    return AuditWriteContext(actor_id=user.pk, actor_ref=f"user:{user.pk}")


def _view_context(
    request: HttpRequest, event_id: uuid.UUID, *, error: str = ""
) -> dict[str, object]:
    qna = admin_event_qna(event_id)
    event = EventQnaSession.objects.select_related("event").get(event_id=event_id).event
    return {
        "event": event,
        "qna": qna,
        "states": EventQnaSession.State.choices,
        "idempotency_key": uuid.uuid4(),
        "error_message": error,
        "studio_navigation": (),
    }


@capability_required("events.qna.read")
def event_qna_detail(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return HttpResponse("Method not allowed", status=405)
    try:
        return render(request, "studio/event_qna.html", _view_context(request, event_id))
    except (QnaError, EventQnaSession.DoesNotExist):
        return HttpResponse("Event Q&A unavailable", status=404)


@capability_required("events.qna.manage")
def event_qna_update(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)
    settings = {
        "listed": request.POST.get("listed") == "on",
        "allow_names": request.POST.get("allow_names") == "on",
        "require_names": request.POST.get("require_names") == "on",
        "answered_placement": request.POST.get("answered_placement", "separate"),
        "default_sort": request.POST.get("default_sort", "popular"),
    }
    payload = {"settings": settings, "state": request.POST.get("state", "draft")}
    try:
        update_session(
            event_id,
            payload,
            actor_role="operator",
            audit_context=_audit_context(request),
        )
    except QnaError as error:
        return render(
            request,
            "studio/event_qna.html",
            _view_context(request, event_id, error=error.message),
            status=error.status,
        )
    return HttpResponseRedirect(f"/studio/events/{event_id}/qna/?saved=1")


@capability_required("events.qna.moderate")
def event_qna_moderate(
    request: HttpRequest,
    event_id: uuid.UUID,
    question_id: str,
) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)
    action = request.POST.get("action", "")
    actions: dict[str, dict[str, Any]] = {
        "answer": {"status": "answered"},
        "delete": {"status": "deleted"},
        "pin": {"pinned": True},
        "unpin": {"pinned": False},
    }
    payload = actions.get(action)
    if payload is None:
        return HttpResponse("Invalid moderation action", status=400)
    try:
        update_question(
            event_id,
            question_id,
            payload,
            moderator=True,
            audit_context=_audit_context(request),
        )
    except QnaError as error:
        return HttpResponse(error.message, status=error.status)
    return HttpResponseRedirect(f"/studio/events/{event_id}/qna/?saved=1")


@capability_required("events.qna.provision.retry")
def event_qna_retry(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)
    try:
        retry_event_qna_provision(event_id, audit_context=_audit_context(request))
    except (QnaError, ValueError) as error:
        return HttpResponse(str(error), status=400)
    return HttpResponseRedirect(f"/studio/events/{event_id}/qna/?retried=1")


@capability_required("events.qna.cohost.create")
def event_qna_cohost(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)
    try:
        invite = create_cohost(
            event_id,
            name=request.POST.get("name"),
            passcode=request.POST.get("passcode"),
            actor_ref=f"user:{request.user.pk}",
            audit_context=_audit_context(request),
        )
    except QnaError as error:
        return HttpResponse(error.message, status=error.status)
    # The passcode is intentionally displayed only in this one-time response;
    # it is never put in a redirect, URL, audit row, or log.
    return render(request, "studio/event_qna_cohost_created.html", {"invite": invite})


@capability_required("events.qna.cohost.revoke")
def event_qna_cohost_revoke(
    request: HttpRequest,
    event_id: uuid.UUID,
    invite_id: str,
) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)
    try:
        revoke_cohost(event_id, invite_id, audit_context=_audit_context(request))
    except QnaError as error:
        return HttpResponse(error.message, status=error.status)
    return HttpResponseRedirect(f"/studio/events/{event_id}/qna/?saved=1")
