from __future__ import annotations

import json
from typing import Any

from django.http import HttpRequest
from django.utils.http import parse_header_parameters

from core.idempotency import UnsafeJsonValue, canonical_json_object
from management_auth.constants import MAX_JSON_BYTES

from .errors import APIError


class _DuplicateKey(ValueError):
    pass


def _object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey
        value[key] = item
    return value


def parse_json_object(request: HttpRequest) -> dict[str, Any]:
    content_type, parameters = parse_header_parameters(request.META.get("CONTENT_TYPE", ""))
    if content_type.casefold() != "application/json" or any(
        key.casefold() != "charset" or value.casefold() != "utf-8"
        for key, value in parameters.items()
    ):
        raise APIError(415, "unsupported_media_type", "UTF-8 application/json is required.")
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError as error:
        raise APIError(400, "invalid_request", "The request body is invalid.") from error
    if length > MAX_JSON_BYTES:
        raise APIError(413, "request_too_large", "The request body exceeds 65,536 bytes.")
    try:
        raw = request.body
    except Exception as error:
        raise APIError(
            413,
            "request_too_large",
            "The request body exceeds 65,536 bytes.",
        ) from error
    if len(raw) > MAX_JSON_BYTES:
        raise APIError(413, "request_too_large", "The request body exceeds 65,536 bytes.")
    try:
        decoded = raw.decode("utf-8", errors="strict")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_object_from_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        return canonical_json_object(parsed)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        UnsafeJsonValue,
        ValueError,
    ) as error:
        raise APIError(400, "invalid_json", "The JSON request body is invalid.") from error
