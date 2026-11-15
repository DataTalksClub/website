"""Reverse proxy for the static DataTalks.Club Academy site under /academy/*."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed

from core.source_policy import UMAMI_ORIGIN

logger = logging.getLogger(__name__)

DEFAULT_ACADEMY_ORIGIN_URL = "https://d5688tx5knzax.cloudfront.net"
ACADEMY_API_ORIGIN = "https://augldvp3omxdxz3irqsmmxc25e0blpxx.lambda-url.eu-west-1.on.aws"
ACADEMY_CSP = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
    f"form-action 'self'; script-src 'self' 'unsafe-inline' {UMAMI_ORIGIN}; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; "
    f"connect-src 'self' {UMAMI_ORIGIN} {ACADEMY_API_ORIGIN}; "
    "media-src 'self'; frame-src https://www.youtube.com https://www.youtube-nocookie.com "
    "https://creators.spotify.com https://open.spotify.com"
)
_PASSTHROUGH_HEADERS = ("Content-Type", "Cache-Control", "ETag", "Last-Modified")
_UPSTREAM_TIMEOUT_SECONDS = 15


def _academy_origin_url() -> str:
    raw = getattr(settings, "ACADEMY_ORIGIN_URL", DEFAULT_ACADEMY_ORIGIN_URL)
    return str(raw).rstrip("/")


def _rewrite_redirect_location(location: str, origin: str) -> str:
    if not location:
        return location
    origin_parts = urlsplit(origin)
    loc_parts = urlsplit(location)
    if loc_parts.scheme and loc_parts.netloc == origin_parts.netloc:
        path = loc_parts.path or "/"
        if loc_parts.query:
            return f"{path}?{loc_parts.query}"
        return path
    return location


def academy_proxy(request: HttpRequest, academy_path: str = "") -> HttpResponse:
    """Proxy GET/HEAD requests for /academy and /academy/* to the Academy origin."""

    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(("GET", "HEAD"))

    if ".." in request.path_info.split("/"):
        return HttpResponse(status=404)

    origin = _academy_origin_url()
    upstream_url = f"{origin}{request.path_info}"
    query = request.META.get("QUERY_STRING", "")
    if query:
        upstream_url = f"{upstream_url}?{query}"

    try:
        upstream = requests.request(
            request.method,
            upstream_url,
            allow_redirects=False,
            timeout=_UPSTREAM_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        logger.warning("Academy upstream request failed for %s", request.path_info, exc_info=True)
        return HttpResponse(
            "Academy upstream unavailable.",
            status=502,
            content_type="text/plain; charset=utf-8",
        )

    body = b"" if request.method == "HEAD" else upstream.content
    response = HttpResponse(body, status=upstream.status_code)
    for header in _PASSTHROUGH_HEADERS:
        value = upstream.headers.get(header)
        if value:
            response[header] = value

    location = upstream.headers.get("Location")
    if location:
        response["Location"] = _rewrite_redirect_location(location, origin)

    response["Content-Security-Policy"] = ACADEMY_CSP
    return response
