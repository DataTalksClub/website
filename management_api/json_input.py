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


def parse_json_object(request: HttpRequest, *, max_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
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
    if length > max_bytes:
        raise APIError(413, "request_too_large", f"The request body exceeds {max_bytes:,} bytes.")
    try:
        raw = request.body
    except Exception as error:
        raise APIError(
            413,
            "request_too_large",
            f"The request body exceeds {max_bytes:,} bytes.",
        ) from error
    if len(raw) > max_bytes:
        raise APIError(413, "request_too_large", f"The request body exceeds {max_bytes:,} bytes.")
    try:
        decoded = raw.decode("utf-8", errors="strict")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_object_from_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if max_bytes > MAX_JSON_BYTES:
            if not isinstance(parsed, dict):
                raise ValueError("JSON root must be an object.")
            return parsed
        return canonical_json_object(parsed)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        UnsafeJsonValue,
        ValueError,
    ) as error:
        raise APIError(400, "invalid_json", "The JSON request body is invalid.") from error
