"""Bounded, deterministic capture transport for the legacy compatibility manifest.

The crawler deliberately owns transport and scheduling only.  Parsing and manifest
semantics live in :mod:`compatibility.extract` and :mod:`compatibility.models`.
Network access is fail closed: every request (including every redirect) must match
an exact scheme/host/port/path rule and every resolved address must be globally
routable.  Connections are pinned to an address that passed that validation so a
second DNS lookup cannot redirect the socket to an internal service.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import mimetypes
import os
import secrets
import socket
import ssl
import stat
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, Protocol, Self
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

from compatibility.extract import (
    ExtractionError,
    extract_html,
    extract_json,
    extract_sitemap,
    extract_text,
)
from compatibility.models import (
    Capture,
    ManifestValidationError,
    ObservationOrigin,
    PageMetadata,
    RedirectHop,
    SitemapState,
)
from compatibility.redaction import (
    is_redacted_value,
    normalize_url_path,
    percent_encode_filesystem_path,
    redact_url,
    value_requires_redaction,
)

CRAWLER_SCHEMA_VERSION = 1
CRAWLER_TOOL_VERSION = "dtc-legacy-manifest-crawler/3"
CRAWLER_USER_AGENT = (
    "DataTalksClub-CompatibilityCrawler/1.0 (+https://github.com/DataTalksClub/website)"
)
CHECKPOINT_MAX_BYTES = 64 * 1024 * 1024
_PROJECT_SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".tmp"
_SOURCE_MIME_TYPES = mimetypes.MimeTypes(filenames=())
# Preserve the deployed/source baseline spelling while remaining independent of host mime.types.
_SOURCE_MIME_TYPES.add_type("application/xml", ".xml", strict=True)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "content-language",
        "content-type",
        "last-modified",
        "location",
        "retry-after",
        "x-robots-tag",
    }
)
_SENSITIVE_QUERY_PARTS = frozenset(
    {
        "access_token",
        "apikey",
        "api_key",
        "auth",
        "authorization",
        "code",
        "credential",
        "email",
        "jwt",
        "key",
        "password",
        "secret",
        "signature",
        "sig",
        "token",
    }
)


def _source_content_type(path: str) -> str:
    """Return a deterministic source-tree type without consulting host MIME configuration."""

    return _SOURCE_MIME_TYPES.guess_type(path)[0] or "application/octet-stream"


_EMAIL_MARKERS = ("@", "%40")


class CrawlError(RuntimeError):
    """A crawler error whose message is safe to write to an operator log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CrawlPolicyError(CrawlError):
    """A URL, address, or response violated the fail-closed crawl policy."""


class CrawlTransportError(CrawlError):
    """A bounded request failed without including remote response details."""

    def __init__(self, code: str, *, transfer_bytes: int = 0) -> None:
        super().__init__(code)
        self.transfer_bytes = transfer_bytes


class CrawlCheckpointError(CrawlError):
    """A checkpoint is invalid or belongs to a different crawl."""


def _bounded_deadline(value: float | None, now: float, maximum_seconds: float) -> float:
    local_limit = now + maximum_seconds
    if value is None:
        return local_limit
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CrawlTransportError("invalid_crawl_deadline")
    return min(float(value), local_limit)


def _normalized_hostname(value: str) -> str:
    candidate = value.rstrip(".").lower()
    if not candidate or "*" in candidate:
        raise CrawlPolicyError("invalid_allowlist_host")
    try:
        encoded = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise CrawlPolicyError("invalid_allowlist_host") from exc
    if len(encoded) > 253 or any(not label or len(label) > 63 for label in encoded.split(".")):
        raise CrawlPolicyError("invalid_allowlist_host")
    return encoded


def _decoded_path_for_policy(path: str) -> str:
    decoded = path
    for _ in range(8):
        next_value = unquote(decoded, errors="strict")
        if next_value == decoded:
            break
        decoded = next_value
    else:
        if unquote(decoded, errors="strict") != decoded:
            raise CrawlPolicyError("url_has_excessive_encoding_layers")
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        raise CrawlPolicyError("url_contains_control_character")
    if "\\" in decoded:
        raise CrawlPolicyError("url_contains_backslash")
    if any(part == ".." for part in decoded.split("/")):
        raise CrawlPolicyError("url_contains_parent_segment")
    return decoded


def _path_is_below(path: str, prefix: str) -> bool:
    if prefix == "/":
        return path.startswith("/")
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(f"{prefix}/")


def _query_key_is_sensitive(key: str) -> bool:
    normalized = "".join(character if character.isalnum() else "_" for character in key.lower())
    parts = frozenset(part for part in normalized.split("_") if part)
    return normalized in _SENSITIVE_QUERY_PARTS or bool(parts & _SENSITIVE_QUERY_PARTS)


def _query_value_looks_private(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _EMAIL_MARKERS) or lowered.startswith(
        ("bearer ", "ghp_", "github_pat_", "akia", "eyj")
    )


def _is_safe_local_next(value: str) -> bool:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or any(character.isspace() for character in value)
    ):
        return False
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return False
    try:
        _decoded_path_for_policy(parsed.path)
    except CrawlPolicyError:
        return False
    for key, nested_value in parse_qsl(parsed.query, keep_blank_values=True):
        if _query_key_is_sensitive(key) or _query_value_looks_private(nested_value):
            return False
    return True


@dataclass(frozen=True, slots=True)
class AllowlistRule:
    """One exact network destination and the public paths it may expose."""

    scheme: str
    host: str
    port: int
    path_prefixes: tuple[str, ...] = ("/",)
    allowed_query_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        scheme = self.scheme.lower()
        if scheme not in {"http", "https"}:
            raise CrawlPolicyError("invalid_allowlist_scheme")
        if not 1 <= self.port <= 65535:
            raise CrawlPolicyError("invalid_allowlist_port")
        host = _normalized_hostname(self.host)
        prefixes = tuple(sorted(set(self.path_prefixes)))
        if not prefixes:
            raise CrawlPolicyError("missing_allowlist_path_prefix")
        for prefix in prefixes:
            if not prefix.startswith("/") or "?" in prefix or "#" in prefix:
                raise CrawlPolicyError("invalid_allowlist_path_prefix")
            decoded = _decoded_path_for_policy(prefix)
            if decoded != prefix:
                raise CrawlPolicyError("encoded_allowlist_path_prefix")
        query_keys = tuple(sorted(set(self.allowed_query_keys)))
        if any(not key for key in query_keys):
            raise CrawlPolicyError("invalid_allowlist_query_key")
        object.__setattr__(self, "scheme", scheme)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "path_prefixes", prefixes)
        object.__setattr__(self, "allowed_query_keys", query_keys)

    def canonical_dict(self) -> dict[str, object]:
        return {
            "allowed_query_keys": list(self.allowed_query_keys),
            "host": self.host,
            "path_prefixes": list(self.path_prefixes),
            "port": self.port,
            "scheme": self.scheme,
        }


@dataclass(frozen=True, slots=True)
class CrawlBounds:
    """Hard limits for one crawl, including work restored from a checkpoint."""

    max_urls: int = 10_000
    max_responses: int = 30_000
    max_redirects: int = 8
    max_retries: int = 2
    max_url_length: int = 2_048
    max_response_bytes: int = 5 * 1024 * 1024
    max_total_bytes: int = 500 * 1024 * 1024
    max_addresses: int = 8
    request_timeout_seconds: float = 10.0
    max_run_seconds: float = 900.0
    request_interval_seconds: float = 0.0
    retry_backoff_seconds: float = 0.0
    max_retry_after_seconds: float = 30.0

    def __post_init__(self) -> None:
        integer_bounds = (
            self.max_urls,
            self.max_responses,
            self.max_redirects,
            self.max_retries,
            self.max_url_length,
            self.max_response_bytes,
            self.max_total_bytes,
            self.max_addresses,
        )
        if any(type(value) is not int for value in integer_bounds):
            raise CrawlPolicyError("crawl_integer_bound_must_be_integer")
        if any(value < 0 for value in integer_bounds):
            raise CrawlPolicyError("negative_crawl_bound")
        if self.max_urls == 0 or self.max_responses == 0:
            raise CrawlPolicyError("empty_crawl_bound")
        float_bounds = (
            self.request_timeout_seconds,
            self.max_run_seconds,
            self.request_interval_seconds,
            self.retry_backoff_seconds,
            self.max_retry_after_seconds,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in float_bounds
        ):
            raise CrawlPolicyError("crawl_time_bound_must_be_finite_number")
        if self.request_timeout_seconds <= 0 or self.max_run_seconds <= 0:
            raise CrawlPolicyError("invalid_time_bound")
        if any(
            value < 0
            for value in (
                self.request_interval_seconds,
                self.retry_backoff_seconds,
                self.max_retry_after_seconds,
            )
        ):
            raise CrawlPolicyError("negative_delay_bound")

    def canonical_dict(self) -> dict[str, int | float]:
        return {
            "max_redirects": self.max_redirects,
            "max_response_bytes": self.max_response_bytes,
            "max_responses": self.max_responses,
            "max_retries": self.max_retries,
            "max_run_seconds": self.max_run_seconds,
            "max_total_bytes": self.max_total_bytes,
            "max_addresses": self.max_addresses,
            "max_url_length": self.max_url_length,
            "max_urls": self.max_urls,
            "request_timeout_seconds": self.request_timeout_seconds,
            "request_interval_seconds": self.request_interval_seconds,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "max_retry_after_seconds": self.max_retry_after_seconds,
        }


@dataclass(frozen=True, slots=True)
class AuthorizedTarget:
    url: str
    scheme: str
    host: str
    port: int
    request_target: str
    addresses: tuple[str, ...]


Resolver = Callable[[str, int], Iterable[str]]


def resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    """Resolve *host* once and return only after every result is globally routable."""

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise CrawlPolicyError("dns_resolution_failed") from exc
        addresses = {str(entry[4][0]).split("%", 1)[0] for entry in results}
    else:
        addresses = {str(literal)}
    if not addresses:
        raise CrawlPolicyError("dns_resolution_empty")
    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise CrawlPolicyError("dns_returned_invalid_address") from exc
        if not ip.is_global:
            raise CrawlPolicyError("dns_returned_non_public_address")
        parsed.append(ip)
    return tuple(str(ip) for ip in sorted(parsed, key=lambda value: (value.version, int(value))))


@dataclass(frozen=True, slots=True)
class CrawlPolicy:
    rules: tuple[AllowlistRule, ...]
    bounds: CrawlBounds = field(default_factory=CrawlBounds)
    discover_references: bool = True
    robots_required: bool = True

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(
                set(self.rules),
                key=lambda rule: (
                    rule.scheme,
                    rule.host,
                    rule.port,
                    rule.path_prefixes,
                    rule.allowed_query_keys,
                ),
            )
        )
        if not ordered:
            raise CrawlPolicyError("empty_crawl_allowlist")
        if type(self.discover_references) is not bool or type(self.robots_required) is not bool:
            raise CrawlPolicyError("crawl_policy_flag_must_be_boolean")
        object.__setattr__(self, "rules", ordered)

    @property
    def fingerprint(self) -> str:
        payload = {
            "bounds": self.bounds.canonical_dict(),
            "rules": [rule.canonical_dict() for rule in self.rules],
            "discover_references": self.discover_references,
            "robots_required": self.robots_required,
            "schema_version": CRAWLER_SCHEMA_VERSION,
            "tool_version": CRAWLER_TOOL_VERSION,
            "user_agent": CRAWLER_USER_AGENT,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @property
    def internal_hosts(self) -> frozenset[str]:
        return frozenset(rule.host for rule in self.rules)

    def matching_rule(self, url: str) -> AllowlistRule | None:
        if len(url) > self.bounds.max_url_length:
            raise CrawlPolicyError("url_too_long")
        if any(character.isspace() for character in url):
            raise CrawlPolicyError("url_contains_raw_whitespace")
        if any(ord(character) < 32 or ord(character) == 127 for character in url):
            raise CrawlPolicyError("url_contains_control_character")
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise CrawlPolicyError("invalid_url") from exc
        if parsed.fragment:
            raise CrawlPolicyError("fetch_url_contains_fragment")
        if parsed.username is not None or parsed.password is not None:
            raise CrawlPolicyError("url_contains_credentials")
        if not host:
            raise CrawlPolicyError("url_missing_host")
        normalized_host = _normalized_hostname(host)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            return None
        effective_port = port or (443 if scheme == "https" else 80)
        path = parsed.path or "/"
        decoded_path = _decoded_path_for_policy(path)
        for rule in self.rules:
            if (scheme, normalized_host, effective_port) != (rule.scheme, rule.host, rule.port):
                continue
            if not any(
                _path_is_below(path, prefix) and _path_is_below(decoded_path, prefix)
                for prefix in rule.path_prefixes
            ):
                continue
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
            for key, value in pairs:
                if key not in rule.allowed_query_keys:
                    raise CrawlPolicyError("query_key_not_allowlisted")
                if key == "next" and not _is_safe_local_next(value):
                    raise CrawlPolicyError("query_next_value_not_safe")
                if (
                    value_requires_redaction(key, value) or _query_value_looks_private(value)
                ) and not is_redacted_value(value):
                    raise CrawlPolicyError("query_contains_private_data")
            return rule
        return None

    def authorize(
        self, url: str, resolver: Resolver = resolve_public_addresses
    ) -> AuthorizedTarget:
        rule = self.matching_rule(url)
        if rule is None:
            raise CrawlPolicyError("url_not_allowlisted")
        parsed = urlsplit(url)
        host = _normalized_hostname(parsed.hostname or "")
        addresses = tuple(resolver(host, rule.port))
        if not addresses:
            raise CrawlPolicyError("dns_resolution_empty")
        verified: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError as exc:
                raise CrawlPolicyError("dns_returned_invalid_address") from exc
            if not ip.is_global:
                raise CrawlPolicyError("dns_returned_non_public_address")
            verified.append(ip)
        ordered = tuple(
            str(ip) for ip in sorted(set(verified), key=lambda value: (value.version, int(value)))
        )
        if len(ordered) > self.bounds.max_addresses:
            raise CrawlPolicyError("dns_address_count_limit_exceeded")
        request_target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        return AuthorizedTarget(
            url=url,
            scheme=rule.scheme,
            host=host,
            port=rule.port,
            request_target=request_target,
            addresses=ordered,
        )


@dataclass(frozen=True, slots=True)
class HttpResponse:
    requested_url: str
    final_url: str
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    redirect_chain: tuple[RedirectHop, ...]
    response_count: int
    transfer_bytes: int

    @property
    def content_type(self) -> str:
        return dict(self.headers).get("content-type", "").split(";", 1)[0].strip().lower()


class _PinnedConnection(http.client.HTTPConnection):
    """HTTP connection whose socket destination is the already-validated address."""

    def __init__(self, target: AuthorizedTarget, address: str, timeout: float) -> None:
        super().__init__(target.host, target.port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()


class _PinnedTlsConnection(_PinnedConnection):
    def __init__(
        self,
        target: AuthorizedTarget,
        address: str,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(target, address, timeout)
        self._server_hostname = target.host
        self._context = context

    def connect(self) -> None:
        super().connect()
        assert self.sock is not None
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._server_hostname)


class Connection(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def request(
        self, method: str, url: str, body: bytes | None, headers: Mapping[str, str]
    ) -> None: ...

    def getresponse(self) -> http.client.HTTPResponse: ...


ConnectionFactory = Callable[[AuthorizedTarget, str, float], Any]
RobotsVerifier = Callable[[str], None]


def _default_connection_factory(
    target: AuthorizedTarget, address: str, timeout: float
) -> Connection:
    if target.scheme == "https":
        return _PinnedTlsConnection(target, address, timeout, ssl.create_default_context())
    return _PinnedConnection(target, address, timeout)


class BoundedHttpTransport:
    """GET-only transport that validates and pins every hop independently."""

    def __init__(
        self,
        policy: CrawlPolicy,
        *,
        resolver: Resolver = resolve_public_addresses,
        connection_factory: ConnectionFactory = _default_connection_factory,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        robots_verifier: RobotsVerifier | None = None,
    ) -> None:
        self.policy = policy
        self._resolver = resolver
        self._connection_factory = connection_factory
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._robots_verifier = robots_verifier
        self._last_request_at: dict[tuple[str, str, int], float] = {}
        self.last_response_count = 0
        self.last_transfer_bytes = 0

    def fetch(
        self,
        url: str,
        *,
        deadline: float | None = None,
        max_responses: int | None = None,
        max_bytes: int | None = None,
    ) -> HttpResponse:
        self.last_response_count = 0
        self.last_transfer_bytes = 0
        started = self._monotonic()
        hard_deadline = _bounded_deadline(
            deadline,
            started,
            self.policy.bounds.max_run_seconds,
        )
        if max_responses is not None and (type(max_responses) is not int or max_responses <= 0):
            raise CrawlTransportError("invalid_response_budget")
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
            raise CrawlTransportError("invalid_byte_budget")
        response_budget = min(
            self.policy.bounds.max_responses,
            self.policy.bounds.max_responses if max_responses is None else max_responses,
        )
        byte_budget = min(
            self.policy.bounds.max_total_bytes,
            self.policy.bounds.max_total_bytes if max_bytes is None else max_bytes,
        )
        if response_budget <= 0 or byte_budget <= 0:
            raise CrawlTransportError("crawl_budget_exhausted")
        current_url = url
        redirect_chain: list[RedirectHop] = []
        visited_urls = {url}
        response_count = 0
        transfer_bytes = 0
        for redirect_number in range(self.policy.bounds.max_redirects + 1):
            target = self.policy.authorize(current_url, self._resolver)
            response: tuple[int, tuple[tuple[str, str], ...], bytes] | None = None
            for attempt in range(self.policy.bounds.max_retries + 1):
                if self._monotonic() >= hard_deadline:
                    raise CrawlTransportError("crawl_deadline_exceeded")
                if response_count >= response_budget:
                    raise CrawlTransportError("response_count_limit_exceeded")
                if transfer_bytes >= byte_budget:
                    raise CrawlTransportError("total_response_size_limit_exceeded")
                self._pace(target, hard_deadline)
                response_count += 1
                self.last_response_count = response_count
                try:
                    remaining = hard_deadline - self._monotonic()
                    if remaining <= 0:
                        raise CrawlTransportError("crawl_deadline_exceeded")
                    response = self._request_once(
                        target,
                        hard_deadline,
                        min(self.policy.bounds.max_response_bytes, byte_budget - transfer_bytes),
                    )
                except CrawlTransportError as exc:
                    transfer_bytes += exc.transfer_bytes
                    self.last_transfer_bytes = transfer_bytes
                    if (
                        exc.code == "all_validated_addresses_failed"
                        and attempt < self.policy.bounds.max_retries
                    ):
                        self._retry_backoff(None, hard_deadline)
                        continue
                    raise
                except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                    if attempt >= self.policy.bounds.max_retries:
                        raise CrawlTransportError("request_failed_after_retries") from exc
                    self._retry_backoff(None, hard_deadline)
                    continue
                transfer_bytes += len(response[2])
                self.last_transfer_bytes = transfer_bytes
                if transfer_bytes > byte_budget:
                    raise CrawlTransportError("total_response_size_limit_exceeded")
                if response[0] in _RETRYABLE_STATUSES and attempt < self.policy.bounds.max_retries:
                    self._retry_backoff(dict(response[1]).get("retry-after"), hard_deadline)
                    continue
                break
            assert response is not None
            status, headers, body = response
            header_map = dict(headers)
            if status not in _REDIRECT_STATUSES:
                return HttpResponse(
                    requested_url=url,
                    final_url=current_url,
                    status=status,
                    headers=headers,
                    body=body,
                    redirect_chain=tuple(redirect_chain),
                    response_count=response_count,
                    transfer_bytes=transfer_bytes,
                )
            location = header_map.get("location")
            if not location:
                raise CrawlTransportError("redirect_missing_location")
            if redirect_number >= self.policy.bounds.max_redirects:
                raise CrawlTransportError("redirect_limit_exceeded")
            raw_hop_url = urljoin(current_url, location)
            normalized_hop_url = normalize_url_path(raw_hop_url)
            next_url = _without_fragment(normalized_hop_url)
            # Authorize the exact network destination before any durable-value
            # redaction. A sensitive query fails closed and is never fetched.
            self.policy.authorize(next_url, self._resolver)
            if self._robots_verifier is not None:
                self._robots_verifier(next_url)
            try:
                hop_url = redact_url(normalized_hop_url)
            except ValueError as exc:
                raise CrawlPolicyError("redirect_location_is_unsafe") from exc
            if next_url in visited_urls:
                raise CrawlTransportError("redirect_loop")
            try:
                redirect_chain.append(RedirectHop(status=status, url=hop_url))
            except ManifestValidationError as exc:
                raise CrawlPolicyError("redirect_location_contains_private_data") from exc
            visited_urls.add(next_url)
            current_url = next_url
        raise CrawlTransportError("redirect_limit_exceeded")

    def _pace(self, target: AuthorizedTarget, deadline: float) -> None:
        key = (target.scheme, target.host, target.port)
        now = self._monotonic()
        previous = self._last_request_at.get(key)
        if previous is not None:
            remaining_delay = self.policy.bounds.request_interval_seconds - (now - previous)
            self._sleep_with_deadline(max(0.0, remaining_delay), deadline)
        self._last_request_at[key] = self._monotonic()

    def _retry_backoff(self, retry_after: str | None, deadline: float) -> None:
        delay = self.policy.bounds.retry_backoff_seconds
        if retry_after is not None and retry_after.isdecimal():
            delay = max(
                delay,
                min(float(retry_after), self.policy.bounds.max_retry_after_seconds),
            )
        self._sleep_with_deadline(delay, deadline)

    def _sleep_with_deadline(self, delay: float, deadline: float) -> None:
        if delay <= 0:
            return
        if self._monotonic() + delay >= deadline:
            raise CrawlTransportError("crawl_deadline_exceeded")
        self._sleeper(delay)

    def _request_once(
        self,
        target: AuthorizedTarget,
        deadline: float,
        max_body_bytes: int,
    ) -> tuple[int, tuple[tuple[str, str], ...], bytes]:
        last_error: BaseException | None = None
        for address in target.addresses:
            try:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise CrawlTransportError("crawl_deadline_exceeded")
                timeout = min(self.policy.bounds.request_timeout_seconds, remaining)
                with self._connection_factory(target, address, timeout) as connection:
                    connection.request(
                        "GET",
                        target.request_target,
                        None,
                        {
                            "Accept": (
                                "text/html,application/xhtml+xml,application/json,application/xml,"
                                "text/xml;q=0.9,*/*;q=0.1"
                            ),
                            "Accept-Encoding": "identity",
                            "Connection": "close",
                            "Host": _host_header(target),
                            "User-Agent": CRAWLER_USER_AGENT,
                        },
                    )
                    remote = connection.getresponse()
                    headers = _safe_headers(remote.getheaders())
                    declared_length = _declared_content_length(remote.getheader("Content-Length"))
                    if (
                        declared_length is not None
                        and declared_length > self.policy.bounds.max_response_bytes
                    ):
                        raise CrawlTransportError("response_too_large")
                    if declared_length is not None and declared_length > max_body_bytes:
                        raise CrawlTransportError("total_response_size_limit_exceeded")
                    body = remote.read(max_body_bytes + 1)
                    if len(body) > max_body_bytes:
                        code = (
                            "response_too_large"
                            if max_body_bytes == self.policy.bounds.max_response_bytes
                            else "total_response_size_limit_exceeded"
                        )
                        raise CrawlTransportError(
                            code,
                            transfer_bytes=len(body),
                        )
                    return remote.status, headers, body
            except http.client.IncompleteRead as exc:
                raise CrawlTransportError(
                    "incomplete_response",
                    transfer_bytes=len(exc.partial),
                ) from exc
            except CrawlError:
                raise
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_error = exc
        raise CrawlTransportError("all_validated_addresses_failed") from last_error


def _host_header(target: AuthorizedTarget) -> str:
    default_port = 443 if target.scheme == "https" else 80
    host = f"[{target.host}]" if ":" in target.host else target.host
    return host if target.port == default_port else f"{host}:{target.port}"


def _safe_headers(headers: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    selected: dict[str, str] = {}
    for raw_name, raw_value in headers:
        name = raw_name.lower().strip()
        if name not in _SAFE_RESPONSE_HEADERS:
            continue
        value = raw_value.strip()
        if any(ord(character) < 32 and character != "\t" for character in value):
            raise CrawlTransportError("response_header_contains_control_character")
        selected[name] = value
    return tuple(sorted(selected.items()))


def _declared_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise CrawlTransportError("invalid_content_length") from exc
    if parsed < 0:
        raise CrawlTransportError("invalid_content_length")
    return parsed


def _without_fragment(url: str) -> str:
    if len(url) == 0:
        raise CrawlPolicyError("empty_url")
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


@dataclass(frozen=True, slots=True)
class CrawlCheckpoint:
    """Scheduling state; completed captures are kept in a separate manifest work file."""

    policy_sha256: str
    seeds_sha256: str
    pending_urls: tuple[str, ...]
    completed_urls: tuple[str, ...]
    response_count: int = 0
    total_bytes: int = 0
    schema_version: int = CRAWLER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != CRAWLER_SCHEMA_VERSION:
            raise CrawlCheckpointError("unsupported_checkpoint_schema")
        if not _is_sha256(self.policy_sha256) or not _is_sha256(self.seeds_sha256):
            raise CrawlCheckpointError("invalid_checkpoint_fingerprint")
        if (
            type(self.response_count) is not int
            or type(self.total_bytes) is not int
            or self.response_count < 0
            or self.total_bytes < 0
        ):
            raise CrawlCheckpointError("invalid_checkpoint_counter")
        if tuple(sorted(set(self.pending_urls))) != self.pending_urls:
            raise CrawlCheckpointError("checkpoint_pending_not_canonical")
        if tuple(sorted(set(self.completed_urls))) != self.completed_urls:
            raise CrawlCheckpointError("checkpoint_completed_not_canonical")
        if set(self.pending_urls) & set(self.completed_urls):
            raise CrawlCheckpointError("checkpoint_url_sets_overlap")

    def dumps(self) -> str:
        return (
            json.dumps(
                {
                    "completed_urls": list(self.completed_urls),
                    "pending_urls": list(self.pending_urls),
                    "policy_sha256": self.policy_sha256,
                    "response_count": self.response_count,
                    "schema_version": self.schema_version,
                    "seeds_sha256": self.seeds_sha256,
                    "total_bytes": self.total_bytes,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )

    @classmethod
    def loads(cls, value: str) -> Self:
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CrawlCheckpointError("invalid_checkpoint_json") from exc
        if not isinstance(decoded, dict) or set(decoded) != {
            "completed_urls",
            "pending_urls",
            "policy_sha256",
            "response_count",
            "schema_version",
            "seeds_sha256",
            "total_bytes",
        }:
            raise CrawlCheckpointError("invalid_checkpoint_shape")
        try:
            return cls(
                policy_sha256=str(decoded["policy_sha256"]),
                seeds_sha256=str(decoded["seeds_sha256"]),
                pending_urls=tuple(_string_list(decoded["pending_urls"])),
                completed_urls=tuple(_string_list(decoded["completed_urls"])),
                response_count=_strict_int(decoded["response_count"]),
                total_bytes=_strict_int(decoded["total_bytes"]),
                schema_version=_strict_int(decoded["schema_version"]),
            )
        except (TypeError, ValueError) as exc:
            raise CrawlCheckpointError("invalid_checkpoint_value") from exc


def _strict_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError
    return value


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError
    return value


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _is_git_revision(value: str) -> bool:
    return len(value) in {40, 64} and all(character in "0123456789abcdef" for character in value)


def seeds_fingerprint(seeds: Sequence[str]) -> str:
    canonical = json.dumps(sorted(set(seeds)), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_checkpoint(policy: CrawlPolicy, seeds: Sequence[str]) -> CrawlCheckpoint:
    canonical = tuple(sorted({_without_fragment(seed) for seed in seeds}))
    if not canonical:
        raise CrawlCheckpointError("empty_crawl_seed_set")
    if len(canonical) > policy.bounds.max_urls:
        raise CrawlCheckpointError("seed_count_exceeds_url_limit")
    for seed in canonical:
        # Shape validation happens without DNS; authorization happens immediately before I/O.
        if policy.matching_rule(seed) is None:
            raise CrawlPolicyError("seed_not_allowlisted")
    return CrawlCheckpoint(
        policy_sha256=policy.fingerprint,
        seeds_sha256=seeds_fingerprint(canonical),
        pending_urls=canonical,
        completed_urls=(),
    )


def _safe_checkpoint_path(path: Path) -> Path:
    scratch = _PROJECT_SCRATCH_ROOT.resolve()
    parent = path.parent.resolve()
    if parent != scratch and scratch not in parent.parents:
        raise CrawlCheckpointError("checkpoint_must_be_below_project_tmp")
    return parent / path.name


def save_checkpoint(path: Path, checkpoint: CrawlCheckpoint) -> None:
    """Atomically save a checkpoint. Callers must place scratch paths below ``.tmp``."""

    target = _safe_checkpoint_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if stat.S_ISLNK(target.lstat().st_mode):
            raise CrawlCheckpointError("checkpoint_symlink_forbidden")
    except FileNotFoundError:
        pass
    payload = checkpoint.dumps().encode("utf-8")
    if len(payload) > CHECKPOINT_MAX_BYTES:
        raise CrawlCheckpointError("checkpoint_too_large")
    staging = target.with_name(f".{target.name}.{secrets.token_hex(8)}.pending")
    descriptor = os.open(
        staging,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, target)
    except BaseException:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
        raise


def load_checkpoint(path: Path) -> CrawlCheckpoint:
    target = _safe_checkpoint_path(path)
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if size > CHECKPOINT_MAX_BYTES:
                raise CrawlCheckpointError("checkpoint_too_large")
            payload = stream.read(CHECKPOINT_MAX_BYTES + 1)
    except OSError as exc:
        raise CrawlCheckpointError("checkpoint_read_failed") from exc
    if len(payload) > CHECKPOINT_MAX_BYTES:
        raise CrawlCheckpointError("checkpoint_too_large")
    try:
        value = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CrawlCheckpointError("checkpoint_is_not_utf8") from exc
    return CrawlCheckpoint.loads(value)


@dataclass(frozen=True, slots=True)
class CrawlRun:
    captures: tuple[Capture, ...]
    checkpoint: CrawlCheckpoint
    complete: bool


class CaptureObserver(Protocol):
    def __call__(self, capture: Capture, checkpoint: CrawlCheckpoint) -> None: ...


def crawl_http(
    *,
    seeds: Sequence[str],
    policy: CrawlPolicy,
    origin: ObservationOrigin = ObservationOrigin.PRODUCTION,
    checkpoint: CrawlCheckpoint | None = None,
    completed_captures: Sequence[Capture] = (),
    max_new_captures: int | None = None,
    transport: BoundedHttpTransport | None = None,
    observer: CaptureObserver | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
    robots_verified: bool = False,
) -> CrawlRun:
    """Crawl a deterministic chunk and return resumable scheduling state.

    ``completed_captures`` is loaded from the caller's manifest work file.  Keeping it
    separate means checkpoints never contain response bodies, cookies, credentials,
    or unredacted parser input.  The observer is called after each atomic scheduling
    transition so a CLI can persist the work manifest and then the supplied checkpoint.
    """

    if max_new_captures is not None and max_new_captures < 0:
        raise CrawlCheckpointError("invalid_chunk_size")
    if origin is ObservationOrigin.PRODUCTION and policy.robots_required and not robots_verified:
        raise CrawlPolicyError("production_robots_verification_required")
    canonical_seeds = tuple(sorted({_without_fragment(seed) for seed in seeds}))
    state = checkpoint or new_checkpoint(policy, canonical_seeds)
    if state.policy_sha256 != policy.fingerprint:
        raise CrawlCheckpointError("checkpoint_policy_mismatch")
    if state.seeds_sha256 != seeds_fingerprint(canonical_seeds):
        raise CrawlCheckpointError("checkpoint_seed_mismatch")
    capture_by_url = {capture.requested_url: capture for capture in completed_captures}
    if len(capture_by_url) != len(completed_captures):
        raise CrawlCheckpointError("duplicate_completed_capture")
    if set(capture_by_url) != set(state.completed_urls):
        raise CrawlCheckpointError("checkpoint_capture_set_mismatch")
    for url in (*state.pending_urls, *state.completed_urls):
        if policy.matching_rule(url) is None:
            raise CrawlCheckpointError("checkpoint_url_not_allowlisted")
    for capture in completed_captures:
        if capture.origin is not origin:
            raise CrawlCheckpointError("checkpoint_capture_origin_mismatch")
        for url in (
            capture.requested_url,
            capture.final_url,
            *(hop.url for hop in capture.redirect_chain),
        ):
            if policy.matching_rule(_without_fragment(url)) is None:
                raise CrawlCheckpointError("checkpoint_capture_url_not_allowlisted")
        if policy.discover_references:
            _crawlable_references(capture.metadata, capture.final_url, policy)
    if state.response_count != sum(capture.response_count for capture in completed_captures):
        raise CrawlCheckpointError("checkpoint_response_count_mismatch")
    if state.total_bytes != sum(capture.transfer_bytes for capture in completed_captures):
        raise CrawlCheckpointError("checkpoint_total_bytes_mismatch")
    client = transport or BoundedHttpTransport(policy, monotonic=monotonic)
    hard_deadline = _bounded_deadline(
        deadline,
        monotonic(),
        policy.bounds.max_run_seconds,
    )
    pending = list(state.pending_urls)
    completed = set(state.completed_urls)
    response_count = state.response_count
    total_bytes = state.total_bytes
    new_captures: list[Capture] = []
    while pending:
        if max_new_captures is not None and len(new_captures) >= max_new_captures:
            break
        if monotonic() >= hard_deadline:
            raise CrawlTransportError("crawl_deadline_exceeded")
        requested_url = pending.pop(0)
        remaining_responses = policy.bounds.max_responses - response_count
        remaining_bytes = policy.bounds.max_total_bytes - total_bytes
        if remaining_responses <= 0 or remaining_bytes <= 0:
            raise CrawlTransportError("crawl_budget_exhausted")
        try:
            response = client.fetch(
                requested_url,
                deadline=hard_deadline,
                max_responses=remaining_responses,
                max_bytes=remaining_bytes,
            )
        except CrawlError as error:
            response_count += int(getattr(client, "last_response_count", 0))
            total_bytes += int(getattr(client, "last_transfer_bytes", 0))
            if response_count > policy.bounds.max_responses:
                raise CrawlTransportError("response_count_limit_exceeded") from error
            if total_bytes > policy.bounds.max_total_bytes:
                raise CrawlTransportError("total_response_size_limit_exceeded") from error
            if error.code in {
                "crawl_budget_exhausted",
                "crawl_deadline_exceeded",
                "response_count_limit_exceeded",
                "total_response_size_limit_exceeded",
            }:
                raise CrawlTransportError(error.code) from error
            capture = Capture.create(
                origin=origin,
                requested_url=requested_url,
                status=0,
                response_count=int(getattr(client, "last_response_count", 0)),
                transfer_bytes=int(getattr(client, "last_transfer_bytes", 0)),
                error_code=error.code,
            )
            metadata = capture.metadata
        else:
            response_count += response.response_count
            total_bytes += response.transfer_bytes
            if response_count > policy.bounds.max_responses:
                raise CrawlTransportError("response_count_limit_exceeded")
            if total_bytes > policy.bounds.max_total_bytes:
                raise CrawlTransportError("total_response_size_limit_exceeded")
            try:
                metadata, sitemap = _extract_response(response, policy.internal_hosts)
            except ExtractionError as error:
                capture = Capture.create(
                    origin=origin,
                    requested_url=response.requested_url,
                    status=0,
                    response_count=response.response_count,
                    transfer_bytes=response.transfer_bytes,
                    error_code=error.code,
                )
                metadata = capture.metadata
            else:
                capture = Capture.create(
                    origin=origin,
                    requested_url=response.requested_url,
                    status=response.status,
                    final_url=response.final_url,
                    response_count=response.response_count,
                    transfer_bytes=response.transfer_bytes,
                    content_type=response.content_type,
                    response_last_modified=dict(response.headers).get("last-modified", ""),
                    response_content_language=dict(response.headers).get("content-language", ""),
                    response_robots=_response_robots(
                        dict(response.headers).get("x-robots-tag", "")
                    ),
                    body_sha256=hashlib.sha256(response.body).hexdigest(),
                    redirect_chain=response.redirect_chain,
                    metadata=metadata,
                    sitemap=sitemap,
                )
        discovered = (
            _crawlable_references(metadata, capture.final_url, policy)
            if policy.discover_references
            else ()
        )
        completed.add(requested_url)
        queued = set(pending) | completed
        for discovered_url in discovered:
            if discovered_url not in queued:
                pending.append(discovered_url)
                queued.add(discovered_url)
        if len(queued) > policy.bounds.max_urls:
            raise CrawlTransportError("url_count_limit_exceeded")
        pending.sort()
        state = CrawlCheckpoint(
            policy_sha256=policy.fingerprint,
            seeds_sha256=state.seeds_sha256,
            pending_urls=tuple(pending),
            completed_urls=tuple(sorted(completed)),
            response_count=response_count,
            total_bytes=total_bytes,
        )
        new_captures.append(capture)
        if observer is not None:
            observer(capture, state)
    return CrawlRun(captures=tuple(new_captures), checkpoint=state, complete=not pending)


def _extract_response(
    response: HttpResponse, internal_hosts: frozenset[str]
) -> tuple[PageMetadata, SitemapState]:
    content_type = response.content_type
    try:
        text = response.body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        if content_type.startswith("text/") or content_type in {
            "application/json",
            "application/ld+json",
            "application/xml",
            "application/xhtml+xml",
        }:
            raise ExtractionError("text_response_is_not_utf8") from exc
        return PageMetadata(), SitemapState()
    if content_type in {"text/html", "application/xhtml+xml"}:
        return extract_html(text, response.final_url, internal_hosts), SitemapState()
    if content_type in {"application/json", "application/ld+json"} or response.final_url.endswith(
        ".json"
    ):
        return extract_json(text, response.final_url, internal_hosts), SitemapState()
    if content_type in {"application/xml", "text/xml"} or response.final_url.endswith(".xml"):
        sitemap = extract_sitemap(text, response.final_url, internal_hosts)
        return PageMetadata(), sitemap
    if content_type.startswith("text/"):
        return extract_text(text, response.final_url, internal_hosts), SitemapState()
    return PageMetadata(), SitemapState()


def _response_robots(value: str) -> tuple[str, ...]:
    return tuple(
        sorted({directive.strip().lower() for directive in value.split(",") if directive.strip()})
    )


def _crawlable_references(
    metadata: PageMetadata, document_url: str, policy: CrawlPolicy
) -> tuple[str, ...]:
    result: set[str] = set()
    references: Iterable[Any] = getattr(metadata, "references", ())
    for reference in references:
        raw_url = getattr(reference, "url", None)
        if not isinstance(raw_url, str) or not raw_url:
            continue
        candidate = _without_fragment(urljoin(document_url, raw_url))
        if any(is_redacted_value(value) for _key, value in parse_qsl(urlsplit(candidate).query)):
            continue
        try:
            rule = policy.matching_rule(candidate)
        except CrawlPolicyError:
            # A same-host URL that violates policy is a hard error; external links are inventory
            # data but are never transport destinations.
            parsed = urlsplit(candidate)
            if parsed.hostname and _normalized_hostname(parsed.hostname) in policy.internal_hosts:
                raise
            continue
        if rule is not None:
            result.add(candidate)
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class LocalTreeSource:
    root: Path
    public_base_url: str
    repository: str
    revision: str
    source_path_prefix: str = ""

    def __post_init__(self) -> None:
        if not self.repository or any(character.isspace() for character in self.repository):
            raise CrawlPolicyError("invalid_source_repository")
        if not _is_git_revision(self.revision):
            raise CrawlPolicyError("invalid_source_revision")
        prefix = PurePosixPath(self.source_path_prefix)
        if prefix.is_absolute() or ".." in prefix.parts or "\\" in self.source_path_prefix:
            raise CrawlPolicyError("invalid_source_path_prefix")
        parsed = urlsplit(self.public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise CrawlPolicyError("invalid_public_base_url")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise CrawlPolicyError("unsafe_public_base_url")


def inventory_local_tree(
    source: LocalTreeSource,
    *,
    policy: CrawlPolicy,
    max_files: int | None = None,
) -> tuple[Capture, ...]:
    """Inventory a committed generated tree without following symlinks."""

    root = source.root.resolve(strict=True)
    if not root.is_dir():
        raise CrawlPolicyError("source_root_is_not_directory")
    file_limit = max_files if max_files is not None else policy.bounds.max_urls
    if file_limit <= 0 or file_limit > policy.bounds.max_urls:
        raise CrawlPolicyError("invalid_source_file_limit")
    candidates: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CrawlPolicyError("source_tree_contains_symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise CrawlPolicyError("source_path_escaped_root")
        candidates.append((relative, path))
    candidates.sort(key=lambda item: item[0])
    if len(candidates) > file_limit:
        raise CrawlPolicyError("source_file_count_limit_exceeded")
    total_bytes = 0
    captures: list[Capture] = []
    for relative, path in candidates:
        size = path.stat().st_size
        if size > policy.bounds.max_response_bytes:
            raise CrawlPolicyError("source_file_too_large")
        total_bytes += size
        if total_bytes > policy.bounds.max_total_bytes:
            raise CrawlPolicyError("source_tree_too_large")
        body = path.read_bytes()
        if len(body) != size:
            raise CrawlPolicyError("source_file_changed_during_inventory")
        public_url = _source_public_url(source.public_base_url, relative)
        if policy.matching_rule(public_url) is None:
            raise CrawlPolicyError("source_url_not_allowlisted")
        content_type = _source_content_type(relative)
        response = HttpResponse(
            requested_url=public_url,
            final_url=public_url,
            status=200,
            headers=(("content-type", content_type),),
            body=body,
            redirect_chain=(),
            response_count=0,
            transfer_bytes=len(body),
        )
        metadata, sitemap = _extract_response(response, policy.internal_hosts)
        captures.append(
            Capture.create(
                origin=ObservationOrigin.SOURCE,
                requested_url=public_url,
                status=200,
                source_repository=source.repository,
                source_path=(
                    f"{source.source_path_prefix.rstrip('/')}/{relative}"
                    if source.source_path_prefix
                    else relative
                ),
                content_type=content_type,
                body_sha256=hashlib.sha256(body).hexdigest(),
                metadata=metadata,
                sitemap=sitemap,
            )
        )
    return tuple(captures)


def _source_public_url(base_url: str, relative: str) -> str:
    relative_path = PurePosixPath(relative)
    if relative_path.name == "index.html":
        suffix = "/".join(relative_path.parts[:-1])
        public_path = f"{suffix}/" if suffix else ""
    else:
        public_path = relative
    base = base_url if base_url.endswith("/") else f"{base_url}/"
    return urljoin(base, percent_encode_filesystem_path(public_path))
