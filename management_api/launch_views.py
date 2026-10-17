"""Authenticated JSON adapter over the launch domain."""

from django.http import JsonResponse

from core.idempotency import IdempotencyConflict, IdempotencyInProgress
from core.services import ServiceContext
from courses.services.launch_validation import LAUNCH_IDEMPOTENCY_KEY_MAX, LaunchError
from management_api.concurrency import require_navigation_if_match
from management_api.dispatch import admin_capability
from management_api.errors import APIError
from management_api.json_input import parse_json_object
from management_registry import CAPABILITY_REGISTRY


def invoke(request, key, payload):
    capability = CAPABILITY_REGISTRY.require(key)
    identity = request.api_identity
    context = ServiceContext.from_current(
        actor_ref=f"api_principal:{identity.principal.pk}",
        idempotency_key=command_key(request),
    )
    try:
        if "expected_revision" in payload:
            if require_navigation_if_match(request) != payload["expected_revision"]:
                raise APIError(409, "revision_conflict", "If-Match and submitted revision differ.")
        return JsonResponse(capability.service(payload, context=context))
    except LaunchError as error:
        raise APIError(error.status, error.code, str(error)) from error
    except (IdempotencyConflict, IdempotencyInProgress) as error:
        raise APIError(
            409, "idempotency_conflict", "Reload the command or use a new idempotency key."
        ) from error


def command_key(request):
    if request.method != "POST":
        return None
    value = request.headers.get("Idempotency-Key", "")
    if not 1 <= len(value) <= LAUNCH_IDEMPOTENCY_KEY_MAX or value != value.strip():
        raise APIError(
            400, "invalid_idempotency_key", "Use an Idempotency-Key of 1 to 128 characters."
        )
    return value


@admin_capability("courses.launch.read")
def launch_read(request):
    if request.GET.get("resource") == "identity":
        identity = request.api_identity
        return JsonResponse(
            {
                "principal_id": str(identity.principal.pk),
                "kind": identity.principal.kind,
                "scopes": identity.credential.scopes,
            }
        )
    return invoke(request, "courses.launch.read", request.GET.dict())


@admin_capability("courses.launch.write")
def launch_write(request):
    return invoke(request, "courses.launch.write", parse_json_object(request))


@admin_capability("courses.launch.send")
def launch_send(request):
    return invoke(request, "courses.launch.send", parse_json_object(request))
