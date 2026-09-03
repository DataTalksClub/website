import json
import os
from collections.abc import Callable

from django.conf import settings
from django.core.handlers.wsgi import LimitedStream
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.middleware.common import CommonMiddleware

from core.context import (
    CONTEXT_ID_PATTERN,
    bind_context,
    external_context_id_or_new,
    is_safe_external_context_id,
    reset_context,
)
from core.security import MAX_REQUEST_BODY_BYTES, MAX_WEBHOOK_BODY_BYTES

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
        # One member's year in review, readable by that member and by staff.
        "user_wrapped",
    }
)
ALB_READINESS_PATH = "/health/ready"
ROBOTS_HEADER_VALUE = "noindex, nofollow"
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; "
        "media-src 'self'; frame-src https://www.youtube.com https://www.youtube-nocookie.com "
        "https://creators.spotify.com https://open.spotify.com"
    ),
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), "
        "payment=(), usb=()"
    ),
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
_CORS_HEADERS = frozenset(
    {
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-allow-headers",
        "access-control-allow-methods",
        "access-control-expose-headers",
        "access-control-max-age",
    }
)
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CREDENTIAL_HEADER_NAMES = frozenset(
    {
        "Authorization",
        "X-CSRFToken",
        "X-Preview-Token",
        "X-Management-Token",
    }
)
# These cookies are documented, non-credential preferences.  Every other
# viewer cookie is treated as unknown credential-like state and is private.
_ANONYMOUS_COOKIE_NAMES = frozenset({"browser_timezone", "dtc_analytics_consent"})


def apply_security_headers(response: HttpResponse) -> None:
    """Apply the non-identity browser policy to every response path."""

    for name, value in SECURITY_HEADERS.items():
        response.setdefault(name, value)
    # CORS is deny-by-default.  Route-specific trusted-origin behavior would
    # be an owning product decision, so no presentation route may accidentally
    # opt in merely by returning a header from a third-party helper.
    for name in tuple(response.headers):
        if name.casefold() in _CORS_HEADERS:
            del response[name]


def _sanitize_mutation_error(request: HttpRequest, response: HttpResponse) -> HttpResponse:
    """Remove attacker-controlled field names from API mutation errors."""

    if not request.path_info.startswith("/api/") or response.status_code < 400:
        return response
    if not response.headers.get("Content-Type", "").startswith("application/json"):
        return response
    try:
        payload = json.loads(response.content)
    except (TypeError, ValueError, UnicodeDecodeError):
        return response
    code = payload.get("code") if isinstance(payload, dict) else None
    error = payload.get("error") if isinstance(payload, dict) else None
    nested_code = error.get("code") if isinstance(error, dict) else None
    selected_code = code or nested_code
    if selected_code not in {"invalid_field", "invalid_date_format", "invalid_json"}:
        return response
    if isinstance(error, dict):
        error["message"] = "The request contains an invalid field."
        error.pop("fields", None)
    elif isinstance(payload, dict):
        payload["error"] = "The request contains an invalid field."
        payload.pop("details", None)
    replacement = JsonResponse(payload, status=response.status_code)
    for name in ("Allow", "WWW-Authenticate", "X-Request-ID", "X-Correlation-ID"):
        if name in response.headers:
            replacement[name] = response.headers[name]
    return replacement


class RequestBoundaryMiddleware:
    """Reject oversized bodies before parsers, auth, or domain services run."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        webhook_path = request.path_info.rstrip("/") == "/api/datamailer/events"
        # Let Django's existing @require_POST guard unsupported methods first.
        # This preserves the explicit 405/Allow contract without allowing an
        # unsupported request to reach webhook authentication or JSON parsing.
        if webhook_path and request.method != "POST":
            return self.get_response(request)

        if request.method in _BODY_METHODS:
            raw_length: str | bytes | int | None = request.META.get("CONTENT_LENGTH")
            if raw_length is None or raw_length == "":
                length_missing = True
                content_length = None
            else:
                length_missing = False
                try:
                    content_length = int(raw_length)
                except (TypeError, ValueError):
                    content_length = None
            if content_length is not None and content_length < 0:
                content_length = None
                length_missing = False
            limit = MAX_WEBHOOK_BODY_BYTES if webhook_path else MAX_REQUEST_BODY_BYTES
            if content_length is not None and content_length > limit:
                return self._too_large()
            stream_seekable, stream_size = self._seekable_stream_size(request)
            if stream_seekable:
                # ASGIHandler buffers into a seekable SpooledTemporaryFile.  A
                # client can still understate Content-Length, so compare the
                # buffered size before Django or an owning parser reads it.
                if stream_size is None or stream_size > limit:
                    return self._too_large()
                if content_length is not None and stream_size > content_length:
                    return self._too_large()
            elif content_length is None:
                # WSGI/streaming bodies without a trustworthy length cannot be
                # bounded without consuming an untrusted stream.  Reject them
                # before parsers; ASGI seekable bodies take the safe path above.
                stream = getattr(request, "_stream", None)
                if not (
                    length_missing
                    and isinstance(stream, LimitedStream)
                    and stream.limit == 0
                    and not request.META.get("HTTP_TRANSFER_ENCODING")
                ):
                    return self._too_large()

        if webhook_path:
            authorization = request.headers.get("Authorization", "")
            legacy_token = request.headers.get("X-Datamailer-Webhook-Token", "")
            if authorization and legacy_token:
                return self._webhook_rejected()
            if authorization:
                scheme, separator, token = authorization.partition(" ")
                if (
                    scheme.casefold() != "bearer"
                    or not separator
                    or not token
                    or token != token.strip()
                ):
                    return self._webhook_rejected()
            if request.content_type != "application/json":
                return self._webhook_rejected(status=415)
        return self.get_response(request)

    @staticmethod
    def _seekable_stream_size(request: HttpRequest) -> tuple[bool, int | None]:
        """Inspect and replay a seekable request stream without reading it.

        Django's ASGI handler hands middleware a seekable spooled file, while
        WSGI commonly exposes a non-seekable ``LimitedStream``.  Returning the
        seekability bit separately lets the caller fail closed for unknown
        lengths on streaming WSGI requests while preserving the normal trusted
        Content-Length path.
        """

        stream = getattr(request, "_stream", None)
        if stream is None:
            return False, None
        try:
            if not stream.seekable():
                return False, None
            position = stream.tell()
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(position)
        except (AttributeError, OSError, TypeError, ValueError):
            return True, None
        if not isinstance(size, int) or size < 0:
            return True, None
        return True, size

    @staticmethod
    def _too_large() -> JsonResponse:
        response = JsonResponse(
            {"error": "Request body exceeds the configured limit."},
            status=413,
        )
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response

    @staticmethod
    def _webhook_rejected(*, status: int = 401) -> JsonResponse:
        response = JsonResponse({"error": "Webhook request rejected."}, status=status)
        response["Cache-Control"] = "private, no-store, max-age=0"
        if status == 401:
            response["WWW-Authenticate"] = "Bearer"
        return response


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


def _is_credential_bearing_request(request: HttpRequest) -> bool:
    user = getattr(request, "user", None)
    if bool(user is not None and user.is_authenticated):
        return True
    # Fail closed when an earlier middleware short-circuits before Django can
    # resolve the principal (notably SecurityMiddleware and WhiteNoise), and
    # for viewer-supplied credentials that do not establish a Django user.
    if any(request.headers.get(name) for name in _CREDENTIAL_HEADER_NAMES):
        return True
    session_cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
    csrf_cookie_name = getattr(settings, "CSRF_COOKIE_NAME", "csrftoken")
    if session_cookie_name in request.COOKIES or csrf_cookie_name in request.COOKIES:
        return True
    # Only documented non-credential preferences are anonymous-safe.  Any
    # other cookie is unknown credential-like state and must not share a
    # response.
    return any(name not in _ANONYMOUS_COOKIE_NAMES for name in request.COOKIES)


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
        if (
            request.path_info.rstrip("/") == "/api/datamailer/events"
            and response.status_code >= 400
        ):
            preserved_headers = {
                name: response.headers[name]
                for name in ("Allow", "WWW-Authenticate", "X-Request-ID", "X-Correlation-ID")
                if name in response.headers
            }
            response = JsonResponse(
                {"error": "Webhook request rejected."},
                status=response.status_code,
            )
            for name, value in preserved_headers.items():
                response[name] = value
        response = _sanitize_mutation_error(request, response)
        apply_security_headers(response)
        private_surface = _is_private_surface(request)
        if settings.NOINDEX or private_surface:
            # Assignment replaces a downstream value instead of appending a second field.
            response["X-Robots-Tag"] = ROBOTS_HEADER_VALUE
        if _is_credential_bearing_request(request) or private_surface:
            apply_private_no_store(response)
        return response
