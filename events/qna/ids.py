"""Opaque identifiers used by the website-native Q&A store."""

from __future__ import annotations

import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def opaque_id() -> str:
    """Return a sortable, non-guessable 26-character ULID-like identifier."""

    timestamp = int(time.time() * 1_000) & ((1 << 48) - 1)
    random_bits = secrets.randbits(80)
    value = (timestamp << 80) | random_bits
    chars = []
    for shift in range(125, -1, -5):
        chars.append(_CROCKFORD[(value >> shift) & 0x1F] if shift < 128 else "0")
    return "".join(chars)


def normalize_cohost_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def normalize_passcode(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(value.strip().upper().split()).replace("-", "")
