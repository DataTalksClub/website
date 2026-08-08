from __future__ import annotations

import re

from django.http import HttpRequest

from .errors import APIError

_ETAG = re.compile(r'^"rev-([1-9][0-9]*)"$')


def revision_etag(revision: int) -> str:
    if revision < 1:
        raise ValueError("revision must be positive")
    return f'"rev-{revision}"'


def require_if_match(request: HttpRequest) -> int:
    raw = request.META.get("HTTP_IF_MATCH")
    if raw is None:
        raise APIError(428, "precondition_required", "A strong If-Match revision is required.")
    if "," in raw or raw.startswith("W/") or raw == "*":
        raise APIError(400, "invalid_if_match", "If-Match must contain one strong revision ETag.")
    match = _ETAG.fullmatch(raw)
    if match is None:
        raise APIError(400, "invalid_if_match", "If-Match must contain one strong revision ETag.")
    return int(match.group(1))
