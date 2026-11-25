"""Host-scoped preparation for the explicitly authorized future apex cutover."""

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponsePermanentRedirect,
)
from django.middleware.clickjacking import XFrameOptionsMiddleware
from django.middleware.security import SecurityMiddleware
from django.urls import Resolver404, resolve

from core.cache_policy import ROUTE_CACHE_CLASSES, SHAREABLE_CACHE_CLASSES
from core.middleware import (
    _resolved_view_path,
    apply_private_no_store,
    apply_security_headers,
    has_sensitive_query_key,
    response_may_stay_shareable,
)


def _public_success(request, response):
    directives = {
        part.partition("=")[0].strip().lower()
        for part in response.get("Cache-Control", "").split(",")
    }
    match = getattr(request, "resolver_match", None)
    if match is None:
        # An anonymous page-cache hit returns before Django resolves the URL,
        # so resolve it here; otherwise cached public pages stay noindex.
        try:
            match = resolve(request.path_info)
        except Resolver404:
            match = None
    view = _resolved_view_path(match)
    return (
        request.method in {"GET", "HEAD"}
        and response.status_code == 200
        and not response.cookies
        and not directives.intersection({"private", "no-store"})
        and not has_sensitive_query_key(request)
        and not request.META.get("HTTP_AUTHORIZATION")
        and not getattr(getattr(request, "user", None), "is_authenticated", False)
        and view is not None
        and ROUTE_CACHE_CLASSES.get(view, "") in SHAREABLE_CACHE_CLASSES
        and response_may_stay_shareable(request, response)
    )


class ApexCutoverMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.security = SecurityMiddleware(get_response)
        self.frames = XFrameOptionsMiddleware(get_response)

    def __call__(self, request):
        # get_host validates ALLOWED_HOSTS; forwarded or arbitrary host values
        # cannot activate the indexing exception.
        try:
            host = request.get_host().split(":", 1)[0].lower()
        except DisallowedHost:
            # Let the normal response boundary render and protect the denial.
            return self.get_response(request)
        if host == "www.datatalks.club":
            redirect: HttpResponse
            if request.method not in {"GET", "HEAD"}:
                redirect = HttpResponseNotAllowed(["GET", "HEAD"])
            elif has_sensitive_query_key(request):
                redirect = HttpResponseBadRequest()
            else:
                redirect = HttpResponsePermanentRedirect(
                    "https://datatalks.club" + request.get_full_path()
                )
            # Do not execute an unsafe view before refusing its request.
            apply_security_headers(redirect)
            apply_private_no_store(redirect)
            redirect["X-Robots-Tag"] = "noindex, nofollow"
            return self.frames.process_response(
                request, self.security.process_response(request, redirect)
            )
        response = self.get_response(request)
        if (
            host != "datatalks.club"
            or not getattr(settings, "APEX_CUTOVER_ENABLED", False)
            or not _public_success(request, response)
        ):
            return response
        if request.path == "/robots.txt":
            from core.views import PRODUCTION_ROBOTS_BODY

            response.content = PRODUCTION_ROBOTS_BODY
            if response.has_header("Content-Length"):
                response["Content-Length"] = str(len(response.content))
        if response.has_header("X-Robots-Tag"):
            del response["X-Robots-Tag"]
        return response
