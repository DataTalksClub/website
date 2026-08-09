"""Fail-closed HTTP boundary for local content-review browsing."""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOCAL_SESSION_PATHS = frozenset(
    {
        "/admin/login/",
        "/admin/logout/",
        "/auth/logout/",
    }
)
PROVIDER_AUTH_PREFIXES = (
    "/accounts/github/",
    "/accounts/google/",
    "/accounts/slack/",
)


def _plain_response(message: str, *, status: int) -> HttpResponse:
    response = HttpResponse(message, status=status, content_type="text/plain; charset=utf-8")
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


class LocalReviewNoNetworkMiddleware:
    """Stop provider-backed and mutating requests before copied views run."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not getattr(settings, "LOCAL_REVIEW_OUTBOUND_NETWORK_DISABLED", False):
            return self.get_response(request)

        path = request.path
        if path == "/cadmin/cloudwatch/":
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated or not user.is_staff:
                return self.get_response(request)
            return _plain_response(
                "CloudWatch is disabled in local content review.",
                status=200,
            )
        if path.startswith(PROVIDER_AUTH_PREFIXES):
            return _plain_response(
                "External sign-in providers are disabled in local content review.",
                status=403,
            )
        if request.method not in SAFE_METHODS and path not in LOCAL_SESSION_PATHS:
            return _plain_response(
                "Mutating requests are disabled in local content review.",
                status=403,
            )
        return self.get_response(request)
