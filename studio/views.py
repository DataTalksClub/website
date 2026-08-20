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
from core.audit import AuditWriteContext, record_audit_event
from core.audit_queries import (
    AUDIT_DISPLAY_FIELDS,
    AuditQueryError,
    parse_audit_list_query,
    present_audit_event,
)
from core.capabilities import Capability
from core.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    JsonObject,
    execute_idempotent,
    hash_idempotency_key,
)
from core.models import AuditEvent, RevisionConflict
from core.navigation import (
    NAVIGATION_FORM_SLOTS,
    NAVIGATION_TARGETS,
    InvalidSiteNavigation,
    SiteNavigationRevisionConflict,
)
from core.services import ServiceContext
from core.site_settings import (
    ANNOUNCEMENT_ENABLED_KEY,
    ANNOUNCEMENT_MESSAGE_KEY,
    InvalidSiteSettingsBatch,
    SiteSettingsRevisionConflict,
)
from core.sponsors import (
    SPONSOR_FILTER_FIELDS,
    SPONSOR_PLACEMENTS,
    InvalidSponsor,
    SponsorNotFound,
    SponsorRevisionConflict,
)
from courses.models import CourseRegistrationCountSourceRun
from courses.registration_count_importer import CourseCountSourceError
from courses.services.registration_counts import (
    CourseRegistrationCountConflict,
    CourseRegistrationCountInvalid,
)
from courses.services.registration_counts import (
    serialize_run as serialize_course_count_run,
)
from events.identity import EventIdentityNotFound, get_event_identity, list_event_identities
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

from .auth import (
    audit_site_navigation_denial,
    audit_site_settings_denial,
    capability_required,
    staff_required,
)


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
                "events.identity.detail",
                "events.qna.read",
                "courses.registration_count_baseline.detail",
                "courses.registration_count_baseline.total",
                "site.sponsors.detail",
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
                    "site.settings.read": "Site settings",
                    "site.navigation.read": "Site navigation",
                    "events.historical_registration_import.manage": (
                        "Historical registration totals"
                    ),
                    "events.historical_registration_mapping.manage": "Historical mappings",
                    "events.historical_registration_total.read": "Registration total preview",
                    "events.identity.read": "Event identities",
                    "courses.registration_count_baseline.manage": ("Course registration totals"),
                    "site.sponsors.read": "Sponsors",
                }.get(capability.key, capability.description),
                "route": capability.studio.route,
            }
        )
    return tuple(navigation)


def _service_context(request: HttpRequest) -> ServiceContext:
    principal = request.studio_principal  # type: ignore[attr-defined]
    return ServiceContext.from_current(actor_ref=f"user:{principal.user.pk}")


def _audit_identity_access(request: HttpRequest, *, event_id: uuid.UUID | None = None) -> None:
    principal = request.studio_principal  # type: ignore[attr-defined]
    record_audit_event(
        action="events.identity.viewed",
        target_type="events.event",
        target_id=event_id,
        target_label="event-identity",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=AuditWriteContext(
            actor_id=principal.user.pk,
            actor_ref=f"user:{principal.user.pk}",
        ),
        metadata={"surface": "studio"},
    )


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


def _event_actor(
    request: HttpRequest,
    capability_key: str,
    *,
    target_id: uuid.UUID | None = None,
):
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
        actor_id = getattr(request.user, "pk", None)
        if (
            request.method == "POST"
            and capability_key.startswith("courses.registration_count_baseline.")
            and bool(getattr(request.user, "is_authenticated", False))
            and actor_id is not None
        ):
            key_hash = ""
            idempotency_key = request.POST.get("idempotency_key", "")
            if idempotency_key:
                try:
                    key_hash = hash_idempotency_key(capability.key, idempotency_key)
                except (TypeError, ValueError):
                    pass
            record_audit_event(
                action=capability.audit_action,
                target_type="courses.registration_count_source_run",
                target_id=target_id,
                target_label="course-registration-count-source",
                outcome=AuditEvent.Outcome.DENIED,
                context=AuditWriteContext.from_service_context(
                    ServiceContext.from_current(actor_ref=f"user:{actor_id}"),
                    actor_id=actor_id,
                    idempotency_key_hash=key_hash,
                ),
                changes={
                    "state": {"before": None, "after": None},
                    "revision": {"before": None, "after": None},
                },
                metadata={"reason": "permission_denied", "state": "denied"},
            )
        return HttpResponseForbidden("Studio access denied")
    return principal


def _studio_event_context(
    request: HttpRequest,
    *,
    idempotency_key: str | None = None,
) -> ServiceContext:
    return ServiceContext.from_current(
        actor_ref=f"user:{request.user.pk}",
        idempotency_key=idempotency_key,
    )


def _historical_studio_error(error: Exception) -> tuple[str, int]:
    if isinstance(error, HistoricalRegistrationConflict | RevisionConflict):
        return ("The aggregate state changed or is not ready for this action.", 409)
    if isinstance(error, ProtectedSourceError):
        return (f"The registered source was rejected ({error.code}).", 400)
    return ("The historical aggregate request is invalid.", 400)


def _course_count_studio_error(error: Exception) -> tuple[str, int]:
    if isinstance(error, CourseRegistrationCountConflict | RevisionConflict):
        return ("The course count state changed or is not ready for this action.", 409)
    if isinstance(error, CourseCountSourceError):
        return (f"The registered source was rejected ({error.code}).", 400)
    if isinstance(error, CourseRegistrationCountSourceRun.DoesNotExist):
        return ("The course count source is unavailable.", 404)
    return ("The course count request is invalid.", 400)


@staff_required
def home(request: HttpRequest) -> HttpResponse:
    principal = request.studio_principal  # type: ignore[attr-defined]
    principal.capability.service(None, context=_service_context(request))
    return render(
        request,
        "studio/home.html",
        {"studio_navigation": _navigation(request)},
    )


def _site_settings_can_write(request: HttpRequest) -> bool:
    capability = CAPABILITY_REGISTRY.require("site.settings.write")
    try:
        authorize_studio_request(
            request_user=request.user,
            session_reference=session_reference(request),
            capability=capability,
        )
    except (StudioAuthenticationRequired, StudioAuthorizationDenied):
        return False
    return True


def _audit_site_settings_denial(request: HttpRequest, *, reason: str) -> None:
    audit_site_settings_denial(
        request,
        reason=reason,
        idempotency_key=request.POST.get("idempotency_key", ""),
    )


def _site_settings_page_context(
    request: HttpRequest,
    *,
    submitted_enabled: object | None = None,
    submitted_message: object | None = None,
    idempotency_key: object | None = None,
    error_message: str = "",
    field_errors: dict[str, str] | None = None,
) -> dict[str, object]:
    read_capability = CAPABILITY_REGISTRY.require("site.settings.read")
    result = read_capability.service(context=_service_context(request))
    items = {
        item["key"]: item
        for item in result["settings"]
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    enabled = items[ANNOUNCEMENT_ENABLED_KEY]
    message = items[ANNOUNCEMENT_MESSAGE_KEY]
    return {
        "enabled_setting": enabled,
        "message_setting": message,
        "enabled_value": (enabled["value"] if submitted_enabled is None else submitted_enabled),
        "message_value": (message["value"] if submitted_message is None else submitted_message),
        "can_write": _site_settings_can_write(request),
        "idempotency_key": idempotency_key or uuid.uuid4(),
        "error_message": error_message,
        "field_errors": field_errors or {},
        "saved": request.GET.get("saved") == "1",
        "studio_navigation": _navigation(request),
    }


@capability_required("site.settings.read")
def site_settings_read(request: HttpRequest) -> HttpResponse:
    try:
        context = _site_settings_page_context(request)
    except Exception:
        return HttpResponse("Site settings are unavailable", status=500)
    return render(request, "studio/settings.html", context)


@capability_required("site.settings.write")
def site_settings_write(request: HttpRequest) -> HttpResponse:
    read_capability = CAPABILITY_REGISTRY.require("site.settings.read")
    try:
        authorize_studio_request(
            request_user=request.user,
            session_reference=session_reference(request),
            capability=read_capability,
        )
    except (StudioAuthenticationRequired, StudioAuthorizationDenied):
        _audit_site_settings_denial(request, reason="permission_denied")
        return HttpResponseForbidden("Studio access denied")

    raw_enabled = request.POST.get("announcement_enabled")
    enabled = raw_enabled == "true"
    message = request.POST.get("announcement_message", "")
    raw_idempotency = request.POST.get("idempotency_key", "")
    safe_idempotency: object = uuid.uuid4()
    form_state_valid = (
        raw_enabled in {None, "true"}
        and len(request.POST.getlist("announcement_enabled")) <= 1
        and len(request.POST.getlist("announcement_message")) == 1
        and len(request.POST.getlist("idempotency_key")) == 1
        and len(request.POST.getlist("enabled_expected_revision")) == 1
        and len(request.POST.getlist("message_expected_revision")) == 1
    )
    try:
        parsed_idempotency = uuid.UUID(raw_idempotency)
        if str(parsed_idempotency) != raw_idempotency:
            raise ValueError("non-canonical idempotency key")
        safe_idempotency = raw_idempotency
    except (AttributeError, TypeError, ValueError):
        form_state_valid = False
    try:
        enabled_revision = int(request.POST.get("enabled_expected_revision", ""))
        message_revision = int(request.POST.get("message_expected_revision", ""))
        if enabled_revision < 0 or message_revision < 0:
            raise ValueError("negative revision")
    except (TypeError, ValueError):
        form_state_valid = False
        enabled_revision = -1
        message_revision = -1
    if not form_state_valid:
        _audit_site_settings_denial(request, reason="invalid_request")
        context = _site_settings_page_context(
            request,
            submitted_enabled=enabled,
            submitted_message=message,
            idempotency_key=safe_idempotency,
            error_message="Correct the highlighted settings and try again.",
            field_errors={"form": "The settings form state is invalid."},
        )
        return render(request, "studio/settings.html", context, status=400)

    capability = request.studio_principal.capability  # type: ignore[attr-defined]
    try:
        capability.service(
            updates=[
                {
                    "key": ANNOUNCEMENT_ENABLED_KEY,
                    "value": enabled,
                    "expected_revision": enabled_revision,
                },
                {
                    "key": ANNOUNCEMENT_MESSAGE_KEY,
                    "value": message,
                    "expected_revision": message_revision,
                },
            ],
            source="studio",
            idempotency_key=raw_idempotency,
            actor_ref=f"user:{request.user.pk}",
            actor_id=request.user.pk,
            context=_service_context(request),
        )
    except SiteSettingsRevisionConflict:
        _audit_site_settings_denial(request, reason="revision_conflict")
        context = _site_settings_page_context(
            request,
            submitted_enabled=enabled,
            submitted_message=message,
            idempotency_key=raw_idempotency,
            error_message="The settings changed in another session. Review and save again.",
            field_errors={"form": "A submitted revision is stale."},
        )
        return render(request, "studio/settings.html", context, status=409)
    except (IdempotencyConflict, IdempotencyInProgress):
        _audit_site_settings_denial(request, reason="idempotency_conflict")
        context = _site_settings_page_context(
            request,
            submitted_enabled=enabled,
            submitted_message=message,
            idempotency_key=raw_idempotency,
            error_message="This save request conflicts with an earlier submission.",
            field_errors={"form": "Reload the page before trying again."},
        )
        return render(request, "studio/settings.html", context, status=409)
    except (InvalidSiteSettingsBatch, TypeError, ValueError):
        _audit_site_settings_denial(request, reason="invalid_request")
        context = _site_settings_page_context(
            request,
            submitted_enabled=enabled,
            submitted_message=message,
            idempotency_key=raw_idempotency,
            error_message="Correct the highlighted settings and try again.",
            field_errors={
                "announcement_message": ("Enter one plain-text line of 500 characters or fewer.")
            },
        )
        return render(request, "studio/settings.html", context, status=400)
    except Exception:
        _audit_site_settings_denial(request, reason="internal_error")
        return HttpResponse("Site settings could not be saved", status=500)
    return HttpResponseRedirect(f"{reverse('studio:settings')}?saved=1")


def site_settings(request: HttpRequest) -> HttpResponse:
    if request.method in {"GET", "HEAD"}:
        return site_settings_read(request)
    return site_settings_write(request)


site_settings.management_capability_keys = (  # type: ignore[attr-defined]
    "site.settings.read",
    "site.settings.write",
)
site_settings.management_capability_views = {  # type: ignore[attr-defined]
    "GET": site_settings_read,
    "POST": site_settings_write,
}


def _site_navigation_can_write(request: HttpRequest) -> bool:
    capability = CAPABILITY_REGISTRY.require("site.navigation.write")
    try:
        authorize_studio_request(
            request_user=request.user,
            session_reference=session_reference(request),
            capability=capability,
        )
    except (StudioAuthenticationRequired, StudioAuthorizationDenied):
        return False
    return True


def _audit_site_navigation_denial(request: HttpRequest, *, reason: str) -> None:
    audit_site_navigation_denial(
        request,
        reason=reason,
        idempotency_key=request.POST.get("idempotency_key", ""),
    )


def _navigation_source_label(source: object) -> str:
    if source == "admin_api":
        return "Admin API"
    if source == "studio":
        return "Studio"
    return "Code default"


def _empty_navigation_slot(index: int) -> dict[str, object]:
    return {
        "index": index,
        "number": index + 1,
        "key": "",
        "label": "",
        "target": "",
        "position": "",
        "visible": False,
    }


def _navigation_slots(entries: object) -> list[dict[str, object]]:
    slots: list[dict[str, object]] = []
    if isinstance(entries, list):
        for index, item in enumerate(entries[:NAVIGATION_FORM_SLOTS]):
            if not isinstance(item, dict):
                continue
            slots.append(
                {
                    "index": index,
                    "number": index + 1,
                    "key": item.get("key", ""),
                    "label": item.get("label", ""),
                    "target": item.get("target", ""),
                    "position": item.get("position", ""),
                    "visible": item.get("visible") is True,
                }
            )
    while len(slots) < NAVIGATION_FORM_SLOTS:
        slots.append(_empty_navigation_slot(len(slots)))
    return slots


def _parse_navigation_form(request: HttpRequest) -> tuple[list[dict[str, object]], bool]:
    entries: list[dict[str, object]] = []
    form_state_valid = (
        len(request.POST.getlist("idempotency_key")) == 1
        and len(request.POST.getlist("expected_revision")) == 1
    )
    for index in range(NAVIGATION_FORM_SLOTS):
        keys = request.POST.getlist(f"entry-{index}-key")
        labels = request.POST.getlist(f"entry-{index}-label")
        targets = request.POST.getlist(f"entry-{index}-target")
        positions = request.POST.getlist(f"entry-{index}-position")
        visibles = request.POST.getlist(f"entry-{index}-visible")
        if any(len(values) > 1 for values in (keys, labels, targets, positions, visibles)):
            form_state_valid = False
        key = keys[0] if keys else ""
        label = labels[0] if labels else ""
        target = targets[0] if targets else ""
        position = positions[0] if positions else ""
        visible = visibles[0] == "true" if visibles else False
        if not key and not label and not target and not position and not visibles:
            continue
        parsed_position: object = position
        if position == "":
            parsed_position = None
        else:
            try:
                parsed_position = int(position)
            except (TypeError, ValueError):
                form_state_valid = False
        entries.append(
            {
                "key": key,
                "label": label,
                "target": target,
                "position": parsed_position,
                "visible": visible,
            }
        )
    return entries, form_state_valid


def _navigation_error_links(field_errors: dict[str, str]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for field, message in field_errors.items():
        href = "#site-navigation-fields"
        if field.startswith("entries."):
            parts = field.split(".")
            if len(parts) >= 2 and parts[1].isdigit():
                href = f"#navigation-entry-{parts[1]}"
        links.append({"href": href, "message": message})
    return links


def _site_navigation_page_context(
    request: HttpRequest,
    *,
    submitted_entries: object | None = None,
    idempotency_key: object | None = None,
    error_message: str = "",
    field_errors: dict[str, str] | None = None,
) -> dict[str, object]:
    read_capability = CAPABILITY_REGISTRY.require("site.navigation.read")
    result = read_capability.service(context=_service_context(request))
    current_entries = result["entries"] if isinstance(result.get("entries"), list) else []
    display_entries = submitted_entries if submitted_entries is not None else current_entries
    return {
        "menu": result,
        "source_label": _navigation_source_label(result.get("source")),
        "entry_slots": _navigation_slots(display_entries),
        "target_choices": NAVIGATION_TARGETS,
        "can_write": _site_navigation_can_write(request),
        "idempotency_key": idempotency_key or uuid.uuid4(),
        "error_message": error_message,
        "field_errors": field_errors or {},
        "error_links": _navigation_error_links(field_errors or {}),
        "saved": request.GET.get("saved") == "1",
        "studio_navigation": _navigation(request),
    }


@capability_required("site.navigation.read")
def site_navigation_read(request: HttpRequest) -> HttpResponse:
    try:
        context = _site_navigation_page_context(request)
    except Exception:
        return HttpResponse("Site navigation is unavailable", status=500)
    return render(request, "studio/navigation.html", context)


@capability_required("site.navigation.write")
def site_navigation_write(request: HttpRequest) -> HttpResponse:
    read_capability = CAPABILITY_REGISTRY.require("site.navigation.read")
    try:
        authorize_studio_request(
            request_user=request.user,
            session_reference=session_reference(request),
            capability=read_capability,
        )
    except (StudioAuthenticationRequired, StudioAuthorizationDenied):
        _audit_site_navigation_denial(request, reason="permission_denied")
        return HttpResponseForbidden("Studio access denied")

    raw_idempotency = request.POST.get("idempotency_key", "")
    safe_idempotency: object = uuid.uuid4()
    entries, form_state_valid = _parse_navigation_form(request)
    try:
        parsed_idempotency = uuid.UUID(raw_idempotency)
        if str(parsed_idempotency) != raw_idempotency:
            raise ValueError("non-canonical idempotency key")
        safe_idempotency = raw_idempotency
    except (AttributeError, TypeError, ValueError):
        form_state_valid = False
    try:
        expected_revision = int(request.POST.get("expected_revision", ""))
        if expected_revision < 0:
            raise ValueError("negative revision")
    except (TypeError, ValueError):
        form_state_valid = False
        expected_revision = -1
    if not form_state_valid:
        _audit_site_navigation_denial(request, reason="invalid_request")
        context = _site_navigation_page_context(
            request,
            submitted_entries=entries,
            idempotency_key=safe_idempotency,
            error_message="Correct the highlighted navigation entries and try again.",
            field_errors={"form": "The navigation form state is invalid."},
        )
        return render(request, "studio/navigation.html", context, status=400)

    capability = request.studio_principal.capability  # type: ignore[attr-defined]
    try:
        capability.service(
            entries=entries,
            expected_revision=expected_revision,
            source="studio",
            idempotency_key=raw_idempotency,
            actor_ref=f"user:{request.user.pk}",
            actor_id=request.user.pk,
            context=_service_context(request),
        )
    except SiteNavigationRevisionConflict:
        _audit_site_navigation_denial(request, reason="revision_conflict")
        context = _site_navigation_page_context(
            request,
            submitted_entries=entries,
            idempotency_key=raw_idempotency,
            error_message="The navigation changed in another session. Review and save again.",
            field_errors={"form": "The submitted revision is stale."},
        )
        return render(request, "studio/navigation.html", context, status=409)
    except (IdempotencyConflict, IdempotencyInProgress):
        _audit_site_navigation_denial(request, reason="idempotency_conflict")
        context = _site_navigation_page_context(
            request,
            submitted_entries=entries,
            idempotency_key=raw_idempotency,
            error_message="This save request conflicts with an earlier submission.",
            field_errors={"form": "Reload the page before trying again."},
        )
        return render(request, "studio/navigation.html", context, status=409)
    except InvalidSiteNavigation as error:
        _audit_site_navigation_denial(request, reason="invalid_request")
        context = _site_navigation_page_context(
            request,
            submitted_entries=entries,
            idempotency_key=raw_idempotency,
            error_message="Correct the highlighted navigation entries and try again.",
            field_errors=error.fields
            or {"entries": "Enter a complete valid menu of 1 to 12 entries."},
        )
        return render(request, "studio/navigation.html", context, status=400)
    except Exception:
        _audit_site_navigation_denial(request, reason="internal_error")
        return HttpResponse("Site navigation could not be saved", status=500)
    return HttpResponseRedirect(f"{reverse('studio:navigation')}?saved=1")


def site_navigation(request: HttpRequest) -> HttpResponse:
    if request.method in {"GET", "HEAD"}:
        return site_navigation_read(request)
    return site_navigation_write(request)


site_navigation.management_capability_keys = (  # type: ignore[attr-defined]
    "site.navigation.read",
    "site.navigation.write",
)
site_navigation.management_capability_views = {  # type: ignore[attr-defined]
    "GET": site_navigation_read,
    "POST": site_navigation_write,
}


def _sponsor_can(request: HttpRequest, key: str) -> bool:
    try:
        authorize_studio_request(
            request_user=request.user,
            session_reference=session_reference(request),
            capability=CAPABILITY_REGISTRY.require(key),
        )
    except (StudioAuthenticationRequired, StudioAuthorizationDenied):
        return False
    return True


def _sponsor_query(request: HttpRequest):
    from types import SimpleNamespace

    filters = {
        name: value
        for name in SPONSOR_FILTER_FIELDS
        if (value := request.GET.get(name, "").strip())
    }
    raw_sort = request.GET.get("sort", "").strip()
    sort = tuple(part for part in raw_sort.split(",") if part) if raw_sort else ()
    try:
        page = int(request.GET.get("page", "1"))
        page_size = int(request.GET.get("page_size", "20"))
    except ValueError as error:
        raise InvalidSponsor("page is invalid") from error
    return SimpleNamespace(page=page, page_size=page_size, sort=sort, filters=filters)


def _sponsor_form_payload(post) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": post.get("name", ""),
        "url": post.get("url", ""),
        "tagline": post.get("tagline", ""),
        "lifecycle": post.get("lifecycle", "draft"),
    }
    if "key" in post:
        payload["key"] = post.get("key", "")
    placement = (post.get("placement") or "").strip()
    if placement:
        raw_position = post.get("position", "1")
        try:
            position = int(raw_position)
        except (TypeError, ValueError):
            position = 0
        payload["assignments"] = [
            {
                "placement": placement,
                "position": position,
                "enabled": post.get("assignment_enabled") == "true",
            }
        ]
    else:
        payload["assignments"] = []
    return payload


def _sponsor_submitted(post) -> dict[str, object]:
    return {
        "key": post.get("key", ""),
        "name": post.get("name", ""),
        "url": post.get("url", ""),
        "tagline": post.get("tagline", ""),
        "lifecycle": post.get("lifecycle", "draft"),
        "placement": post.get("placement", ""),
        "position": post.get("position", "1"),
        "assignment_enabled": post.get("assignment_enabled") == "true",
    }


def _sponsor_list_context(
    request: HttpRequest,
    *,
    submitted: dict[str, object] | None = None,
    idempotency_key: object | None = None,
    export_idempotency_key: object | None = None,
    error_message: str = "",
    field_errors: dict[str, str] | None = None,
) -> dict[str, object]:
    result = CAPABILITY_REGISTRY.require("site.sponsors.read").service(
        _sponsor_query(request),
        context=_service_context(request),
    )
    return {
        "sponsors": result["items"],
        "page": result["page"],
        "page_size": result["page_size"],
        "total_count": result["total_count"],
        "filters": request.GET,
        "placements": SPONSOR_PLACEMENTS,
        "submitted": submitted or {},
        "can_write": _sponsor_can(request, "site.sponsors.write"),
        "can_export": _sponsor_can(request, "site.sponsors.export"),
        "idempotency_key": idempotency_key or uuid.uuid4(),
        "export_idempotency_key": export_idempotency_key or uuid.uuid4(),
        "error_message": error_message,
        "field_errors": field_errors or {},
        "saved": request.GET.get("saved") == "1",
        "studio_navigation": _navigation(request),
    }


def _sponsor_detail_context(
    request: HttpRequest,
    sponsor: dict[str, object],
    *,
    submitted: dict[str, object] | None = None,
    idempotency_key: object | None = None,
    error_message: str = "",
    field_errors: dict[str, str] | None = None,
) -> dict[str, object]:
    assignments = sponsor.get("assignments")
    current = assignments[0] if isinstance(assignments, list) and assignments else {}
    defaults = {
        "name": sponsor.get("name", ""),
        "url": sponsor.get("url", ""),
        "tagline": sponsor.get("tagline", ""),
        "lifecycle": sponsor.get("lifecycle", "draft"),
        "placement": current.get("placement", "") if isinstance(current, dict) else "",
        "position": current.get("position", 1) if isinstance(current, dict) else 1,
        "assignment_enabled": (
            current.get("enabled", False) if isinstance(current, dict) else False
        ),
    }
    return {
        "sponsor": sponsor,
        "submitted": submitted or defaults,
        "placements": SPONSOR_PLACEMENTS,
        "can_write": _sponsor_can(request, "site.sponsors.update"),
        "idempotency_key": idempotency_key or uuid.uuid4(),
        "error_message": error_message,
        "field_errors": field_errors or {},
        "saved": request.GET.get("saved") == "1",
        "studio_navigation": _navigation(request),
    }


def _sponsor_error_message(error: Exception) -> tuple[str, dict[str, str], int]:
    if isinstance(error, SponsorRevisionConflict):
        return (
            "This sponsor changed in another session. Review and save again.",
            {"form": "A submitted revision is stale."},
            409,
        )
    if isinstance(error, (IdempotencyConflict, IdempotencyInProgress)):
        return (
            "This save request conflicts with an earlier submission.",
            {"form": "Reload the page before trying again."},
            409,
        )
    if isinstance(error, InvalidSponsor):
        return (
            "Correct the highlighted fields and try again.",
            error.fields or {"form": "The sponsor form is invalid."},
            400,
        )
    if isinstance(error, (TypeError, ValueError)):
        return (
            "Correct the highlighted fields and try again.",
            {"form": "The sponsor form is invalid."},
            400,
        )
    raise error


@capability_required("site.sponsors.read")
def sponsor_list_read(request: HttpRequest) -> HttpResponse:
    try:
        context = _sponsor_list_context(request)
    except Exception:
        return HttpResponse("Sponsors are unavailable", status=500)
    return render(request, "studio/sponsors.html", context)


@capability_required("site.sponsors.write")
def sponsor_list_write(request: HttpRequest) -> HttpResponse:
    if not _sponsor_can(request, "site.sponsors.read"):
        return HttpResponseForbidden("Studio access denied")
    raw_idempotency = request.POST.get("idempotency_key", "")
    try:
        parsed = uuid.UUID(raw_idempotency)
        if str(parsed) != raw_idempotency:
            raise ValueError("non-canonical idempotency key")
    except (AttributeError, TypeError, ValueError):
        context = _sponsor_list_context(
            request,
            submitted=_sponsor_submitted(request.POST),
            idempotency_key=uuid.uuid4(),
            error_message="Correct the highlighted fields and try again.",
            field_errors={"form": "The sponsor form state is invalid."},
        )
        return render(request, "studio/sponsors.html", context, status=400)
    capability = request.studio_principal.capability  # type: ignore[attr-defined]
    try:
        result = capability.service(
            payload=_sponsor_form_payload(request.POST),
            source="studio",
            idempotency_key=raw_idempotency,
            actor_ref=f"user:{request.user.pk}",
            actor_id=request.user.pk,
            context=_service_context(request),
        )
    except (
        InvalidSponsor,
        IdempotencyConflict,
        IdempotencyInProgress,
        TypeError,
        ValueError,
    ) as error:
        message, fields, status = _sponsor_error_message(error)
        context = _sponsor_list_context(
            request,
            submitted=_sponsor_submitted(request.POST),
            idempotency_key=raw_idempotency,
            error_message=message,
            field_errors=fields,
        )
        return render(request, "studio/sponsors.html", context, status=status)
    except Exception:
        return HttpResponse("Sponsors could not be saved", status=500)
    return HttpResponseRedirect(
        f"{reverse('studio:sponsor-detail', args=[result.sponsor['id']])}?saved=1"
    )


def sponsors(request: HttpRequest) -> HttpResponse:
    if request.method in {"GET", "HEAD"}:
        return sponsor_list_read(request)
    return sponsor_list_write(request)


sponsors.management_capability_keys = (  # type: ignore[attr-defined]
    "site.sponsors.read",
    "site.sponsors.write",
)
sponsors.management_capability_views = {  # type: ignore[attr-defined]
    "GET": sponsor_list_read,
    "POST": sponsor_list_write,
}


@capability_required("site.sponsors.detail")
def sponsor_detail_read(request: HttpRequest, sponsor_id: uuid.UUID) -> HttpResponse:
    capability = request.studio_principal.capability  # type: ignore[attr-defined]
    try:
        sponsor = capability.service(sponsor_id, context=_service_context(request))
    except Exception:
        return HttpResponse("Sponsor is unavailable", status=500)
    if sponsor is None:
        return HttpResponse("Sponsor unavailable", status=404)
    return render(request, "studio/sponsor_detail.html", _sponsor_detail_context(request, sponsor))


@capability_required("site.sponsors.update")
def sponsor_detail_write(request: HttpRequest, sponsor_id: uuid.UUID) -> HttpResponse:
    if not _sponsor_can(request, "site.sponsors.detail"):
        return HttpResponseForbidden("Studio access denied")
    raw_idempotency = request.POST.get("idempotency_key", "")
    try:
        parsed = uuid.UUID(raw_idempotency)
        if str(parsed) != raw_idempotency:
            raise ValueError("non-canonical idempotency key")
        expected_revision = int(request.POST.get("expected_revision", ""))
        if expected_revision < 1:
            raise ValueError("invalid revision")
    except (AttributeError, TypeError, ValueError):
        current = CAPABILITY_REGISTRY.require("site.sponsors.detail").service(
            sponsor_id,
            context=_service_context(request),
        )
        if current is None:
            return HttpResponse("Sponsor unavailable", status=404)
        context = _sponsor_detail_context(
            request,
            current,
            submitted=_sponsor_submitted(request.POST),
            idempotency_key=uuid.uuid4(),
            error_message="Correct the highlighted fields and try again.",
            field_errors={"form": "The sponsor form state is invalid."},
        )
        return render(request, "studio/sponsor_detail.html", context, status=400)
    capability = request.studio_principal.capability  # type: ignore[attr-defined]
    try:
        capability.service(
            sponsor_id=sponsor_id,
            payload=_sponsor_form_payload(request.POST),
            expected_revision=expected_revision,
            source="studio",
            idempotency_key=raw_idempotency,
            actor_ref=f"user:{request.user.pk}",
            actor_id=request.user.pk,
            context=_service_context(request),
        )
    except SponsorNotFound:
        return HttpResponse("Sponsor unavailable", status=404)
    except (
        InvalidSponsor,
        SponsorRevisionConflict,
        IdempotencyConflict,
        IdempotencyInProgress,
        TypeError,
        ValueError,
    ) as error:
        current = CAPABILITY_REGISTRY.require("site.sponsors.detail").service(
            sponsor_id,
            context=_service_context(request),
        )
        if current is None:
            return HttpResponse("Sponsor unavailable", status=404)
        message, fields, status = _sponsor_error_message(error)
        context = _sponsor_detail_context(
            request,
            current,
            submitted=_sponsor_submitted(request.POST),
            idempotency_key=raw_idempotency,
            error_message=message,
            field_errors=fields,
        )
        return render(request, "studio/sponsor_detail.html", context, status=status)
    except Exception:
        return HttpResponse("Sponsors could not be saved", status=500)
    return HttpResponseRedirect(f"{reverse('studio:sponsor-detail', args=[sponsor_id])}?saved=1")


def sponsor_detail(request: HttpRequest, sponsor_id: uuid.UUID) -> HttpResponse:
    if request.method in {"GET", "HEAD"}:
        return sponsor_detail_read(request, sponsor_id)
    return sponsor_detail_write(request, sponsor_id)


sponsor_detail.management_capability_keys = (  # type: ignore[attr-defined]
    "site.sponsors.detail",
    "site.sponsors.update",
)
sponsor_detail.management_capability_views = {  # type: ignore[attr-defined]
    "GET": sponsor_detail_read,
    "POST": sponsor_detail_write,
}


def _sponsor_lifecycle_action(
    request: HttpRequest,
    sponsor_id: uuid.UUID,
    *,
    capability_key: str,
) -> HttpResponse:
    raw_idempotency = request.POST.get("idempotency_key", "")
    try:
        parsed = uuid.UUID(raw_idempotency)
        if str(parsed) != raw_idempotency:
            raise ValueError("non-canonical idempotency key")
        expected_revision = int(request.POST.get("expected_revision", ""))
        if expected_revision < 1:
            raise ValueError("invalid revision")
    except (AttributeError, TypeError, ValueError):
        current = CAPABILITY_REGISTRY.require("site.sponsors.detail").service(
            sponsor_id,
            context=_service_context(request),
        )
        if current is None:
            return HttpResponse("Sponsor unavailable", status=404)
        context = _sponsor_detail_context(
            request,
            current,
            idempotency_key=uuid.uuid4(),
            error_message="Confirm the lifecycle change before continuing.",
            field_errors={"confirmed": "Confirm this action before continuing."},
        )
        return render(request, "studio/sponsor_detail.html", context, status=400)
    capability = request.studio_principal.capability  # type: ignore[attr-defined]
    try:
        capability.service(
            sponsor_id=sponsor_id,
            confirmed=request.POST.get("confirmed") == "true",
            expected_revision=expected_revision,
            source="studio",
            idempotency_key=raw_idempotency,
            actor_ref=f"user:{request.user.pk}",
            actor_id=request.user.pk,
            context=_service_context(request),
        )
    except SponsorNotFound:
        return HttpResponse("Sponsor unavailable", status=404)
    except (
        InvalidSponsor,
        SponsorRevisionConflict,
        IdempotencyConflict,
        IdempotencyInProgress,
        TypeError,
        ValueError,
    ) as error:
        current = CAPABILITY_REGISTRY.require("site.sponsors.detail").service(
            sponsor_id,
            context=_service_context(request),
        )
        if current is None:
            return HttpResponse("Sponsor unavailable", status=404)
        message, fields, status = _sponsor_error_message(error)
        context = _sponsor_detail_context(
            request,
            current,
            idempotency_key=raw_idempotency,
            error_message=message,
            field_errors=fields,
        )
        return render(request, "studio/sponsor_detail.html", context, status=status)
    except Exception:
        return HttpResponse("Sponsors could not be saved", status=500)
    return HttpResponseRedirect(f"{reverse('studio:sponsor-detail', args=[sponsor_id])}?saved=1")


@capability_required("site.sponsors.archive")
def sponsor_archive(request: HttpRequest, sponsor_id: uuid.UUID) -> HttpResponse:
    return _sponsor_lifecycle_action(
        request,
        sponsor_id,
        capability_key="site.sponsors.archive",
    )


@capability_required("site.sponsors.reactivate")
def sponsor_reactivate(request: HttpRequest, sponsor_id: uuid.UUID) -> HttpResponse:
    return _sponsor_lifecycle_action(
        request,
        sponsor_id,
        capability_key="site.sponsors.reactivate",
    )


@capability_required("site.sponsors.export")
def sponsor_export(request: HttpRequest) -> HttpResponse:
    raw_idempotency = request.POST.get("idempotency_key", "")
    try:
        parsed = uuid.UUID(raw_idempotency)
        if str(parsed) != raw_idempotency:
            raise ValueError("non-canonical idempotency key")
    except (AttributeError, TypeError, ValueError):
        context = _sponsor_list_context(
            request,
            export_idempotency_key=uuid.uuid4(),
            error_message="Confirm the export before continuing.",
            field_errors={"form": "The export form state is invalid."},
        )
        return render(request, "studio/sponsors.html", context, status=400)
    filters = {
        name: value
        for name in ("lifecycle", "placement")
        if (value := request.POST.get(name, "").strip())
    }
    capability = request.studio_principal.capability  # type: ignore[attr-defined]
    try:
        result = capability.service(
            confirmed=request.POST.get("confirmed") == "true",
            reason=request.POST.get("reason", ""),
            filters=filters,
            idempotency_key=raw_idempotency,
            actor_ref=f"user:{request.user.pk}",
            actor_id=request.user.pk,
            context=_service_context(request),
        )
    except (
        InvalidSponsor,
        IdempotencyConflict,
        IdempotencyInProgress,
        TypeError,
        ValueError,
    ) as error:
        message, fields, status = _sponsor_error_message(error)
        context = _sponsor_list_context(
            request,
            export_idempotency_key=raw_idempotency,
            error_message=message,
            field_errors=fields,
        )
        return render(request, "studio/sponsors.html", context, status=status)
    except Exception:
        return HttpResponse("Sponsors could not be exported", status=500)
    response = HttpResponse(result.csv, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{result.filename}"'
    return response


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


@capability_required("events.identity.read")
def event_identity_list(request: HttpRequest) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(("GET", "HEAD"))
    try:
        result = list_event_identities(page=1, page_size=100)
        _audit_identity_access(request)
    except (TypeError, ValueError):
        return HttpResponse("Event identities unavailable", status=500)
    return render(
        request,
        "studio/event_identities.html",
        {"identities": result["items"], "studio_navigation": _navigation(request)},
    )


@capability_required("events.identity.detail")
def event_identity_detail(request: HttpRequest, event_id: uuid.UUID) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(("GET", "HEAD"))
    try:
        identity = get_event_identity(event_id)
        _audit_identity_access(request, event_id=event_id)
    except EventIdentityNotFound:
        return HttpResponse("Event identity unavailable", status=404)
    return render(
        request,
        "studio/event_identity_detail.html",
        {"identity": identity, "studio_navigation": _navigation(request)},
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
                "event_id": request.POST.get("event_id", ""),
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
    identity_listing = list_event_identities(page=1, page_size=100)
    event_identities = list(identity_listing["items"])
    while len(event_identities) < identity_listing["total_count"]:
        identity_listing = list_event_identities(
            page=identity_listing["page"] + 1,
            page_size=identity_listing["page_size"],
        )
        event_identities.extend(identity_listing["items"])
    return render(
        request,
        "studio/historical_registration_mappings.html",
        {
            "mappings": listing["items"],
            "event_identities": event_identities,
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
    event_id: uuid.UUID,
) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(("GET", "HEAD"))
    selected = _event_actor(request, "events.historical_registration_total.read")
    if isinstance(selected, HttpResponse):
        return selected
    try:
        preview = selected.capability.service(event_id)
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


def course_registration_count_list(request: HttpRequest) -> HttpResponse:
    if request.method not in {"GET", "HEAD", "POST"}:
        return HttpResponseNotAllowed(("GET", "HEAD", "POST"))
    capability_key = (
        "courses.registration_count_baseline.create"
        if request.method == "POST"
        else "courses.registration_count_baseline.manage"
    )
    selected = _event_actor(request, capability_key)
    if isinstance(selected, HttpResponse):
        return selected
    error_message = ""
    status = 200
    if request.method == "POST":
        try:
            if request.POST.get("confirmed") != "true":
                raise CourseRegistrationCountInvalid("confirmation_required")
            source_reference = request.POST.get("source_reference", "")
            reason_code = request.POST.get("reason_code", "")
            idempotency_key = request.POST.get("idempotency_key", "")

            def command() -> dict:
                run, replayed = selected.capability.service(
                    source_reference=source_reference,
                    reason_code=reason_code,
                    actor=selected.user,
                    context=_studio_event_context(
                        request,
                        idempotency_key=idempotency_key,
                    ),
                )
                return {"run_id": str(run.id), "replayed": replayed}

            result = execute_idempotent(
                scope=selected.capability.key,
                key=idempotency_key,
                request={
                    "source_reference": source_reference,
                    "reason_code": reason_code,
                    "confirmed": True,
                },
                command=command,
            )
            return HttpResponseRedirect(
                reverse(
                    "studio:course-registration-count-detail",
                    kwargs={"run_id": result.value["run_id"]},
                )
            )
        except Exception as error:
            error_message, status = _course_count_studio_error(error)
    listing = CAPABILITY_REGISTRY.require("courses.registration_count_baseline.manage").service(
        page=1, page_size=100
    )
    return render(
        request,
        "studio/course_registration_count_list.html",
        {
            "runs": listing["items"],
            "idempotency_key": uuid.uuid4(),
            "error_message": error_message,
            "studio_navigation": _navigation(request),
        },
        status=status,
    )


def course_registration_count_detail(request: HttpRequest, run_id: uuid.UUID) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(("GET", "HEAD"))
    selected = _event_actor(request, "courses.registration_count_baseline.detail")
    if isinstance(selected, HttpResponse):
        return selected
    try:
        detail = selected.capability.service(run_id)
    except CourseRegistrationCountSourceRun.DoesNotExist:
        return HttpResponse("Course count source unavailable", status=404)
    return render(
        request,
        "studio/course_registration_count_detail.html",
        {
            "run": detail,
            "actions": ("dry-run", "validate", "activate", "cancel", "rollback"),
            "idempotency_key": uuid.uuid4(),
            "studio_navigation": _navigation(request),
        },
    )


def course_registration_count_action(
    request: HttpRequest,
    run_id: uuid.UUID,
    action: str,
) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(("POST",))
    if action not in {"dry-run", "validate", "activate", "cancel", "rollback"}:
        return HttpResponse("Course count action unavailable", status=404)
    selected = _event_actor(
        request,
        f"courses.registration_count_baseline.{action}",
        target_id=run_id,
    )
    if isinstance(selected, HttpResponse):
        return selected
    try:
        if request.POST.get("confirmed") != "true":
            raise CourseRegistrationCountInvalid("confirmation_required")
        expected_revision = int(request.POST.get("expected_revision", "0"))
        reason_code = request.POST.get("reason_code", "")
        idempotency_key = request.POST.get("idempotency_key", "")

        def command() -> dict:
            kwargs = {
                "expected_revision": expected_revision,
                "actor": selected.user,
                "context": _studio_event_context(
                    request,
                    idempotency_key=idempotency_key,
                ),
                "reason_code": reason_code,
            }
            value = selected.capability.service(run_id, **kwargs)
            return value if isinstance(value, dict) else serialize_course_count_run(value)

        execute_idempotent(
            scope=selected.capability.key,
            key=idempotency_key,
            request={
                "run_id": str(run_id),
                "action": action,
                "confirmed": True,
                "reason_code": reason_code,
                "expected_revision": expected_revision,
            },
            command=command,
        )
    except Exception as error:
        message, status = _course_count_studio_error(error)
        try:
            detail = CAPABILITY_REGISTRY.require(
                "courses.registration_count_baseline.detail"
            ).service(run_id)
        except CourseRegistrationCountSourceRun.DoesNotExist:
            return HttpResponse("Course count source unavailable", status=404)
        return render(
            request,
            "studio/course_registration_count_detail.html",
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
        reverse("studio:course-registration-count-detail", kwargs={"run_id": run_id})
    )


def course_registration_count_total(
    request: HttpRequest,
    campaign_slug: str,
) -> HttpResponse:
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(("GET", "HEAD"))
    selected = _event_actor(request, "courses.registration_count_baseline.total")
    if isinstance(selected, HttpResponse):
        return selected
    try:
        preview = selected.capability.service(campaign_slug)
    except CourseRegistrationCountInvalid:
        return HttpResponse("Course registration total unavailable", status=404)
    return render(
        request,
        "studio/course_registration_count_total.html",
        {"total": preview, "studio_navigation": _navigation(request)},
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
