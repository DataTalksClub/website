"""Staff HTML adapters over the same registered services as the management API."""

import uuid

from community_base.kernel.services import ServiceContext
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.capabilities import ServiceKind
from core.idempotency import IdempotencyConflict, IdempotencyInProgress
from management_registry import CAPABILITY_REGISTRY
from studio.auth import audit_capability_denial, capability_required

from .management_services import SlackManagementError
from .rate_limits import business_rate
from .views import private


def _invoke(request, key, payload):
    for source in (request.GET, request.POST):
        if any(len(values) != 1 for _, values in source.lists()):
            raise SlackManagementError(400, "invalid_request", "Use each field once.")
    if request.method == "POST":
        allowed = {"csrfmiddlewaretoken", "idempotency_key"}
        if key == "slack.applications.review":
            allowed |= {"application_id", "action", "expected_state"}
        elif key == "slack.members.ban":
            allowed |= {"member_id", "confirmed", "expected_count", "scope_token"}
        if set(request.POST) - allowed:
            raise SlackManagementError(400, "invalid_request", "Unsupported form field.")
    context = ServiceContext.from_current(
        actor_ref=f"user:{request.user.pk}",
        idempotency_key=request.POST.get("idempotency_key") if request.method == "POST" else None,
    )
    return CAPABILITY_REGISTRY.require(key).service(payload, context=context)


def _error(error, request):
    principal = getattr(request, "studio_principal", None)
    if principal and principal.capability.service_kind is ServiceKind.COMMAND:
        audit_capability_denial(
            request,
            capability_key=principal.capability.key,
            reason=(
                error.code
                if isinstance(error, SlackManagementError)
                else "permission_denied"
                if isinstance(error, PermissionDenied)
                else "idempotency_conflict"
                if isinstance(error, (IdempotencyConflict, IdempotencyInProgress))
                else "invalid_request"
            ),
            idempotency_key=request.POST.get("idempotency_key", ""),
        )
    if isinstance(error, PermissionDenied):
        return HttpResponse("Slack management permission is denied.", status=403)
    if isinstance(error, SlackManagementError):
        return HttpResponse(error.message, status=error.status)
    if isinstance(error, ValueError):
        return HttpResponse("Invalid management request.", status=400)
    return HttpResponse(
        "This command conflicts with an earlier submission. Reload and review.", status=409
    )


def _integer(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


@private
@capability_required("slack.applications.list")
@require_http_methods(["GET", "HEAD"])
def review(request):
    try:
        result = _invoke(
            request, "slack.applications.list", {"state": "pending", **request.GET.dict()}
        )
    except SlackManagementError as error:
        return _error(error, request)
    return render(
        request,
        "slack_access/review.html",
        {
            "applications": result["applications"],
            "next_after": result["next_after"],
            "idempotency_key": uuid.uuid4(),
        },
    )


@private
@capability_required("slack.applications.review")
@business_rate
@require_http_methods(["POST"])
def review_action(request):
    try:
        _invoke(
            request,
            "slack.applications.review",
            {
                "application_id": request.POST.get("application_id"),
                "action": request.POST.get("action"),
                "expected_state": request.POST.get("expected_state"),
            },
        )
    except (
        SlackManagementError,
        IdempotencyConflict,
        IdempotencyInProgress,
        ValueError,
        PermissionDenied,
    ) as error:
        return _error(error, request)
    return redirect("studio:slack-review")


@private
@capability_required("slack.members.preview")
@require_http_methods(["GET", "HEAD"])
def moderation(request):
    preview = None
    if request.GET.get("member_id"):
        try:
            preview = _invoke(
                request,
                "slack.members.preview",
                {
                    "member_id": request.GET.get("member_id"),
                },
            )
        except SlackManagementError as error:
            return _error(error, request)
    return render(
        request,
        "slack_access/moderation.html",
        {
            "preview": preview,
            "idempotency_key": uuid.uuid4(),
        },
    )


@private
@capability_required("slack.members.ban")
@business_rate
@require_http_methods(["POST"])
def moderation_action(request):
    try:
        result = _invoke(
            request,
            "slack.members.ban",
            {
                "member_id": request.POST.get("member_id"),
                "confirmed": request.POST.get("confirmed") == "true",
                "expected_count": _integer(request.POST.get("expected_count")),
                "scope_token": request.POST.get("scope_token"),
            },
        )
    except (
        SlackManagementError,
        IdempotencyConflict,
        IdempotencyInProgress,
        ValueError,
        PermissionDenied,
    ) as error:
        return _error(error, request)
    return render(request, "slack_access/moderation.html", {"count": result["affected_count"]})


@private
@capability_required("slack.joins.report")
@require_http_methods(["GET", "HEAD"])
def report(request):
    try:
        result = _invoke(request, "slack.joins.report", request.GET.dict())
    except SlackManagementError as error:
        return _error(error, request)
    return render(
        request,
        "slack_access/report.html",
        {
            "observations": result["joins"],
            "monitor": result["monitor"],
            "next_after": result["next_after"],
        },
    )
