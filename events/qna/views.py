"""Public Event-linked Q&A adapters."""

from __future__ import annotations

import json
from typing import Any

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

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


def _moderator(request: HttpRequest, session: Any) -> bool:
    user = getattr(request, "user", None)
    if (
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "is_staff", False)
        and user.has_perm("events.manage_event_qna")
    ):
        return True
    return (
        services.cohost_for_request(
            session.id,
            request.COOKIES.get(security.COHOST_COOKIE),
        )
        is not None
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
    event: Any, request: HttpRequest, *, allow_host: bool = True
) -> tuple[Any, bool]:
    session = services._qna_session(event.id)  # shared service boundary; no direct mutation
    moderator = _moderator(request, session) if allow_host else False
    if moderator:
        return session, True
    if event.lifecycle not in services.PUBLIC_EVENT_LIFECYCLES:
        raise services.QnaNotFound()
    if session.state == services.EventQnaSession.State.ARCHIVED:
        raise services.QnaArchived()
    if session.state == services.EventQnaSession.State.DRAFT:
        raise services.QnaNotFound()
    return session, False


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
        event, redirect = _event(event_id, slug, redirect=request.method in {"GET", "HEAD"})
        if redirect is not None:
            return redirect
        session, moderator = _route_session(event, request)
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
                        "qna_config": _config(session, moderator=moderator),
                        "moderator": moderator,
                    },
                )
            ),
            _participant(request)[1],
        )
    except QnaError as error:
        return _json_error(error)


def _api_context(request: HttpRequest, event_id: str, slug: str) -> tuple[Any, Any, bool]:
    event, redirect = _event(event_id, slug)
    if redirect is not None:
        raise QnaError(404, "not_found", "The Q&A resource was not found.")
    session, moderator = _route_session(event, request)
    return event, session, moderator


def _rate(request: HttpRequest, scope: str, *, window: int, limit: int) -> None:
    address = request.META.get("REMOTE_ADDR", "")
    result = services.admit_rate(f"{scope}:{address}", window_seconds=window, limit=limit)
    if result is not None:
        error = QnaError(429, "rate_limited", "Too many requests. Wait a moment and try again.")
        error.retry_after = result  # type: ignore[attr-defined]
        raise error


def qna_questions(request: HttpRequest, event_id: str, slug: str) -> HttpResponse:
    try:
        event, session, moderator = _api_context(request, event_id, slug)
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
                moderator=moderator,
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
            _rate(request, f"question:{session.id}:{participant}", window=10, limit=1)
            _rate(request, f"question-hour:{session.id}:{participant}", window=3600, limit=20)
            _rate(request, "question-ip", window=3600, limit=300)
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
        event, session, moderator = _api_context(request, event_id, slug)
        if request.method != "PATCH":
            response = HttpResponse("Method not allowed", status=405)
            response["Allow"] = "PATCH"
            return _private(response)
        participant = security.participant_from_token(
            request.COOKIES.get(security.PARTICIPANT_COOKIE)
        )
        question = services.update_question(
            event.id,
            question_id,
            _body(request),
            participant=participant,
            moderator=moderator,
        )
        return _private(
            JsonResponse(
                services.serialize_question(question, participant=participant),
            )
        )
    except QnaError as error:
        return _json_error(error)


def qna_vote(request: HttpRequest, event_id: str, slug: str, question_id: str) -> HttpResponse:
    try:
        event, _session, _moderator = _api_context(request, event_id, slug)
        if request.method not in {"POST", "DELETE"}:
            response = HttpResponse("Method not allowed", status=405)
            response["Allow"] = "POST, DELETE"
            return _private(response)
        participant, token = _participant(request)
        _rate(request, f"vote:{_session.id}:{participant}", window=3600, limit=120)
        score, voted = services.vote_question(
            event.id, question_id, participant=participant, add=request.method == "POST"
        )
        return _with_participant(_private(JsonResponse({"score": score, "voted": voted})), token)
    except QnaError as error:
        return _json_error(error)


def qna_cohost_gate(request: HttpRequest, event_id: str, slug: str, name: str) -> HttpResponse:
    try:
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
        _rate(request, f"cohost:{event.id}", window=300, limit=10)
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
        if not _moderator(request, session):
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
