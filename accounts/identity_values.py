from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

SNAPSHOT_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def normalize_account_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized or None


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_snapshot_id(value: str) -> str:
    normalized = value.strip().casefold()
    if not SNAPSHOT_ID_PATTERN.fullmatch(normalized):
        raise ValueError("snapshot ID must be a lowercase SHA-256 digest")
    return normalized


def validate_safe_reference(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not SAFE_REFERENCE_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} must be a safe opaque reference")
    return normalized
