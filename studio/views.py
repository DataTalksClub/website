import uuid

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render

from accounts.studio_authorization import (
    StudioAuthenticationRequired,
    StudioAuthorizationDenied,
    authorize_studio_request,
)
from accounts.studio_sessions import session_reference
from core.audit_queries import (
    AUDIT_DISPLAY_FIELDS,
    AuditQueryError,
    parse_audit_list_query,
    present_audit_event,
)
from core.services import ServiceContext

from .auth import capability_required, staff_required
from .registry import CAPABILITY_REGISTRY


def _navigation(request: HttpRequest) -> tuple[dict[str, str], ...]:
    navigation: list[dict[str, str]] = []
    for capability in CAPABILITY_REGISTRY:
        if (
            capability.test_only
            or capability.studio.test_only
            or capability.key in {"studio.home.read", "studio.audit.detail"}
        ):
            continue
        try:
            authorize_studio_request(
                request_user=request.user,
                session_reference=session_reference(request),
                capability=capability,
            )
        except (StudioAuthenticationRequired, StudioAuthorizationDenied):
            continue
        navigation.append(
            {
                "key": capability.key,
                "label": "Audit" if capability.key == "studio.audit.browse" else "Studio home",
                "route": capability.studio.route,
            }
        )
    return tuple(navigation)


def _service_context(request: HttpRequest) -> ServiceContext:
    principal = request.studio_principal  # type: ignore[attr-defined]
    return ServiceContext.from_current(actor_ref=f"user:{principal.user.pk}")


def _audit_fields_allowed(request: HttpRequest) -> bool:
    principal = request.studio_principal  # type: ignore[attr-defined]
    policy = principal.capability.field_policy
    if policy is None:
        return False
    try:
        return all(policy(principal.user, field) is True for field in AUDIT_DISPLAY_FIELDS)
    except Exception:
        return False


@staff_required
def home(request: HttpRequest) -> HttpResponse:
    principal = request.studio_principal  # type: ignore[attr-defined]
    principal.capability.service(None, context=_service_context(request))
    return render(
        request,
        "studio/home.html",
        {"studio_navigation": _navigation(request)},
    )


@capability_required("studio.audit.browse")
def audit_list(request: HttpRequest) -> HttpResponse:
    if not _audit_fields_allowed(request):
        return HttpResponseForbidden("Studio access denied")
    principal = request.studio_principal  # type: ignore[attr-defined]
    try:
        query = parse_audit_list_query(request.GET)
        page = principal.capability.service(
            query,
            context=_service_context(request),
            actor=principal.user,
        )
    except AuditQueryError:
        return HttpResponseBadRequest("Invalid audit filter")
    return render(
        request,
        "studio/audit_list.html",
        {
            "audit_page": page,
            "events": tuple((event.id, present_audit_event(event)) for event in page.events),
            "filters": query.filters,
            "studio_navigation": _navigation(request),
        },
    )


@capability_required("studio.audit.detail")
def audit_detail(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if not _audit_fields_allowed(request):
        return HttpResponseForbidden("Studio access denied")
    principal = request.studio_principal  # type: ignore[attr-defined]
    event = principal.capability.service(
        event_id,
        context=_service_context(request),
        actor=principal.user,
    )
    if event is None:
        return HttpResponse("Audit event unavailable", status=404, content_type="text/plain")
    policy = principal.capability.object_policy
    try:
        allowed = policy is not None and policy(principal.user, event) is True
    except Exception:
        allowed = False
    if not allowed:
        return HttpResponse("Audit event unavailable", status=404, content_type="text/plain")
    return render(
        request,
        "studio/audit_detail.html",
        {
            "event_id": event.id,
            "event": present_audit_event(event),
            "studio_navigation": _navigation(request),
        },
    )
