"""Low-cardinality compatibility monitoring without path or referrer leakage."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from course_management.observability.events import record_event

_CONTRACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_METHODS = frozenset({"GET", "HEAD"})
_SUPPRESSED: ContextVar[bool] = ContextVar("compatibility_monitoring_suppressed", default=False)


@dataclass(frozen=True, slots=True)
class CompatibilityEvent:
    event_kind: str
    status: int
    status_class: str
    method: str
    path_group: str
    referrer_group: str
    redirect_group: str
    canonical_mismatch: bool
    request_kind: str
    crawler_kind: str
    duration_ms: int

    def properties(self) -> dict[str, str | int | bool]:
        return asdict(self)


def _known_contracts(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > 20_000:
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_contract_id in value.items():
        if (
            type(raw_key) is not str
            or type(raw_contract_id) is not str
            or len(raw_key) > 2_048
            or _CONTRACT_ID.fullmatch(raw_contract_id) is None
        ):
            return {}
        parsed = urlsplit(f"//{raw_key}" if not raw_key.startswith("//") else raw_key)
        if not parsed.hostname or parsed.query or parsed.fragment or parsed.username is not None:
            return {}
        result[f"{parsed.netloc.lower()}{parsed.path or '/'}"] = raw_contract_id
    return result


def _request_key(host: str, path: str) -> str:
    safe_path = path.split("?", 1)[0].split("#", 1)[0]
    return f"{host.lower()}{safe_path}"


def _path_group(host: str, path: str, known: Mapping[str, str]) -> str:
    contract_id = known.get(_request_key(host, path))
    return f"contract:{contract_id}" if contract_id else "unknown"


def _referrer_group(referrer: str, host: str, known: Mapping[str, str]) -> str:
    if not referrer:
        return "absent"
    if len(referrer) > 4_096 or any(ord(character) < 0x20 for character in referrer):
        return "invalid"
    try:
        parsed = urlsplit(referrer)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return "invalid"
    except ValueError:
        return "invalid"
    if parsed.netloc.lower() != host.lower():
        return "external"
    key = _request_key(host, parsed.path or "/")
    contract_id = known.get(key)
    return f"contract:{contract_id}" if contract_id else "internal_unknown"


def _redirect_group(location: str, host: str, known: Mapping[str, str]) -> str:
    if not location:
        return "missing"
    if len(location) > 4_096 or any(ord(character) < 0x20 for character in location):
        return "invalid"
    try:
        parsed = urlsplit(location)
    except ValueError:
        return "invalid"
    if parsed.netloc and parsed.netloc.lower() != host.lower():
        return "external"
    target_host = parsed.netloc or host
    contract_id = known.get(_request_key(target_host, parsed.path or "/"))
    return f"contract:{contract_id}" if contract_id else "internal_unknown"


def _request_kind(path: str, path_group: str) -> str:
    exact_path = path.split("?", 1)[0].split("#", 1)[0]
    if exact_path.endswith("/sitemap.xml") or exact_path == "/sitemap.xml":
        return "sitemap"
    if exact_path.endswith("/robots.txt") or exact_path == "/robots.txt":
        return "robots"
    return "contract" if path_group.startswith("contract:") else "other"


def _crawler_kind(user_agent: str) -> str:
    lowered = user_agent[:1_024].lower()
    for known in ("googlebot", "bingbot", "duckduckbot", "yandexbot", "baiduspider"):
        if known in lowered:
            return known
    if any(marker in lowered for marker in ("bot", "crawler", "spider", "slurp")):
        return "other_crawler"
    return "browser_or_other"


@contextmanager
def suppress_compatibility_monitoring():
    """Suppress trusted in-process parity probes; no request header can set this."""

    token = _SUPPRESSED.set(True)
    try:
        yield
    finally:
        _SUPPRESSED.reset(token)


def safe_compatibility_event(
    *,
    host: str,
    path: str,
    method: str,
    status: int,
    referrer: str = "",
    location: str = "",
    canonical_mismatch: bool = False,
    known_contracts: Mapping[str, str] | None = None,
    user_agent: str = "",
    duration_ms: int = 0,
) -> CompatibilityEvent:
    """Build one bounded event without retaining raw user-controlled dimensions."""

    if type(status) is not int or not 100 <= status <= 599:
        raise ValueError("compatibility_event_status_is_invalid")
    if type(canonical_mismatch) is not bool:
        raise ValueError("canonical_mismatch_must_be_boolean")
    if type(duration_ms) is not int or duration_ms < 0:
        raise ValueError("compatibility_event_duration_is_invalid")
    duration_ms = min(duration_ms, 120_000)
    is_redirect = status in {301, 302, 303, 307, 308}
    known = _known_contracts(known_contracts or {})
    kind = (
        "canonical_mismatch"
        if canonical_mismatch
        else "redirect"
        if is_redirect
        else "http_error"
        if status in {404, 410} or status >= 500
        else "request"
    )
    path_group = _path_group(host, path, known)
    return CompatibilityEvent(
        event_kind=kind,
        status=status,
        status_class=f"{status // 100}xx",
        method=method.upper() if method.upper() in _SAFE_METHODS else "other",
        path_group=path_group,
        referrer_group=_referrer_group(referrer, host, known),
        redirect_group=(_redirect_group(location, host, known) if is_redirect else "not_redirect"),
        canonical_mismatch=canonical_mismatch,
        request_kind=_request_kind(path, path_group),
        crawler_kind=_crawler_kind(user_agent),
        duration_ms=duration_ms,
    )


def emit_compatibility_event(
    event: CompatibilityEvent,
    *,
    recorder: Callable[..., None] = record_event,
) -> None:
    """Emit without passing a request, so generic observability cannot add raw paths."""

    recorder(
        "compatibility_response",
        request=None,
        distinct_id="anonymous",
        properties=event.properties(),
    )


class CompatibilityMonitoringMiddleware:
    """Observe only release-relevant outcomes using safe configured contract IDs."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started = time.monotonic()
        response = self.get_response(request)
        if _SUPPRESSED.get() or request.path == "/health/ready":
            return response
        event = safe_compatibility_event(
            host=request.get_host(),
            path=request.path,
            method=request.method or "",
            status=response.status_code,
            referrer=request.headers.get("Referer", ""),
            location=response.headers.get("Location", ""),
            canonical_mismatch=bool(getattr(response, "compatibility_canonical_mismatch", False)),
            known_contracts=getattr(settings, "COMPATIBILITY_CONTRACT_PATHS", {}),
            user_agent=request.headers.get("User-Agent", ""),
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
        emit_compatibility_event(event)
        return response
