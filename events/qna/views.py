"""Public Event-linked Q&A adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.studio_authorization import (
    StudioAuthenticationRequired,
    StudioAuthorizationDenied,
    authorize_studio_request,
)
from accounts.studio_sessions import session_reference
from core.audit import AuditWriteContext
from management_registry import CAPABILITY_REGISTRY
from studio.auth import audit_capability_denial

from . import qr, security, services
from .errors import QnaError


def _private(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _json_error(error: QnaError) -> HttpResponse:
    response = JsonResponse(
        {"error": {"code": error.code, "message": error.message}},
        status=error.status,
    )
    if error.status == 429:
        response["Retry-After"] = getattr(error, "retry_after", "60")
    return _private(response)


def _body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QnaError(400, "invalid_json", "The request body must be a JSON object.") from exc
    if not isinstance(payload, dict):
        raise QnaError(400, "invalid_json", "The request body must be a JSON object.")
    return payload


def _event(event_id: str, slug: str, *, redirect: bool = False) -> tuple[Any, HttpResponse | None]:
    try:
        event = services.Event.objects.get(public_id=int(event_id))
    except (ValueError, services.Event.DoesNotExist) as exc:
        raise services.QnaNotFound() from exc
    if event.public_id is None or str(event.public_id) != str(event_id):
        raise services.QnaNotFound()
    if slug != event.slug:
        if redirect:
            return event, HttpResponseRedirect(f"{services.event_qna_path(event)}/")
        raise QnaError(404, "not_found", "The Q&A resource was not found.")
    return event, None


@dataclass(frozen=True, slots=True)
class ModerationActor:
    """A resolved privileged actor for one public Q&A request.

    Exactly one identity kind is set: staff actors carry their user id and
    co-host actors the opaque invite reference of their redeemed grant.
    Neither carries credentials, cookies, names, or question text.
    """

    user_id: Any | None = None
    cohost_invite_id: str | None = None

    @property
    def audit_context(self) -> AuditWriteContext:
        if self.user_id is not None:
            return AuditWriteContext(
                actor_id=self.user_id,
                actor_ref=f"user:{self.user_id}",
            )
        return AuditWriteContext(actor_ref=f"cohost:{self.cohost_invite_id}")


def _staff_principal(request: HttpRequest, *, capability_key: str) -> Any | None:
    """Resolve staff authority through the canonical Studio boundary.

    Returns the Studio principal only when the refreshed account holds the
    explicit permission and a live StaffSession; a revoked, idle-expired, or
    absolute-expired staff session never escalates the ordinary
    authenticated cookie. Anonymous and non-staff visitors resolve to None
    without database work.
    """

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        return authorize_studio_request(
            request_user=user,
            session_reference=session_reference(request),
            capability=CAPABILITY_REGISTRY.require(capability_key),
        )
    except (StudioAuthenticationRequired, StudioAuthorizationDenied):
        return None


def _moderation_actor(
    request: HttpRequest,
    session: Any,
    *,
    capability_key: str,
) -> ModerationActor | None:
    """Resolve the current privileged actor, or None.

    Staff authority and room-scoped co-host grants are distinct adapters:
    a co-host grant never implies a website staff session, and staff
    authorization is rechecked on every request.
    """

    principal = _staff_principal(request, capability_key=capability_key)
    if principal is not None:
        return ModerationActor(user_id=principal.user.pk)
    invite = services.cohost_for_request(
        session.id,
        request.COOKIES.get(security.COHOST_COOKIE),
    )
    if invite is not None:
        return ModerationActor(cohost_invite_id=str(invite.invite_id))
    return None


def _audit_denied_staff_moderation(request: HttpRequest) -> None:
    """Record one denial audit for a plausible staff actor whose elevation
    failed. Ordinary participant traffic — anonymous cookies or accounts
    without a staff-session reference — is never audited here."""

    user = getattr(request, "user", None)
    if not (
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_staff", False)
        and session_reference(request) is not None
    ):
        return
    audit_capability_denial(
        request,
        capability_key="events.qna.moderate",
        reason="moderation_denied",
        actor=user,
    )


def _participant(request: HttpRequest) -> tuple[str, str | None]:
    participant = security.participant_from_token(request.COOKIES.get(security.PARTICIPANT_COOKIE))
    if participant:
        return participant, None
    participant, token = security.new_participant()
    return participant, token


def _with_participant(response: HttpResponse, token: str | None) -> HttpResponse:
    if token:
        response.set_cookie(
            security.PARTICIPANT_COOKIE,
            token,
            max_age=security.PARTICIPANT_TTL,
            secure=True,
            httponly=True,
            samesite="Lax",
            path="/",
        )
    return response


def _route_session(
    event: Any,
    request: HttpRequest,
    *,
    actor_capability: str = "events.qna.read",
    allow_host: bool = True,
) -> tuple[Any, ModerationActor | None]:
    session = services._qna_session(event.id)  # shared service boundary; no direct mutation
    actor = (
        _moderation_actor(request, session, capability_key=actor_capability) if allow_host else None
    )
    if actor is not None:
        return session, actor
    if event.lifecycle not in services.PUBLIC_EVENT_LIFECYCLES:
        raise services.QnaNotFound()
    if session.state == services.EventQnaSession.State.ARCHIVED:
        raise services.QnaArchived()
    if session.state == services.EventQnaSession.State.DRAFT:
        raise services.QnaNotFound()
    return session, None


def _config(session: Any, *, moderator: bool) -> dict[str, Any]:
    config = services.serialize_session(session, moderator=moderator)
    event_path = services.event_qna_path(session.event)
    config.update(
        {
            "api_base": f"{event_path}/api",
            "can_ask": session.state == services.EventQnaSession.State.OPEN,
            "can_vote": session.state == services.EventQnaSession.State.OPEN,
            "banner": (
                "Questions are closed for this session."
                if session.state != services.EventQnaSession.State.OPEN
                else ""
            ),
        }
    )
    if not moderator:
        config.pop("host_links", None)
    return config


@ensure_csrf_cookie
def public_qna(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    try:
        _rate_ip(request, "public", window=300, limit=2000)
        event, redirect = _event(event_id, slug, redirect=request.method in {"GET", "HEAD"})
        if redirect is not None:
            return redirect
        session, actor = _route_session(event, request)
        if request.method not in {"GET", "HEAD"}:
            return _private(HttpResponse("Method not allowed", status=405))
        return _with_participant(
            _private(
                render(
                    request,
                    "events/qna/public.html",
                    {
                        "event": event,
                        "session": session,
                        "qna_config": _config(session, moderator=actor is not None),
                        "moderator": actor is not None,
                    },
                )
            ),
            _participant(request)[1],
        )
    except QnaError as error:
        return _json_error(error)


def _api_context(
    request: HttpRequest,
    event_id: str,
    slug: str,
    *,
    actor_capability: str = "events.qna.read",
) -> tuple[Any, Any, ModerationActor | None]:
    _rate_ip(request, "public", window=300, limit=2000)
    event, redirect = _event(event_id, slug)
    if redirect is not None:
        raise QnaError(404, "not_found", "The Q&A resource was not found.")
    session, actor = _route_session(event, request, actor_capability=actor_capability)
    return event, session, actor


def _refuse(retry_after: int) -> QnaError:
    error = QnaError(429, "rate_limited", "Too many requests. Wait a moment and try again.")
    error.retry_after = retry_after  # type: ignore[attr-defined]
    return error


def _rate_identity(scope: str, identity: str, *, window: int, limit: int) -> None:
    """Admit one action against a named identity's budget -- no IP attached.

    The reviewed quotas (event-qna-integration.md "Rate limits and errors") scope
    questions and votes per participant/session; appending the source address to
    those keys made the same participant's quota reset on every IP change
    (audit EVT-06).  The caller composes the identity in full.
    """

    result = services.admit_rate(f"{scope}:{identity}", window_seconds=window, limit=limit)
    if result is not None:
        raise _refuse(result)


def _client_ip(request: HttpRequest) -> str:
    # REMOTE_ADDR is the only source: a client-supplied forwarding header must
    # never select the budget identity, or spoofing one header would buy a
    # fresh budget (audit EVT-06).  Proxy topology is an operations decision,
    # not something to infer per request.
    return request.META.get("REMOTE_ADDR", "")


def _rate_ip(request: HttpRequest, scope: str, *, window: int, limit: int) -> None:
    """Admit one action against a source-IP budget across sessions."""

    identity = f"{scope}:{_client_ip(request)}"
    result = services.admit_rate(identity, window_seconds=window, limit=limit)
    if result is not None:
        raise _refuse(result)


def qna_questions(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    try:
        event, session, actor = _api_context(request, event_id, slug)
        if request.method in {"GET", "HEAD"}:
            unknown = set(request.GET) - {"sort", "status"}
            if unknown:
                raise QnaError(400, "invalid_query", "Only sort and status are supported.")
            sort = request.GET.get("sort") or None
            raw_statuses = request.GET.get("status", "")
            statuses = {item for item in raw_statuses.split(",") if item} or None
            participant, token = _participant(request)
            items, counts, etag, current = services.list_questions(
                event.id,
                participant=participant,
                moderator=actor is not None,
                sort=sort,
                statuses=statuses,
            )
            if request.headers.get("If-None-Match") == etag:
                response = HttpResponse(status=304)
                response["ETag"] = etag
                return _with_participant(_private(response), token)
            response = JsonResponse(
                {"items": items, "counts": counts, "etag": etag, "state": current.state}
            )
            response["ETag"] = etag
            return _with_participant(_private(response), token)
        if request.method == "POST":
            participant, token = _participant(request)
            _rate_identity(f"question:{session.id}", participant, window=10, limit=1)
            _rate_identity(f"question-hour:{session.id}", participant, window=3600, limit=20)
            _rate_ip(request, "question-ip", window=3600, limit=300)
            question = services.submit_question(
                event.id,
                text=_body(request).get("text"),
                author_name=_body(request).get("author_name"),
                participant=participant,
            )
            response = JsonResponse(
                services.serialize_question(question, participant=participant, voted=True),
                status=201,
            )
            return _with_participant(_private(response), token)
        response = HttpResponse("Method not allowed", status=405)
        response["Allow"] = "GET, HEAD, POST"
        return _private(response)
    except QnaError as error:
        return _json_error(error)


def qna_question(request: HttpRequest, event_id: str, slug: str, question_id: str) -> HttpResponse:
    try:
        event, session, actor = _api_context(
            request, event_id, slug, actor_capability="events.qna.moderate"
        )
        if request.method != "PATCH":
            response = HttpResponse("Method not allowed", status=405)
            response["Allow"] = "PATCH"
            return _private(response)
        participant = security.participant_from_token(
            request.COOKIES.get(security.PARTICIPANT_COOKIE)
        )
        try:
            question = services.update_question(
                event.id,
                question_id,
                _body(request),
                participant=participant,
                moderator=actor is not None,
                audit_context=actor.audit_context if actor is not None else None,
            )
        except QnaError as error:
            if actor is None and error.status == 403:
                _audit_denied_staff_moderation(request)
            raise
        return _private(
            JsonResponse(
                services.serialize_question(question, participant=participant),
            )
        )
    except QnaError as error:
        return _json_error(error)


def qna_vote(request: HttpRequest, event_id: str, slug: str, question_id: str) -> HttpResponse:
    try:
        event, session, _actor = _api_context(request, event_id, slug)
        if request.method not in {"POST", "DELETE"}:
            response = HttpResponse("Method not allowed", status=405)
            response["Allow"] = "POST, DELETE"
            return _private(response)
        participant, token = _participant(request)
        _rate_identity(f"vote:{session.id}", participant, window=3600, limit=120)
        score, voted = services.vote_question(
            event.id, question_id, participant=participant, add=request.method == "POST"
        )
        return _with_participant(_private(JsonResponse({"score": score, "voted": voted})), token)
    except QnaError as error:
        return _json_error(error)


def qna_cohost_gate(request: HttpRequest, event_id: str, slug: str, name: str) -> HttpResponse:
    try:
        _rate_ip(request, "public", window=300, limit=2000)
        event, redirect = _event(event_id, slug, redirect=False)
        if redirect is not None:
            return redirect
        if request.method == "GET":
            session = services._public_session(event.id)
            return _private(
                render(
                    request,
                    "events/qna/cohost_gate.html",
                    {"event": event, "session": session, "name": name, "error": ""},
                )
            )
        if request.method != "POST":
            response = HttpResponse("Method not allowed", status=405)
            response["Allow"] = "GET, POST"
            return _private(response)
        _rate_ip(request, "cohost", window=300, limit=10)
        invite, error = services.redeem_cohost(event.id, name, request.POST.get("passcode", ""))
        if invite is None:
            session = services._qna_session(event.id)
            return _private(
                render(
                    request,
                    "events/qna/cohost_gate.html",
                    {"event": event, "session": session, "name": name, "error": error},
                    status=403,
                )
            )
        response = HttpResponseRedirect(f"{services.event_qna_path(event)}/host/")
        response.set_cookie(
            security.COHOST_COOKIE,
            security.new_cohost_token(str(invite.session_id), invite.invite_id),
            max_age=security.COHOST_TTL,
            secure=True,
            httponly=True,
            samesite="Lax",
            path="/",
        )
        return response
    except QnaError as error:
        return _json_error(error)


def _host_page(
    request: HttpRequest, event_id: str, slug: str, *, presentation: bool
) -> HttpResponse:
    try:
        event, redirect = _event(event_id, slug, redirect=False)
        if redirect is not None:
            return redirect
        session = services._qna_session(event.id)
        actor = _moderation_actor(request, session, capability_key="events.qna.read")
        if actor is None:
            raise QnaError(403, "forbidden", "A co-host grant or Studio authorization is required.")
        template = "events/qna/present.html" if presentation else "events/qna/host.html"
        return _private(
            render(
                request,
                template,
                {
                    "event": event,
                    "session": session,
                    "qna": _config(session, moderator=True),
                    "qna_config": _config(session, moderator=True),
                },
            )
        )
    except QnaError as error:
        return _json_error(error)


def qna_host(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    return _host_page(request, event_id, slug, presentation=False)


def qna_present(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    return _host_page(request, event_id, slug, presentation=True)


def qna_qr(request: HttpRequest, event_id: str, slug: str, kind: str) -> HttpResponse:
    try:
        event, redirect = _event(event_id, slug, redirect=False)
        if redirect is not None:
            return redirect
        services._public_session(event.id)
        if kind == "svg":
            response = HttpResponse(
                qr.svg(services.event_qna_share_url(event)),
                content_type="image/svg+xml",
            )
        elif kind == "png":
            try:
                size = int(request.GET.get("size", "512"))
            except ValueError as exc:
                raise QnaError(400, "invalid_size", "The QR size is invalid.") from exc
            if not 64 <= size <= 2048:
                raise QnaError(400, "invalid_size", "The QR size must be between 64 and 2048.")
            response = HttpResponse(
                qr.png(services.event_qna_share_url(event), size=size),
                content_type="image/png",
            )
        else:
            raise QnaError(404, "not_found", "The QR resource was not found.")
        response["Cache-Control"] = "public, max-age=300"
        return response
    except QnaError as error:
        return _json_error(error)
    except qr.QRCodeUnavailable:
        response = HttpResponse("The Q&A share code is temporarily unavailable.", status=503)
        response["Cache-Control"] = "no-store"
        return response
