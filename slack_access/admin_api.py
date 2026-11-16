"""Bearer-only JSON adapters; the domain owns authorization and effects."""

from community_base.kernel.services import ServiceContext
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

from core.idempotency import IdempotencyConflict, IdempotencyInProgress
from management_api.dispatch import admin_capability
from management_api.errors import APIError
from management_api.json_input import parse_json_object
from management_registry import CAPABILITY_REGISTRY

from .management_services import SlackManagementError


def _invoke(request, key, payload):
    identity = request.api_identity
    try:
        context = ServiceContext.from_current(
            actor_ref=f"api_principal:{identity.principal.pk}",
            idempotency_key=(
                request.headers.get("Idempotency-Key") if request.method == "POST" else None
            ),
        )
    except ValueError:
        raise APIError(
            400, "invalid_idempotency_key", "Use an idempotency key of 1 to 128 characters."
        ) from None
    try:
        return JsonResponse(CAPABILITY_REGISTRY.require(key).service(payload, context=context))
    except SlackManagementError as error:
        raise APIError(error.status, error.code, error.message) from None
    except PermissionDenied:
        raise APIError(403, "permission_denied", "Slack management permission is denied.") from None
    except (IdempotencyConflict, IdempotencyInProgress):
        raise APIError(
            409, "idempotency_conflict", "Use a new idempotency key or reload the result."
        ) from None
    except ValueError:
        raise APIError(400, "invalid_request", "The management request is invalid.") from None


def _body(request, identifier, value):
    payload = parse_json_object(request)
    if identifier in payload:
        raise APIError(400, "invalid_fields", "The route owns the target identifier.")
    return {**payload, identifier: str(value)}


def _query(request):
    if any(len(values) != 1 for _, values in request.GET.lists()):
        raise APIError(400, "invalid_query", "Use each query parameter only once.")
    return request.GET.dict()


@admin_capability("slack.applications.list")
def application_list(request):
    return _invoke(request, "slack.applications.list", _query(request))


@admin_capability("slack.applications.review")
def application_review(request, application_id):
    return _invoke(
        request, "slack.applications.review", _body(request, "application_id", application_id)
    )


@admin_capability("slack.members.preview")
def member_preview(request, member_id):
    if request.GET:
        raise APIError(400, "invalid_fields", "The preview accepts only the route identifier.")
    return _invoke(request, "slack.members.preview", {"member_id": str(member_id)})


@admin_capability("slack.members.ban")
def member_ban(request, member_id):
    return _invoke(request, "slack.members.ban", _body(request, "member_id", member_id))


@admin_capability("slack.joins.report")
def join_report(request):
    return _invoke(request, "slack.joins.report", _query(request))
