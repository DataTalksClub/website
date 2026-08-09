from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from django.views.decorators.http import require_GET

from accounts.navigation import (
    request_uses_canonical_account_host,
    safe_next_path,
)
from course_management.observability import record_event


@require_GET
def explicit_reauthentication(request):
    """Move between cookie hosts without placing a credential in the URL."""

    destination = safe_next_path(request)
    authenticated = bool(
        request.user.is_authenticated
        and request.user.is_active
        and request_uses_canonical_account_host(request)
    )
    if authenticated:
        response = HttpResponseRedirect(destination)
    else:
        query = urlencode({"next": destination})
        origin = settings.ACCOUNT_CANONICAL_ORIGIN.rstrip("/")
        response = HttpResponseRedirect(
            f"{origin}/accounts/login/?{query}"
        )
    response["Referrer-Policy"] = "same-origin"
    record_event(
        "auth.explicit_reauthentication",
        request=request,
        properties={
            "credential_handoff": False,
            "destination_preserved": True,
            "reauthentication_required": not authenticated,
        },
    )
    return response
