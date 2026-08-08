from __future__ import annotations

from typing import Any

from core.idempotency import (
    JsonObject,
    UnsafeJsonValue,
    canonical_json,
    canonical_json_bytes,
    canonical_json_object,
)
from core.limits import MAX_OPERATION_JSON_BYTES
from management_auth.constants import MAX_BULK_ERRORS, MAX_BULK_ITEMS

from .errors import APIError


def parse_bulk_items(
    raw_items: Any,
    *,
    writable_fields: tuple[str, ...],
) -> tuple[JsonObject, ...]:
    """Validate a bounded bulk fixture without ignoring unknown item fields."""

    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_BULK_ITEMS:
        raise APIError(400, "invalid_bulk", "Bulk requests require between 1 and 100 items.")
    allowed = frozenset(writable_fields)
    items: list[JsonObject] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict) or set(raw_item) - allowed:
            raise APIError(400, "invalid_bulk", "A bulk item is invalid.")
        items.append(canonical_json_object(raw_item))
    return tuple(items)


def bounded_bulk_errors(errors: list[Any]) -> list[Any]:
    """Normalize and cap item-level errors before persistence or response."""

    if len(errors) > MAX_BULK_ERRORS:
        raise APIError(400, "invalid_bulk", "Bulk responses exceed 100 item errors.")
    try:
        normalized = canonical_json(errors)
    except UnsafeJsonValue as error:
        raise APIError(400, "invalid_bulk", "Bulk item errors exceed safe bounds.") from error
    if not isinstance(normalized, list):
        raise APIError(400, "invalid_bulk", "Bulk item errors are invalid.")
    if len(canonical_json_bytes(normalized)) > MAX_OPERATION_JSON_BYTES:
        raise APIError(400, "invalid_bulk", "Bulk item errors exceed 65,536 bytes.")
    return normalized
