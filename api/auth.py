from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, JsonResponse


def staff_json_required(view: Callable[..., JsonResponse]) -> Callable[..., JsonResponse]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        if not request.user.is_authenticated:
            return JsonResponse(
                {
                    "error": {
                        "code": "authentication_required",
                        "message": "Authentication required",
                    }
                },
                status=401,
            )
        if not request.user.is_active or not request.user.is_staff:
            return JsonResponse(
                {"error": {"code": "permission_denied", "message": "Permission denied"}},
                status=403,
            )
        return view(request, *args, **kwargs)

    return wrapped
