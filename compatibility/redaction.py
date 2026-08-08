"""Deterministic URL redaction for durable public compatibility artifacts."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import quote, unquote_plus, urlsplit, urlunsplit

_SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:access[_-]?token|api[_-]?key|auth|authorization|code|credential|"
    r"email|jwt|password|refresh[_-]?token|secret|session|signature|sig|token)(?:$|[_-])"
)
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_OPAQUE = re.compile(
    r"(?i)^(?:bearer\s+|gh[pousr]_|github_pat_|akia|asia|eyJ)[A-Za-z0-9._~+/=-]{8,}"
)
_REDACTED = re.compile(r"^redacted-sha256-[0-9a-f]{64}$")
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_SOCIAL_URL_KEYS = frozenset(
    {
        "og:audio",
        "og:audio:secure_url",
        "og:audio:url",
        "og:image",
        "og:image:secure_url",
        "og:image:url",
        "og:url",
        "og:video",
        "og:video:secure_url",
        "og:video:url",
        "twitter:image",
        "twitter:image:src",
        "twitter:player",
        "twitter:player:stream",
    }
)


def redacted_value(value: str) -> str:
    return f"redacted-sha256-{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def is_redacted_value(value: str) -> bool:
    return _REDACTED.fullmatch(value) is not None


def value_requires_redaction(key: str, value: str) -> bool:
    split_key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return bool(_SENSITIVE_KEY.search(split_key) or _EMAIL.fullmatch(value) or _OPAQUE.match(value))


def is_url_valued_social_key(key: str) -> bool:
    """Return whether an OpenGraph/Twitter field has URL semantics."""

    return key in _SOCIAL_URL_KEYS or key.startswith("twitter:app:url:")


def fragment_requires_redaction(value: str) -> bool:
    """Detect secrets in ordinary and query-shaped URL/DOM fragments."""

    decoded = unquote_plus(value)
    if value_requires_redaction("fragment", decoded):
        return True
    candidates = [decoded]
    if "?" in decoded:
        candidates.append(decoded.split("?", 1)[1])
    for candidate in candidates:
        for raw_part in candidate.split("&"):
            raw_key, separator, raw_value = raw_part.partition("=")
            if not separator:
                continue
            key = raw_key.lstrip("#/?")
            if value_requires_redaction(key, raw_value):
                return True
    return False


def redact_fragment(value: str) -> str:
    """Replace a sensitive URL or DOM fragment with a stable digest."""

    return redacted_value(value) if fragment_requires_redaction(value) else value


def url_contains_unredacted_sensitive_value(value: str) -> bool:
    """Check URL-like query/fragment secrets without treating path encoding as redaction."""

    normalized = normalize_url_path(value)
    return redact_url(value) != normalized


def percent_encode_path(value: str) -> str:
    """Encode a URL path without changing existing valid percent escapes.

    The generated-path contract uses ``/`` and ``@`` as its only non-default safe
    path characters. Existing escapes retain their exact hexadecimal case so this
    operation is deterministic and does not silently rewrite URL identity.
    """

    parts: list[str] = []
    start = 0
    for match in _PERCENT_ESCAPE.finditer(value):
        parts.append(quote(value[start : match.start()], safe="/@"))
        parts.append(match.group())
        start = match.end()
    parts.append(quote(value[start:], safe="/@"))
    return "".join(parts)


def percent_encode_filesystem_path(value: str) -> str:
    """Encode a generated filesystem path exactly like the committed path contract."""

    return quote(value, safe="/@")


def normalize_url_path(value: str) -> str:
    """Percent-encode only an absolute URL's path component."""

    parsed = urlsplit(value)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            percent_encode_path(parsed.path),
            parsed.query,
            parsed.fragment,
        )
    )


def redact_url(value: str) -> str:
    """Preserve URL structure/order while replacing only sensitive values with stable digests."""

    parsed = urlsplit(normalize_url_path(value))
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url_contains_credentials")
    parts: list[str] = []
    for raw_part in parsed.query.split("&") if parsed.query else ():
        raw_key, separator, raw_value = raw_part.partition("=")
        key = unquote_plus(raw_key)
        decoded_value = unquote_plus(raw_value)
        if value_requires_redaction(key, decoded_value) and not _REDACTED.fullmatch(decoded_value):
            raw_value = redacted_value(raw_value if separator else raw_part)
            separator = "="
        parts.append(raw_key + (separator + raw_value if separator else ""))
    fragment = redact_fragment(parsed.fragment)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "&".join(parts), fragment))
