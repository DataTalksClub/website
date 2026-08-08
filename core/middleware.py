from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.middleware.common import CommonMiddleware

from core.context import (
    CONTEXT_ID_PATTERN,
    bind_context,
    external_context_id_or_new,
    is_safe_external_context_id,
    reset_context,
)

REQUEST_ID_PATTERN = CONTEXT_ID_PATTERN
PRIVATE_PREFIXES = (
    "/accounts/",
    "/admin/",
    "/api/",
    "/auth/logout/",
    "/cadmin/",
    "/studio/",
)
PRIVATE_ROUTE_NAMES = frozenset(
    {
        "dashboard",
        "enrollment",
        "homework_submissions",
        "leaderboard_complaint",
        "leaderboard_score_breakdown",
        "project_submissions",
        "project_results",
        "projects_eval",
        "projects_eval_add",
        "projects_eval_delete",
        "projects_eval_submit",
        "registration_campaign",
        "update_enrollment_toggle",
    }
)
ALB_READINESS_PATH = "/health/ready"
ROBOTS_HEADER_VALUE = "noindex, nofollow"


class ReadinessProbeCommonMiddleware(CommonMiddleware):
    """Skip dynamic ALB target-host validation only for the readiness probe."""

    def process_request(self, request: HttpRequest) -> HttpResponsePermanentRedirect | None:
        if request.path_info == ALB_READINESS_PATH:
            return None
        return super().process_request(request)


class RequestIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = external_context_id_or_new(supplied)
        supplied_correlation = request.headers.get("X-Correlation-ID", "")
        correlation_id = (
            supplied_correlation
            if is_safe_external_context_id(supplied_correlation)
            else request_id
        )
        request.request_id = request_id  # type: ignore[attr-defined]
        request.correlation_id = correlation_id  # type: ignore[attr-defined]
        tokens = bind_context(request_id=request_id, correlation_id=correlation_id)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            response["X-Correlation-ID"] = correlation_id
            return response
        finally:
            reset_context(tokens)


def _is_authenticated_request(request: HttpRequest) -> bool:
    user = getattr(request, "user", None)
    if bool(user is not None and user.is_authenticated):
        return True
    # Fail closed when an earlier middleware short-circuits before Django can
    # resolve the principal (notably SecurityMiddleware and WhiteNoise).
    if request.headers.get("Authorization"):
        return True
    session_cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
    return session_cookie_name in request.COOKIES


def _is_private_surface(request: HttpRequest) -> bool:
    if getattr(request, "private_response_required", False):
        return True
    if any(
        request.path == prefix.removesuffix("/") or request.path.startswith(prefix)
        for prefix in PRIVATE_PREFIXES
    ):
        return True
    match = getattr(request, "resolver_match", None)
    if match is None:
        return False
    if match.url_name in PRIVATE_ROUTE_NAMES:
        return True
    # Adopted assignment pages remain readable anonymously, but any mutating
    # request is learner-specific even when its view returns a safe denial.
    return request.method not in {"GET", "HEAD", "OPTIONS"} and match.url_name in {
        "homework",
        "project",
    }


def apply_private_no_store(response: HttpResponse) -> None:
    """Overwrite shared-cache directives while retaining harmless directives."""

    retained: list[str] = []
    for raw_directive in response.headers.get("Cache-Control", "").split(","):
        directive = raw_directive.strip()
        name = directive.partition("=")[0].strip().casefold()
        if not directive or name in {"public", "private", "no-store", "max-age", "s-maxage"}:
            continue
        retained.append(directive)
    retained.extend(("private", "no-store", "max-age=0"))
    response["Cache-Control"] = ", ".join(retained)


class ResponsePolicyMiddleware:
    """Apply environment and privacy policy after every inner response path."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        private_surface = _is_private_surface(request)
        if settings.NOINDEX or private_surface:
            # Assignment replaces a downstream value instead of appending a second field.
            response["X-Robots-Tag"] = ROBOTS_HEADER_VALUE
        if _is_authenticated_request(request) or private_surface:
            apply_private_no_store(response)
        return response
