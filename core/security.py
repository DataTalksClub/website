"""Shared, decision-free input and boundary guards.

The helpers in this module deliberately do not know about accounts, roles, or
business operations.  They provide the small pieces that every presentation
boundary can use while the owning service remains responsible for policy.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit

from core.redaction import redact

MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
MAX_WEBHOOK_BODY_BYTES = 256 * 1024
MAX_JSON_DEPTH = 8
MAX_JSON_ITEMS = 128
MAX_JSON_STRING_BYTES = 16 * 1024
MAX_URL_LENGTH = 2_048

PRIVATE_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.google.com",
        "instance-data.ec2.internal",
    }
)


class UnsafeInputError(ValueError):
    """Raised when input crosses a shared non-identity safety boundary."""


def _validate_text(value: object, *, maximum: int, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise UnsafeInputError(f"invalid {label}")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise UnsafeInputError(f"invalid {label}")
    return value


def _ip_is_private(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # ``is_private`` does not include every non-routable class on all Python
    # versions, so explicitly include unspecified, loopback, link-local,
    # multicast and reserved ranges as well.
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    )


def _hostname_is_private(hostname: str, *, resolve: bool) -> bool:
    normalized = hostname.rstrip(".").casefold()
    if normalized in PRIVATE_HOSTNAMES or normalized.endswith(".localhost"):
        return True
    if _ip_is_private(normalized):
        return True
    if not resolve:
        return False
    try:
        addresses = {
            str(result[4][0])
            for result in socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
        }
    except (OSError, UnicodeError):
        # A failed lookup is not treated as private here; callers can decide
        # whether an unavailable DNS result should fail closed.  The outbound
        # validator below does so.
        return False
    return any(_ip_is_private(address) for address in addresses)


def validate_url(
    value: object,
    *,
    allow_relative: bool = False,
    allow_mailto: bool = False,
    allow_git: bool = False,
    reject_private: bool = False,
    resolve_private: bool = False,
) -> str:
    """Validate an HTTP/link URL without fetching it.

    Relative paths are accepted only when explicitly requested and can never
    become protocol-relative URLs.  Absolute URLs reject credentials, control
    characters, and private/link-local targets.  This function has no network
    side effects unless ``resolve_private`` is requested.
    """

    text = _validate_text(value, maximum=MAX_URL_LENGTH, label="URL")
    if "\\" in text or any(character.isspace() for character in text):
        raise UnsafeInputError("invalid URL")
    parsed = urlsplit(text)
    if not parsed.scheme:
        if not allow_relative or not text.startswith("/") or text.startswith("//"):
            raise UnsafeInputError("invalid URL")
        decoded_path = unquote(parsed.path)
        parts = decoded_path.split("/")
        if any(part in {".", ".."} for part in parts) or any(not part for part in parts[1:-1]):
            raise UnsafeInputError("invalid URL path")
        return text

    allowed_schemes = {"http", "https"}
    if allow_mailto:
        allowed_schemes.add("mailto")
    if allow_git:
        allowed_schemes.add("git")
    scheme = parsed.scheme.casefold()
    if scheme not in allowed_schemes:
        raise UnsafeInputError("invalid URL scheme")
    if scheme == "mailto":
        if not parsed.path or parsed.username is not None or parsed.password is not None:
            raise UnsafeInputError("invalid mailto URL")
        return text
    if not parsed.hostname:
        raise UnsafeInputError("invalid URL")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeInputError("URL credentials are not allowed")
    if parsed.fragment and parsed.scheme.casefold() in {"http", "https", "git"}:
        # Fragments are browser-only and are a common place to smuggle markup
        # or sensitive state into copied links.
        raise UnsafeInputError("URL fragments are not allowed")
    hostname = parsed.hostname.casefold().rstrip(".")
    if reject_private and _hostname_is_private(hostname, resolve=resolve_private):
        raise UnsafeInputError("private URL target is not allowed")
    return text


def validate_outbound_url(value: object) -> str:
    """Validate a provider/SSRF destination and fail closed on DNS errors."""

    text = validate_url(value, reject_private=True, resolve_private=True)
    parsed = urlsplit(text)
    if parsed.scheme != "https" or parsed.port not in {None, 443}:
        raise UnsafeInputError("outbound URL must use HTTPS on port 443")
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except (OSError, UnicodeError):
        raise UnsafeInputError("outbound URL could not be resolved") from None
    if not addresses:
        raise UnsafeInputError("outbound URL could not be resolved")
    if _hostname_is_private(parsed.hostname or "", resolve=True):
        raise UnsafeInputError("private URL target is not allowed")
    return text


def validate_relative_path(value: object) -> str:
    """Accept one normalized relative path and reject traversal/symlinks markers."""

    text = _validate_text(value, maximum=MAX_URL_LENGTH, label="path")
    decoded = unquote(text)
    if (
        not decoded
        or decoded.startswith(("/", "\\"))
        or "\\" in decoded
        or any(part in {"", ".", ".."} for part in decoded.split("/"))
    ):
        raise UnsafeInputError("path traversal is not allowed")
    return decoded


def resolve_bounded_path(root: Path, relative: object) -> Path:
    """Resolve a relative path below ``root`` and reject symlink escapes."""

    safe_relative = validate_relative_path(relative)
    root_resolved = root.resolve(strict=True)
    candidate = root_resolved.joinpath(*safe_relative.split("/"))
    # Check every existing component, not only the final file.  This prevents
    # an attacker from swapping an intermediate directory for a symlink.
    current = root_resolved
    for component in safe_relative.split("/"):
        current = current / component
        if current.is_symlink():
            raise UnsafeInputError("symlink paths are not allowed")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root_resolved):
        raise UnsafeInputError("path escapes its boundary")
    return resolved


def validate_json_shape(
    value: object,
    *,
    max_depth: int = MAX_JSON_DEPTH,
    max_items: int = MAX_JSON_ITEMS,
    max_string_bytes: int = MAX_JSON_STRING_BYTES,
) -> None:
    """Bound JSON nesting, fan-out, and string size before domain parsing."""

    def walk(item: object, depth: int) -> None:
        if depth > max_depth:
            raise UnsafeInputError("JSON nesting limit exceeded")
        if isinstance(item, str):
            if len(item.encode("utf-8")) > max_string_bytes:
                raise UnsafeInputError("JSON string limit exceeded")
            return
        if isinstance(item, Mapping):
            if len(item) > max_items:
                raise UnsafeInputError("JSON object limit exceeded")
            for key, child in item.items():
                if not isinstance(key, str) or len(key.encode("utf-8")) > max_string_bytes:
                    raise UnsafeInputError("JSON key limit exceeded")
                walk(child, depth + 1)
            return
        if isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray, str)):
            if len(item) > max_items:
                raise UnsafeInputError("JSON array limit exceeded")
            for child in item:
                walk(child, depth + 1)

    walk(value, 0)


def neutralize_csv_formula(value: object) -> object:
    """Return a spreadsheet-safe cell without changing ordinary text."""

    if not isinstance(value, str) or not value:
        return value
    stripped = value.lstrip(" \t")
    if stripped.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def safe_artifact(value: object) -> object:
    """Apply the shared bounded redaction policy to logs/metrics/traces."""

    return redact(value)
