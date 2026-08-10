import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from accounts.navigation import can_access_course_studio
from accounts.studio_authorization import (
    StudioAuthenticationRequired,
    StudioAuthorizationDenied,
    authorize_studio_request,
)
from accounts.studio_sessions import session_reference
from content.public_data import public_projection
from core.audit import AuditWriteContext, record_audit_event
from core.audit_queries import (
    AUDIT_DISPLAY_FIELDS,
    AuditQueryError,
    parse_audit_list_query,
    present_audit_event,
)
from core.capabilities import Capability
from core.idempotency import JsonObject, execute_idempotent
from core.models import AuditEvent, RevisionConflict
from core.services import ServiceContext
from events.importers import ProtectedSourceError
from events.models import HistoricalEventMapping, HistoricalRegistrationSourceRun
from events.services import (
    HistoricalRegistrationConflict,
    HistoricalRegistrationInvalid,
    serialize_run,
)
from management_api.query import PageQuery
from management_auth.idempotency import (
    SecretUnavailableOnReplay,
    credential_idempotency_operation,
    hash_management_idempotency_key,
)
from management_auth.models import APIPrincipal
from management_auth.policies import require_high_risk_policy
from management_auth.services import manageable_service_principals, principal_has_permission
from management_registry import CAPABILITY_REGISTRY

from .auth import capability_required, staff_required


def _navigation(request: HttpRequest) -> tuple[dict[str, str], ...]:
    navigation: list[dict[str, str]] = []
    if can_access_course_studio(request.user):
        navigation.append(
            {
                "key": "studio.courses",
                "label": "Courses",
                "route": "studio_courses_course_list",
            }
        )
    for capability in CAPABILITY_REGISTRY:
        if (
            capability.test_only
            or capability.studio.test_only
            or capability.studio.method != "GET"
            or capability.key
            in {
                "studio.home.read",
                "studio.audit.detail",
                "events.historical_registration_import.detail",
                "events.historical_registration_total.read",
            }
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
                "label": {
                    "studio.audit.browse": "Audit",
                    "management.credentials.list": "API credentials",
                    "events.historical_registration_import.manage": (
                        "Historical registration totals"
                    ),
                    "events.historical_registration_mapping.manage": "Historical mappings",
                    "events.historical_registration_total.read": "Registration total preview",
                }.get(capability.key, capability.description),
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
    targets = tuple(manageable_service_principals(actor.principal).order_by("name", "id"))
    available_scopes = tuple(
        capability.key
        for capability in CAPABILITY_REGISTRY
        if not capability.test_only
        and not capability.admin_api.test_only
        and any(
            principal_has_permission(target, capability.django_permission) for target in targets
        )
    )
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
            "available_scopes": available_scopes,
            "raw_token": raw_token,
            "notice": notice,
            "error_message": error_message,
            "idempotency_key": uuid.uuid4(),
            "expiry_options": expiry_options,
            "studio_navigation": _navigation(request),
        },
        status=status,
    )


def _event_actor(request: HttpRequest, capability_key: str):
    capability = CAPABILITY_REGISTRY.require(capability_key)
    try:
        principal = authorize_studio_request(
            request_user=request.user,
            session_reference=session_reference(request),
            capability=capability,
        )
    except StudioAuthenticationRequired:
        return HttpResponse("Studio authentication required", status=401)
    except StudioAuthorizationDenied:
        return HttpResponseForbidden("Studio access denied")
    return principal


def _studio_event_context(request: HttpRequest) -> ServiceContext:
    return ServiceContext.from_current(actor_ref=f"user:{request.user.pk}")


def _historical_studio_error(error: Exception) -> tuple[str, int]:
    if isinstance(error, HistoricalRegistrationConflict | RevisionConflict):
        return ("The aggregate state changed or is not ready for this action.", 409)
    if isinstance(error, ProtectedSourceError):
        return (f"The registered source was rejected ({error.code}).", 400)
    return ("The historical aggregate request is invalid.", 400)


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


def historical_registration_list(request: HttpRequest) -> HttpResponse:
    if request.method not in {"GET", "HEAD", "POST"}:
        return HttpResponseNotAllowed(("GET", "HEAD", "POST"))
    capability_key = (
        "events.historical_registration_import.create"
        if request.method == "POST"
        else "events.historical_registration_import.manage"
    )
    selected = _event_actor(request, capability_key)
    if isinstance(selected, HttpResponse):
        return selected
    error_message = ""
    status = 200
    if request.method == "POST":
        try:
            if request.POST.get("confirmed") != "true":
                raise HistoricalRegistrationInvalid("confirmation_required")
            provider = request.POST.get("provider", "")
            source_reference = request.POST.get("source_reference", "")
            mapping_set_revision = int(request.POST.get("mapping_set_revision", "0"))
            idempotency_key = request.POST.get("idempotency_key", "")

            def command() -> dict:
                run, created = selected.capability.service(
                    provider=provider,
                    source_reference=source_reference,
                    mapping_set_revision=mapping_set_revision,
                    actor=selected.user,
                    context=_studio_event_context(request),
                )
                return {"run_id": str(run.id), "created": created}

            result = execute_idempotent(
                scope=selected.capability.key,
                key=idempotency_key,
                request={
                    "provider": provider,
                    "source_reference": source_reference,
                    "mapping_set_revision": mapping_set_revision,
                },
                command=command,
            )
            return HttpResponseRedirect(
                reverse(
                    "studio:historical-registration-detail",
                    kwargs={"run_id": result.value["run_id"]},
                )
            )
        except Exception as error:
            error_message, status = _historical_studio_error(error)
    listing = CAPABILITY_REGISTRY.require("events.historical_registration_import.manage").service(
        page=1, page_size=100
    )
    registry = getattr(settings, "HISTORICAL_REGISTRATION_SOURCES", {})
    references = (
        tuple(sorted(reference for reference in registry if isinstance(reference, str)))
        if isinstance(registry, dict)
        else ()
    )
    return render(
        request,
        "studio/historical_registration_list.html",
        {
            "runs": listing["items"],
            "source_references": references,
            "providers": HistoricalRegistrationSourceRun.Provider.choices,
            "idempotency_key": uuid.uuid4(),
            "error_message": error_message,
            "studio_navigation": _navigation(request),
        },
        status=status,
    )


def historical_registration_detail(request: HttpRequest, run_id: uuid.UUID) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(("GET", "HEAD"))
    selected = _event_actor(request, "events.historical_registration_import.detail")
    if isinstance(selected, HttpResponse):
        return selected
    try:
        detail = selected.capability.service(run_id)
    except HistoricalRegistrationSourceRun.DoesNotExist:
        return HttpResponse("Historical import unavailable", status=404)
    return render(
        request,
        "studio/historical_registration_detail.html",
        {
            "run": detail,
            "actions": ("dry-run", "validate", "activate", "cancel", "rollback"),
            "idempotency_key": uuid.uuid4(),
            "studio_navigation": _navigation(request),
        },
    )


def historical_registration_action(
    request: HttpRequest,
    run_id: uuid.UUID,
    action: str,
) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(("POST",))
    if action not in {"dry-run", "validate", "activate", "cancel", "rollback"}:
        return HttpResponse("Historical action unavailable", status=404)
    selected = _event_actor(request, f"events.historical_registration_import.{action}")
    if isinstance(selected, HttpResponse):
        return selected
    try:
        if request.POST.get("confirmed") != "true":
            raise HistoricalRegistrationInvalid("confirmation_required")
        reason_code = request.POST.get("reason_code", "")

        def command() -> dict:
            kwargs = {
                "actor": selected.user,
                "context": _studio_event_context(request),
            }
            if action != "dry-run":
                kwargs["reason_code"] = reason_code
            value = selected.capability.service(run_id, **kwargs)
            return value if isinstance(value, dict) else serialize_run(value)

        execute_idempotent(
            scope=selected.capability.key,
            key=request.POST.get("idempotency_key", ""),
            request={
                "run_id": str(run_id),
                "action": action,
                "confirmed": True,
                "reason_code": reason_code,
            },
            command=command,
        )
    except Exception as error:
        message, status = _historical_studio_error(error)
        detail = CAPABILITY_REGISTRY.require(
            "events.historical_registration_import.detail"
        ).service(run_id)
        return render(
            request,
            "studio/historical_registration_detail.html",
            {
                "run": detail,
                "actions": ("dry-run", "validate", "activate", "cancel", "rollback"),
                "idempotency_key": uuid.uuid4(),
                "error_message": message,
                "studio_navigation": _navigation(request),
            },
            status=status,
        )
    return HttpResponseRedirect(
        reverse("studio:historical-registration-detail", kwargs={"run_id": run_id})
    )


def historical_registration_mappings(request: HttpRequest) -> HttpResponse:
    if request.method not in {"GET", "HEAD", "POST"}:
        return HttpResponseNotAllowed(("GET", "HEAD", "POST"))
    capability_key = (
        "events.historical_registration_mapping.create"
        if request.method == "POST"
        else "events.historical_registration_mapping.manage"
    )
    selected = _event_actor(request, capability_key)
    if isinstance(selected, HttpResponse):
        return selected
    error_message = ""
    status = 200
    if request.method == "POST":
        try:
            mapping_id_raw = request.POST.get("mapping_id", "")
            mapping_id = uuid.UUID(mapping_id_raw) if mapping_id_raw else None
            existing = (
                HistoricalEventMapping.objects.get(pk=mapping_id)
                if mapping_id is not None
                else None
            )
            expected_revision: int | None = None
            if existing is not None:
                expected_revision = int(request.POST.get("expected_revision", "0"))
                if expected_revision < 1:
                    raise HistoricalRegistrationInvalid("expected_revision_invalid")
            payload: JsonObject = {
                "mapping_id": str(mapping_id) if mapping_id else None,
                "provider": request.POST.get("provider") or None,
                "external_event_identifier": (
                    request.POST.get("external_event_identifier") or None
                ),
                "state": request.POST.get("state", ""),
                "canonical_slug": request.POST.get("canonical_slug", ""),
                "mapping_set_revision": int(request.POST.get("mapping_set_revision", "0")),
                "expected_revision": expected_revision,
                "reason_code": request.POST.get("reason_code", ""),
                "reason": request.POST.get("reason", ""),
                "coverage_boundary": request.POST.get("coverage_boundary", "historical"),
                "combination_policy": request.POST.get("combination_policy", "replacement"),
            }

            def command() -> dict:
                mapping = selected.capability.service(
                    **payload,
                    reviewer=selected.user,
                    context=_studio_event_context(request),
                )
                return {"mapping_id": str(mapping.id)}

            result = execute_idempotent(
                scope=selected.capability.key,
                key=request.POST.get("idempotency_key", ""),
                request=payload,
                command=command,
            )
            return HttpResponseRedirect(
                f"{reverse('studio:historical-registration-mappings')}"
                f"?updated={result.value['mapping_id']}"
            )
        except Exception as error:
            error_message, status = _historical_studio_error(error)
    listing = CAPABILITY_REGISTRY.require("events.historical_registration_mapping.manage").service(
        page=1, page_size=100
    )
    return render(
        request,
        "studio/historical_registration_mappings.html",
        {
            "mappings": listing["items"],
            "event_slugs": tuple(sorted(public_projection()["events_by_slug"])),
            "mapping_states": (
                HistoricalEventMapping.State.MAPPED,
                HistoricalEventMapping.State.EXCLUDED,
            ),
            "combination_policies": (
                "replacement",
                "additive_disjoint",
                "exclude",
            ),
            "providers": HistoricalRegistrationSourceRun.Provider.values,
            "idempotency_key": uuid.uuid4(),
            "error_message": error_message,
            "studio_navigation": _navigation(request),
        },
        status=status,
    )


def historical_registration_total(
    request: HttpRequest,
    canonical_key: str,
) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(("GET", "HEAD"))
    selected = _event_actor(request, "events.historical_registration_total.read")
    if isinstance(selected, HttpResponse):
        return selected
    try:
        preview = selected.capability.service(canonical_key)
    except HistoricalRegistrationInvalid:
        return HttpResponse("Registration total unavailable", status=404)
    return render(
        request,
        "studio/historical_registration_total.html",
        {
            "total": preview,
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
    if (
        not scopes
        or len(scopes) > 64
        or any(CAPABILITY_REGISTRY.get(scope) is None for scope in scopes)
    ):
        _audit_credential_denial(request, actor, reason="scope_invalid")
        return _render_credentials(
            request,
            actor,
            error_message="Choose one or more available scopes.",
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
