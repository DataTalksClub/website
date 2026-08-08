"""Bounded, network-free capture of exact public URLs through Django."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Iterator
from typing import cast
from urllib.parse import urldefrag, urljoin, urlsplit

from django.http import HttpResponseBase, StreamingHttpResponse
from django.test import Client

from compatibility.extract import (
    ExtractionError,
    extract_html,
    extract_json,
    extract_sitemap,
    extract_text,
)
from compatibility.models import PageMetadata, RedirectHop, SitemapState
from compatibility.monitoring import suppress_compatibility_monitoring
from compatibility.target import TargetObservation

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_TEXT_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/xhtml+xml",
        "application/xml",
        "text/xml",
    }
)


class DjangoTargetError(RuntimeError):
    """The local target collector was configured unsafely or exceeded a bound."""


def _normalized_host(value: str) -> str:
    try:
        return value.rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DjangoTargetError("invalid_target_host") from exc


def _network_reference(url: str) -> str:
    parts = urlsplit(url)
    reference = parts.path or "/"
    return f"{reference}?{parts.query}" if parts.query else reference


def _response_robots(value: str) -> tuple[str, ...]:
    return tuple(
        sorted({directive.strip().lower() for directive in value.split(",") if directive.strip()})
    )


def _bounded_body(response: HttpResponseBase, limit: int) -> bytes:
    if isinstance(response, StreamingHttpResponse):
        if response.is_async:
            raise DjangoTargetError("target_async_stream_is_unsupported")
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in cast(Iterator[bytes], response.streaming_content):
                value = bytes(chunk)
                size += len(value)
                if size > limit:
                    raise DjangoTargetError("target_response_size_limit_exceeded")
                chunks.append(value)
        finally:
            response.close()
        return b"".join(chunks)
    body = bytes(response.content)  # type: ignore[attr-defined]
    if len(body) > limit:
        raise DjangoTargetError("target_response_size_limit_exceeded")
    return body


def _extract(
    body: bytes,
    *,
    content_type: str,
    final_url: str,
    internal_hosts: frozenset[str],
) -> tuple[PageMetadata, SitemapState]:
    if not (
        content_type.startswith("text/")
        or content_type in _TEXT_CONTENT_TYPES
        or final_url.endswith((".json", ".xml"))
    ):
        return PageMetadata(), SitemapState()
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExtractionError("text_response_is_not_utf8") from exc
    if content_type in {"text/html", "application/xhtml+xml"}:
        return extract_html(text, final_url, internal_hosts), SitemapState()
    if content_type in {"application/json", "application/ld+json"} or final_url.endswith(".json"):
        return extract_json(text, final_url, internal_hosts), SitemapState()
    if content_type in {"application/xml", "text/xml"} or final_url.endswith(".xml"):
        return PageMetadata(), extract_sitemap(text, final_url, internal_hosts)
    return extract_text(text, final_url, internal_hosts), SitemapState()


class DjangoTargetCollector:
    """Observe anonymous GET/HEAD responses without contacting the network."""

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        max_redirects: int = 4,
        max_response_bytes: int = 16 * 1024 * 1024,
        max_total_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        hosts = frozenset(_normalized_host(host) for host in allowed_hosts)
        if not hosts:
            raise DjangoTargetError("target_allowed_hosts_must_not_be_empty")
        for name, value, lower, upper in (
            ("max_redirects", max_redirects, 0, 16),
            ("max_response_bytes", max_response_bytes, 1, 64 * 1024 * 1024),
            ("max_total_bytes", max_total_bytes, 1, 512 * 1024 * 1024),
        ):
            if type(value) is not int or not lower <= value <= upper:
                raise DjangoTargetError(f"{name}_is_invalid")
        if max_total_bytes < max_response_bytes:
            raise DjangoTargetError("max_total_bytes_is_smaller_than_response_limit")
        self.allowed_hosts = hosts
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self.max_total_bytes = max_total_bytes

    def observe(
        self,
        public_url: str,
        *,
        method: str = "GET",
        follow_redirects: bool = True,
    ) -> TargetObservation:
        """Capture one exact URL through a fresh anonymous Django test client."""

        normalized_method = method.upper()
        if normalized_method not in {"GET", "HEAD"}:
            raise DjangoTargetError("target_method_must_be_get_or_head")
        requested = urlsplit(public_url)
        if (
            requested.scheme not in {"http", "https"}
            or not requested.hostname
            or requested.username is not None
            or requested.password is not None
            or requested.fragment
        ):
            raise DjangoTargetError("target_url_is_invalid")
        if _normalized_host(requested.hostname) not in self.allowed_hosts:
            raise DjangoTargetError("target_url_host_is_not_allowed")

        started = time.monotonic()
        client = Client(raise_request_exception=False)
        current_url = public_url
        seen = {public_url}
        redirect_chain: list[RedirectHop] = []
        response_count = 0
        transfer_bytes = 0
        capture_error = ""
        final_body = b""
        final_response: HttpResponseBase | None = None

        while True:
            current = urlsplit(current_url)
            hostname = current.hostname
            assert hostname is not None
            if _normalized_host(hostname) not in self.allowed_hosts:
                capture_error = "redirect_target_host_is_not_allowed"
                break
            host_header = current.netloc
            with suppress_compatibility_monitoring():
                response = client.generic(
                    normalized_method,
                    _network_reference(current_url),
                    secure=current.scheme == "https",
                    HTTP_HOST=host_header,
                    # WSGI routing still uses decoded PATH_INFO. These values retain
                    # collector provenance; edge tests must prove encoded separators.
                    RAW_URI=_network_reference(current_url),
                    REQUEST_URI=_network_reference(current_url),
                )
            response_count += 1
            body = _bounded_body(response, self.max_response_bytes)
            transfer_bytes += len(body)
            if transfer_bytes > self.max_total_bytes:
                raise DjangoTargetError("target_total_size_limit_exceeded")
            final_response = response
            final_body = body

            status = response.status_code
            if status not in _REDIRECT_STATUSES:
                break
            location = response.headers.get("Location", "")
            if not location:
                capture_error = "redirect_missing_location"
                break
            try:
                hop_url = urljoin(current_url, location)
                hop = RedirectHop(status=status, url=hop_url)
            except (ValueError, TypeError):
                capture_error = "redirect_location_is_unsafe"
                break
            redirect_chain.append(hop)
            current_url = urldefrag(hop_url)[0]
            if not follow_redirects:
                break
            if current_url in seen:
                capture_error = "redirect_loop"
                break
            seen.add(current_url)
            if len(redirect_chain) > self.max_redirects:
                capture_error = "redirect_limit_exceeded"
                break

        assert final_response is not None
        final_url = current_url
        content_type = final_response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        metadata = PageMetadata()
        sitemap = SitemapState()
        if not capture_error or capture_error in {
            "redirect_loop",
            "redirect_limit_exceeded",
            "redirect_target_host_is_not_allowed",
        }:
            try:
                metadata, sitemap = _extract(
                    final_body,
                    content_type=content_type,
                    final_url=final_url,
                    internal_hosts=self.allowed_hosts,
                )
            except ExtractionError as exc:
                capture_error = exc.code

        return TargetObservation(
            requested_url=public_url,
            raw_network_reference=_network_reference(public_url),
            status=final_response.status_code,
            final_url=final_url,
            response_count=response_count,
            transfer_bytes=transfer_bytes,
            content_type=content_type,
            response_last_modified=final_response.headers.get("Last-Modified", ""),
            response_content_language=final_response.headers.get("Content-Language", ""),
            response_robots=_response_robots(final_response.headers.get("X-Robots-Tag", "")),
            response_location=final_response.headers.get("Location", ""),
            body_sha256=hashlib.sha256(final_body).hexdigest(),
            redirect_chain=tuple(redirect_chain),
            metadata=metadata,
            sitemap=sitemap,
            capture_error=capture_error,
            elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
