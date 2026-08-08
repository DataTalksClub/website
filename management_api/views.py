from django.http import HttpRequest, JsonResponse

from core.services import ServiceContext

from .dispatch import admin_capability
from .errors import APIError, error_response


@admin_capability("studio.home.read")
def admin_health(request: HttpRequest) -> JsonResponse:
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    result = capability.service(
        None,
        context=ServiceContext.from_current(actor_ref=f"api_principal:{identity.principal.id}"),
    )
    return JsonResponse(result)


def admin_not_found(request: HttpRequest, path: str = "") -> JsonResponse:
    del path
    return error_response(
        request,
        APIError(404, "not_found", "The requested management resource was not found."),
    )


admin_not_found.management_api_exempt = True  # type: ignore[attr-defined]
