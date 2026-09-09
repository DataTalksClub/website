import json
from datetime import datetime
from functools import wraps

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import JsonResponse

INSTRUCTIONS_URL_VALIDATOR = URLValidator(schemes=["http", "https"])


def instructions_url_error(value):
    """Return an error message if value is a non-empty, non-http(s) URL."""
    if not value:
        return None
    try:
        INSTRUCTIONS_URL_VALIDATOR(value)
    except ValidationError:
        return "instructions_url must be a valid http(s) URL"
    return None


def parse_date(date_str):
    """Parse an ISO 8601 date string, returning a datetime or None."""
    try:
        normalized_date_str = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized_date_str)
    except (ValueError, AttributeError):
        return None


MAX_BULK_ITEMS = 200


def _reject_non_finite_constant(value):
    """Reject NaN/Infinity, which json.loads otherwise accepts by default.

    A bare ``NaN`` is not valid JSON, and a non-finite number must never
    reach ``setattr`` on a model field (audit BE-14).
    """

    raise ValueError(f"non-finite number {value} is not valid JSON")


def parse_json_body(request):
    """Parse JSON body from request, returning (data, error_response).

    Strictly JSON: malformed input and the bare ``NaN``/``Infinity``
    constants Python's parser would otherwise accept are both a generic
    ``Invalid JSON`` 400.  The rejected value is never reflected.
    """
    try:
        data = json.loads(request.body, parse_constant=_reject_non_finite_constant)
        return data, None
    except (json.JSONDecodeError, ValueError):
        error_payload = {"error": "Invalid JSON"}
        error_response = JsonResponse(error_payload, status=400)
        return None, error_response


def _invalid_body_error(code, message):
    return JsonResponse({"error": message, "code": code}, status=400)


def parse_json_object(request):
    """Parse a request body that must be one JSON object.

    Every legacy API mutation reads the body field-by-field, so an array,
    string, number, boolean, or null body would otherwise crash the view
    with an ``AttributeError`` (audit BE-14).  Returns ``(dict, None)`` or
    ``(None, error_response)``; the rejected shape is never reflected.
    """

    data, error = parse_json_body(request)
    if error is not None:
        return None, error
    if not isinstance(data, dict):
        return None, _invalid_body_error(
            "invalid_json_object",
            "Request body must be a JSON object",
        )
    return data, None


def parse_json_object_list(request, *, max_items=MAX_BULK_ITEMS):
    """Parse a bulk request body into a bounded list of JSON objects.

    Accepts a JSON array of objects, or a single object for the documented
    legacy single-item form.  Shape validation happens here, before any
    mutation, so a malformed later item can never leave earlier items of the
    same request already persisted (audit BE-14).  Returns
    ``(list[dict], None)`` or ``(None, error_response)``; member values are
    never reflected.
    """

    data, error = parse_json_body(request)
    if error is not None:
        return None, error

    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = data
    else:
        return None, _invalid_body_error(
            "invalid_json_object_list",
            "Request body must be a JSON object or an array of objects",
        )

    if len(items) > max_items:
        return None, _invalid_body_error(
            "too_many_items",
            f"Request body must contain at most {max_items} items",
        )
    for item in items:
        if not isinstance(item, dict):
            return None, _invalid_body_error(
                "invalid_json_object_list",
                "Every item in the request body must be a JSON object",
            )
    return items, None


def require_methods(*methods):
    """Decorator to restrict allowed HTTP methods."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                error_payload = {"error": "Method not allowed"}
                response = JsonResponse(error_payload, status=405)
                return response
            return view_func(request, *args, **kwargs)

        # Introspected by tests and route docs; the narrow ignore is the
        # reviewed exemption for setting an attribute on a plain function.
        wrapper.api_methods = methods  # type: ignore[attr-defined]
        return wrapper

    return decorator
