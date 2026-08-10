from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch

from accounts.studio_authorization import has_explicit_permission
from accounts.studio_roles import STUDIO_ACCESS

SAFE_ACCOUNT_DESTINATION = "/"
MAX_NEXT_LENGTH = 4096
MAX_PERCENT_DECODE_ROUNDS = 3
_ACCOUNT_TRANSITION_PATHS = frozenset(
    {
        "/accounts/continue/",
        "/accounts/login/",
        "/accounts/logout/",
        "/auth/logout/",
    }
)
_PERCENT_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")
_MALFORMED_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
_URI_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_PATH_SAFE = "/:@-._~!$&'()*+,;="
_QUERY_SAFE = "!$&'()*+,-./:;=?@_%~"
_FRAGMENT_SAFE = "!$&'()*+,-./:;=?@_%~"


class _UnsafeNext(ValueError):
    pass


def _contains_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _bounded_percent_decode(value: str) -> str:
    if _MALFORMED_PERCENT.search(value):
        raise _UnsafeNext("malformed percent escape")
    decoded = value
    for _round in range(MAX_PERCENT_DECODE_ROUNDS):
        if _PERCENT_ESCAPE.search(decoded) is None:
            return decoded
        try:
            updated = unquote(decoded, errors="strict")
        except UnicodeDecodeError as error:
            raise _UnsafeNext("invalid percent encoding") from error
        if _contains_control(updated):
            raise _UnsafeNext("encoded control character")
        if updated == decoded:
            return decoded
        decoded = updated
    if _PERCENT_ESCAPE.search(decoded) is not None:
        raise _UnsafeNext("percent encoding exceeds decode bound")
    return decoded


def _normalize_absolute_path(path: str) -> str:
    path = path.replace("\\", "/")
    if not path.startswith("/") or path.startswith("//"):
        raise _UnsafeNext("path is not a local absolute path")
    trailing_slash = path.endswith(("/", "/.", "/.."))
    segments: list[str] = []
    for segment in path.split("/"):
        if not segment or segment == ".":
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    normalized = f"/{'/'.join(segments)}"
    if trailing_slash and normalized != "/":
        normalized = f"{normalized}/"
    return normalized


def _resolve_path(*, request_path: str, candidate_path: str) -> str:
    decoded_candidate = _bounded_percent_decode(candidate_path).replace("\\", "/")
    if decoded_candidate.startswith("//"):
        raise _UnsafeNext("protocol-relative target")
    if not decoded_candidate.startswith("/") and _URI_SCHEME.match(decoded_candidate):
        raise _UnsafeNext("scheme-like relative target")
    normalized_request = _normalize_absolute_path(_bounded_percent_decode(request_path))
    if decoded_candidate.startswith("/"):
        combined = decoded_candidate
    else:
        if normalized_request.endswith("/"):
            base_directory = normalized_request
        else:
            base_directory = normalized_request.rpartition("/")[0] or "/"
            if not base_directory.endswith("/"):
                base_directory = f"{base_directory}/"
        combined = f"{base_directory}{decoded_candidate}"
    return _normalize_absolute_path(combined)


def _canonical_local_target(request, candidate: str) -> str:
    if not isinstance(candidate, str) or not candidate:
        raise _UnsafeNext("empty target")
    if len(candidate.encode("utf-8")) > MAX_NEXT_LENGTH:
        raise _UnsafeNext("target is too long")
    if candidate != candidate.strip() or _contains_control(candidate):
        raise _UnsafeNext("target contains unsafe whitespace")

    reference = candidate.replace("\\", "/")
    if reference.startswith("//"):
        raise _UnsafeNext("protocol-relative target")
    try:
        parsed = urlsplit(reference)
    except ValueError as error:
        raise _UnsafeNext("malformed target") from error
    if parsed.scheme or parsed.netloc:
        raise _UnsafeNext("absolute target")

    normalized_path = _resolve_path(
        request_path=request.path,
        candidate_path=parsed.path,
    )
    _bounded_percent_decode(parsed.query)
    _bounded_percent_decode(parsed.fragment)
    return urlunsplit(
        (
            "",
            "",
            quote(normalized_path, safe=_PATH_SAFE),
            quote(parsed.query, safe=_QUERY_SAFE),
            quote(parsed.fragment, safe=_FRAGMENT_SAFE),
        )
    )


def _is_account_transition_path(path: str) -> bool:
    normalized = path if path.endswith("/") else f"{path}/"
    if normalized in _ACCOUNT_TRANSITION_PATHS:
        return True
    return bool(
        normalized.startswith("/accounts/")
        and (normalized.endswith("/login/") or normalized.endswith("/callback/"))
    )


def safe_next_path(request) -> str:
    candidate = request.GET.get("next", "")
    if not candidate:
        candidate = request.path
    try:
        canonical_target = _canonical_local_target(request, candidate)
    except (TypeError, UnicodeError, _UnsafeNext):
        return SAFE_ACCOUNT_DESTINATION
    candidate_path = urlsplit(canonical_target).path
    request_path = _normalize_absolute_path(request.path)
    if _is_account_transition_path(candidate_path):
        return SAFE_ACCOUNT_DESTINATION
    if candidate_path == request_path and _is_account_transition_path(request_path):
        return SAFE_ACCOUNT_DESTINATION
    return canonical_target


def request_uses_canonical_account_host(request) -> bool:
    canonical_host = urlsplit(settings.ACCOUNT_CANONICAL_ORIGIN).netloc
    return bool(canonical_host and canonical_host.casefold() == request.get_host().casefold())


def login_url_for_path(path: str) -> str:
    try:
        login_path = reverse("login")
    except NoReverseMatch:
        login_path = str(settings.LOGIN_URL)
    return f"{login_path}?{urlencode({'next': path})}"


def can_access_studio(user) -> bool:
    authenticated = bool(user is not None and user.is_authenticated and user.is_active)
    return bool(authenticated and user.is_staff and has_explicit_permission(user, STUDIO_ACCESS))


def can_access_course_studio(user) -> bool:
    if not can_access_studio(user):
        return False
    group_names = set()
    if user.is_staff:
        group_names = set(user.groups.values_list("name", flat=True))
    return bool(group_names.intersection({"site_admin", "course_operator"}))


def account_navigation(request):
    user = getattr(request, "user", None)
    return {
        "account_login_url": login_url_for_path(safe_next_path(request)),
        "can_access_studio": can_access_studio(user),
        "can_access_course_studio": can_access_course_studio(user),
    }
