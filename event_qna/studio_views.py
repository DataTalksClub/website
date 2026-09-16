"""Studio presentation adapters for Event-linked Q&A.

Every mutation here runs through the same guarantees the management API
enforces (audit EVT-02): the session form carries the revision it was
rendered from and is refused when the session has moved; the rendered
idempotency key is actually consumed, so a browser retry replays instead of
re-executing; the form writes only the settings it renders; and the
retry/revoke commands require the same explicit confirmation the API
requires — the template dialog is never the confirmation.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from core.audit import AuditWriteContext
from core.idempotency import JsonValue, execute_idempotent
from core.models import RevisionConflict
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

_CONFIRMED = "true"


def _audit_context(request: HttpRequest) -> AuditWriteContext:
    user = request.user
    return AuditWriteContext(actor_id=user.pk, actor_ref=f"user:{user.pk}")


def _form_idempotency_key(request: HttpRequest) -> str:
    raw = request.POST.get("idempotency_key", "")
    if not raw or "," in raw or len(raw.encode("utf-8")) > 512:
        raise QnaError(400, "invalid_idempotency_key", "A valid idempotency key is required.")
    return raw


def _form_revision(request: HttpRequest) -> int:
    raw = request.POST.get("revision", "")
    try:
        return int(raw)
    except ValueError as exc:
        raise QnaError(
            400, "missing_revision", "Save again: the form did not carry a session revision."
        ) from exc


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


def _error_response(
    request: HttpRequest, event_id: uuid.UUID, error: QnaError | RevisionConflict
) -> HttpResponse:
    if isinstance(error, RevisionConflict):
        message = "This form was stale: the session changed before your save was applied."
        status = 409
    else:
        message = error.message
        status = error.status
    return render(
        request,
        "studio/event_qna.html",
        _view_context(request, event_id, error=message),
        status=status,
    )


@capability_required("events.qna.read")
def event_qna_detail(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return HttpResponse("Method not allowed", status=405)
    try:
        return render(request, "studio/event_qna.html", _view_context(request, event_id))
    except (QnaError, EventQnaSession.DoesNotExist):
        return HttpResponse("Event Q&A unavailable", status=404)


def _session_update_result(
    event_id: uuid.UUID,
    payload: dict[str, Any],
    expected_revision: int,
    request: HttpRequest,
) -> dict[str, Any]:
    session = update_session(
        event_id,
        payload,
        actor_role="operator",
        expected_revision=expected_revision,
        audit_context=_audit_context(request),
    )
    return {"revision": session.revision, "state": session.state}


@capability_required("events.qna.manage")
def event_qna_update(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)
    try:
        expected_revision = _form_revision(request)
        key = _form_idempotency_key(request)
        # Sparse settings: only the fields this form renders.  ``listed`` and
        # ``answered_placement`` are not on this form, so writing defaults for
        # them would silently reset values another operator set through the
        # API; update_session merges the unrendered fields over the session's
        # current values.  Rendered checkboxes still mean false when
        # unchecked -- that is the form's own deliberate value.
        settings: dict[str, JsonValue] = {
            "allow_names": request.POST.get("allow_names") == "on",
            "require_names": request.POST.get("require_names") == "on",
            "default_sort": request.POST.get("default_sort", "popular"),
        }
        payload: dict[str, JsonValue] = {
            "settings": settings,
            "state": request.POST.get("state", "draft"),
        }
        execute_idempotent(
            scope=f"studio.events.qna.manage.user-{request.user.pk}",
            key=key,
            request={
                "event_id": str(event_id),
                "expected_revision": expected_revision,
                **payload,
            },
            command=lambda: _session_update_result(event_id, payload, expected_revision, request),
        )
    except (QnaError, RevisionConflict) as error:
        return _error_response(request, event_id, error)
    return HttpResponseRedirect(f"/studio/events/{event_id}/qna/?saved=1")


def _moderate_result(
    event_id: uuid.UUID,
    question_id: str,
    payload: dict[str, Any],
    expected_revision: int,
    request: HttpRequest,
) -> dict[str, Any]:
    question = update_question(
        event_id,
        question_id,
        payload,
        moderator=True,
        audit_context=_audit_context(request),
        expected_revision=expected_revision,
    )
    revision = EventQnaSession.objects.get(event_id=event_id).revision
    return {"status": question.status, "pinned": question.pinned, "revision": revision}


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
        expected_revision = _form_revision(request)
        key = _form_idempotency_key(request)
        execute_idempotent(
            scope=f"studio.events.qna.moderate.user-{request.user.pk}",
            key=key,
            request={
                "event_id": str(event_id),
                "question_id": question_id,
                "expected_revision": expected_revision,
                **payload,
            },
            command=lambda: _moderate_result(
                event_id, question_id, payload, expected_revision, request
            ),
        )
    except (QnaError, RevisionConflict) as error:
        return _error_response(request, event_id, error)
    return HttpResponseRedirect(f"/studio/events/{event_id}/qna/?saved=1")


def _retry_result(event_id: uuid.UUID, request: HttpRequest) -> dict[str, Any]:
    result = retry_event_qna_provision(event_id, audit_context=_audit_context(request))
    return {"job_id": str(result.job.id), "status": result.job.status}


@capability_required("events.qna.provision.retry")
def event_qna_retry(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)
    # Same contract as the API adapter: the confirmation is a server-checked
    # field, not the template dialog.
    if request.POST.get("confirmed") != _CONFIRMED:
        return HttpResponse("Explicit confirmation is required.", status=400)
    try:
        key = _form_idempotency_key(request)
        execute_idempotent(
            scope=f"studio.events.qna.provision.retry.user-{request.user.pk}",
            key=key,
            request={"event_id": str(event_id), "confirmed": True},
            command=lambda: _retry_result(event_id, request),
        )
    except (QnaError, ValueError) as error:
        return HttpResponse(str(error), status=400)
    return HttpResponseRedirect(f"/studio/events/{event_id}/qna/?retried=1")


@capability_required("events.qna.cohost.create")
def event_qna_cohost(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)
    name = request.POST.get("name")
    passcode = request.POST.get("passcode")
    # The one-time fence: a browser retry must not create a second invite, and
    # the stored replay result carries only identifiers -- never the passcode.
    holder: dict[str, Any] = {}

    def command() -> dict[str, JsonValue]:
        invite = create_cohost(
            event_id,
            name=name,
            passcode=passcode,
            actor_ref=f"user:{request.user.pk}",
            audit_context=_audit_context(request),
        )
        holder["invite"] = invite
        return {"invite_id": str(invite["invite_id"])}

    try:
        key = _form_idempotency_key(request)
        result = execute_idempotent(
            scope=f"studio.events.qna.cohost.create.user-{request.user.pk}",
            key=key,
            request={"event_id": str(event_id), "name": name},
            command=command,
        )
    except QnaError as error:
        return HttpResponse(error.message, status=error.status)
    if result.replayed:
        # The grant was already created under this key; the passcode is never
        # shown twice, so the operator is sent back to the session page.
        return HttpResponseRedirect(f"/studio/events/{event_id}/qna/?already-granted=1")
    # The passcode is intentionally displayed only in this one-time response;
    # it is never put in a redirect, URL, audit row, or log.
    return render(request, "studio/event_qna_cohost_created.html", {"invite": holder["invite"]})


def _revoke_result(event_id: uuid.UUID, invite_id: str, request: HttpRequest) -> dict[str, Any]:
    revoke_cohost(event_id, invite_id, audit_context=_audit_context(request))
    return {"revoked": True}


@capability_required("events.qna.cohost.revoke")
def event_qna_cohost_revoke(
    request: HttpRequest,
    event_id: uuid.UUID,
    invite_id: str,
) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)
    if request.POST.get("confirmed") != _CONFIRMED:
        return HttpResponse("Explicit confirmation is required.", status=400)
    try:
        key = _form_idempotency_key(request)
        execute_idempotent(
            scope=f"studio.events.qna.cohost.revoke.user-{request.user.pk}",
            key=key,
            request={"event_id": str(event_id), "invite_id": invite_id, "confirmed": True},
            command=lambda: _revoke_result(event_id, invite_id, request),
        )
    except QnaError as error:
        return HttpResponse(error.message, status=error.status)
    return HttpResponseRedirect(f"/studio/events/{event_id}/qna/?saved=1")
