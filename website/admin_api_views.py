from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from api.auth import staff_json_required


@staff_json_required
@require_GET
def admin_health(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "status": "ok",
            "version": settings.APP_VERSION,
            "actor": getattr(request.user, "email", ""),
        }
    )
