import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
)
from django.shortcuts import render
from django.utils import timezone

from accounts.studio_authorization import (
    StudioAuthenticationRequired,
    StudioAuthorizationDenied,
    authorize_studio_request,
)
from accounts.studio_sessions import session_reference
from core.audit import AuditWriteContext, record_audit_event
from core.audit_queries import (
    AUDIT_DISPLAY_FIELDS,
    AuditQueryError,
    parse_audit_list_query,
    present_audit_event,
)
from core.capabilities import Capability
from core.models import AuditEvent
from core.services import ServiceContext
from management_api.query import PageQuery
from management_auth.idempotency import (
    SecretUnavailableOnReplay,
    credential_idempotency_operation,
    hash_management_idempotency_key,
)
from management_auth.models import APIPrincipal
from management_auth.policies import require_high_risk_policy
from management_auth.services import manageable_service_principals
from management_registry import CAPABILITY_REGISTRY

from .auth import capability_required, staff_required


def _navigation(request: HttpRequest) -> tuple[dict[str, str], ...]:
    navigation: list[dict[str, str]] = []
    for capability in CAPABILITY_REGISTRY:
        if (
            capability.test_only
            or capability.studio.test_only
            or capability.studio.method != "GET"
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
                "label": (
                    "Audit" if capability.key == "studio.audit.browse" else "API credentials"
                ),
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


@dataclass(frozen=True, slots=True)
class CredentialBrowserActor:
    principal: APIPrincipal
    capability: Capability


def _credential_actor(
    request: HttpRequest,
    capability_key: str,
) -> CredentialBrowserActor | HttpResponse:
    capability = CAPABILITY_REGISTRY.require(capability_key)
    try:
        studio_principal = authorize_studio_request(
            request_user=request.user,
            session_reference=session_reference(request),
            capability=capability,
        )
    except StudioAuthenticationRequired:
        return HttpResponse("Studio authentication required", status=401)
    except StudioAuthorizationDenied:
        return HttpResponseForbidden("Studio access denied")
    principal = (
        APIPrincipal.objects.select_related("user")
        .filter(
            user=studio_principal.user,
            kind=APIPrincipal.Kind.HUMAN,
            is_active=True,
        )
        .first()
    )
    if principal is None:
        return HttpResponseForbidden("Studio access denied")
    return CredentialBrowserActor(principal=principal, capability=capability)


def _credential_confirmation(capability: object, value: object) -> bool:
    policy_key = getattr(capability, "high_risk_policy", None)
    if not isinstance(policy_key, str):
        return False
    try:
        return require_high_risk_policy(policy_key).authorize(confirmed=value)
    except Exception:
        return False


def _credential_context(actor: CredentialBrowserActor) -> ServiceContext:
    return ServiceContext.from_current(actor_ref=f"user:{actor.principal.user_id}")


def _audit_credential_denial(
    request: HttpRequest,
    actor: CredentialBrowserActor,
    *,
    reason: str,
) -> None:
    capability = actor.capability
    key_hash = ""
    try:
        key_hash = hash_management_idempotency_key(
            actor.principal.id,
            credential_idempotency_operation(capability.key),
            request.POST.get("idempotency_key", ""),
        )
    except (AttributeError, TypeError, ValueError):
        pass
    scope_snapshot = (
        ["studio.home.read"] if request.POST.getlist("scopes") == ["studio.home.read"] else []
    )
    record_audit_event(
        action=capability.audit_action,
        target_type="management.credential",
        target_label="credential-request",
        outcome=AuditEvent.Outcome.DENIED,
        context=AuditWriteContext(
            actor_id=actor.principal.user_id,
            api_principal_id=actor.principal.id,
            actor_ref=f"user:{actor.principal.user_id}",
            idempotency_key_hash=key_hash,
        ),
        changes={},
        metadata={
            "reason": reason,
            "scopes": scope_snapshot,
            "expires_at": None,
            "state": "denied",
        },
    )


def _credential_inventory(actor: CredentialBrowserActor) -> dict:
    list_capability = CAPABILITY_REGISTRY.require("management.credentials.list")
    return list_capability.service(
        PageQuery(page=1, page_size=100, sort=(), filters={}),
        context=_credential_context(actor),
        actor_principal=actor.principal,
    )


def _render_credentials(
    request: HttpRequest,
    actor: CredentialBrowserActor,
    *,
    raw_token: str = "",
    notice: str = "",
    error_message: str = "",
    status: int = 200,
) -> HttpResponse:
    inventory = _credential_inventory(actor)
    targets = manageable_service_principals(actor.principal).order_by("name", "id")
    now = timezone.now()
    expiry_options = tuple(
        ((now + timedelta(days=days)).isoformat(), f"{days} days") for days in (30, 60, 90)
    )
    return render(
        request,
        "studio/credentials.html",
        {
            "credentials": inventory["items"],
            "targets": targets,
            "raw_token": raw_token,
            "notice": notice,
            "error_message": error_message,
            "idempotency_key": uuid.uuid4(),
            "expiry_options": expiry_options,
            "studio_navigation": _navigation(request),
        },
        status=status,
    )


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


def credential_list(request: HttpRequest) -> HttpResponse:
    capability_key = (
        "management.credentials.create"
        if request.method == "POST"
        else "management.credentials.list"
    )
    if request.method not in {"GET", "HEAD", "POST"}:
        return HttpResponseNotAllowed(("GET", "HEAD", "POST"))
    selected = _credential_actor(request, capability_key)
    if isinstance(selected, HttpResponse):
        return selected
    actor = selected
    if request.method != "POST":
        return _render_credentials(request, actor)
    capability = actor.capability
    if not _credential_confirmation(capability, request.POST.get("confirmed") == "true"):
        _audit_credential_denial(request, actor, reason="confirmation_missing")
        return _render_credentials(
            request,
            actor,
            error_message="Confirm credential creation before continuing.",
            status=400,
        )
    scopes = request.POST.getlist("scopes")
    if scopes != ["studio.home.read"]:
        _audit_credential_denial(request, actor, reason="scope_invalid")
        return _render_credentials(
            request,
            actor,
            error_message="Choose the available health scope.",
            status=400,
        )
    try:
        target_id = uuid.UUID(request.POST.get("target_principal_id", ""))
        expires_at = datetime.fromisoformat(request.POST.get("expires_at", ""))
        if not timezone.is_aware(expires_at):
            raise ValueError
        result = capability.service(
            actor_principal=actor.principal,
            target_principal_id=target_id,
            name=request.POST.get("name", ""),
            scopes=scopes,
            expires_at=expires_at,
            idempotency_key=request.POST.get("idempotency_key", ""),
            actor_permission=capability.django_permission,
            created_by=actor.principal.user,
        )
    except SecretUnavailableOnReplay:
        _audit_credential_denial(request, actor, reason="secret_unavailable_on_replay")
        return _render_credentials(
            request,
            actor,
            error_message=(
                "This request was already completed. The one-time credential is no longer "
                "available; rotate it to issue a new secret."
            ),
            status=409,
        )
    except Exception:
        _audit_credential_denial(request, actor, reason="invalid_request")
        return _render_credentials(
            request,
            actor,
            error_message="The credential could not be created.",
            status=400,
        )
    return _render_credentials(
        request,
        actor,
        raw_token=str(result.response["token"]),
        notice="Credential created. Copy it now; it will not be shown again.",
        status=201,
    )


def credential_rotate(request: HttpRequest, credential_id: uuid.UUID) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(("POST",))
    selected = _credential_actor(request, "management.credentials.rotate")
    if isinstance(selected, HttpResponse):
        return selected
    actor = selected
    capability = actor.capability
    if not _credential_confirmation(capability, request.POST.get("confirmed") == "true"):
        _audit_credential_denial(request, actor, reason="confirmation_missing")
        return _render_credentials(
            request,
            actor,
            error_message="Confirm credential rotation before continuing.",
            status=400,
        )
    try:
        expected_revision = int(request.POST.get("expected_revision", "0"))
        overlap_seconds = int(request.POST.get("overlap_seconds", "0"))
        if overlap_seconds not in {0, 300, 3600}:
            raise ValueError
        result = capability.service(
            actor_principal=actor.principal,
            credential_id=credential_id,
            expected_revision=expected_revision,
            idempotency_key=request.POST.get("idempotency_key", ""),
            actor_permission=capability.django_permission,
            overlap=timedelta(seconds=overlap_seconds),
            created_by=actor.principal.user,
        )
    except SecretUnavailableOnReplay:
        _audit_credential_denial(request, actor, reason="secret_unavailable_on_replay")
        return _render_credentials(
            request,
            actor,
            error_message=(
                "This rotation was already completed. The one-time credential is no longer "
                "available; rotate the active successor with a new request."
            ),
            status=409,
        )
    except Exception:
        _audit_credential_denial(request, actor, reason="state_conflict")
        return _render_credentials(
            request,
            actor,
            error_message="The credential could not be rotated. Refresh and try again.",
            status=409,
        )
    return _render_credentials(
        request,
        actor,
        raw_token=str(result.response["token"]),
        notice="Credential rotated. Copy the successor now; it will not be shown again.",
        status=201,
    )


def credential_revoke(request: HttpRequest, credential_id: uuid.UUID) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(("POST",))
    selected = _credential_actor(request, "management.credentials.revoke")
    if isinstance(selected, HttpResponse):
        return selected
    actor = selected
    capability = actor.capability
    if not _credential_confirmation(capability, request.POST.get("confirmed") == "true"):
        _audit_credential_denial(request, actor, reason="confirmation_missing")
        return _render_credentials(
            request,
            actor,
            error_message="Confirm credential revocation before continuing.",
            status=400,
        )
    try:
        capability.service(
            actor_principal=actor.principal,
            credential_id=credential_id,
            expected_revision=int(request.POST.get("expected_revision", "0")),
            idempotency_key=request.POST.get("idempotency_key", ""),
            actor_permission=capability.django_permission,
        )
    except Exception:
        _audit_credential_denial(request, actor, reason="state_conflict")
        return _render_credentials(
            request,
            actor,
            error_message="The credential could not be revoked. Refresh and try again.",
            status=409,
        )
    return _render_credentials(
        request,
        actor,
        notice="Credential revoked. It can no longer authenticate.",
    )
