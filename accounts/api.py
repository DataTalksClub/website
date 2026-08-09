from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from accounts.auth import token_required


def _identity_payload(user):
    return {
        "account_id": user.pk,
        "identity_state": user.identity_state,
        "auth_user_model": "accounts.CustomUser",
    }


@require_GET
def current_account_identity(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return JsonResponse(
            {"error": "Authentication required"},
            status=401,
        )
    return JsonResponse(_identity_payload(request.user))


@token_required
@require_GET
def compatibility_account_identity(request):
    return JsonResponse(_identity_payload(request.user))
