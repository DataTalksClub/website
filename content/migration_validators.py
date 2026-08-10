"""Stable deconstructible validators shared by current models and historical migrations.

This module is deliberately self-contained.  Migrations serialize these import paths, so it
must not depend on mutable application modules such as ``core.redaction``.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

# Frozen from the credential, PII, and URL policy used when content migration 0001 was created.
# Additive application-policy changes belong in current model/service validation; changing this
# historical list would also change old migrations when they are replayed.
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb)://[^\s/:@]+:[^\s@]+@"),
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?i)\bhttps?://[^\s<>]+"),
)


def _is_sensitive_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SENSITIVE_TEXT_PATTERNS)


def validate_exact_public_path(value: str) -> None:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "?" in value
        or "#" in value
        or any(
            character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    ):
        raise ValidationError("Public paths must preserve one exact queryless, fragmentless path.")


def validate_storage_key_shape(value: str) -> None:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValidationError("Storage keys must be safe relative object keys.")


def validate_secret_reference(value: str) -> None:
    if value and (
        re.fullmatch(r"[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._-]{0,190}", value) is None
        or _is_sensitive_text(value)
    ):
        raise ValidationError("Secret references must be bounded opaque identifiers.")
