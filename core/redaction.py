"""Bounded, non-mutating redaction for audit and operational metadata."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from itertools import islice

REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"
CYCLE = "[CYCLE]"

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "authorizationheader",
        "proxyauthorization",
        "cookie",
        "setcookie",
        "password",
        "passwd",
        "secret",
        "secretkey",
        "djangosecretkey",
        "apikey",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "oidctoken",
        "sessiontoken",
        "awssessiontoken",
        "awsaccesskeyid",
        "awssecretaccesskey",
        "credential",
        "credentials",
        "databaseurl",
        "webhooktoken",
        "privatekey",
        "body",
        "emailbody",
    }
)
_SENSITIVE_SUFFIXES = (
    "apikey",
    "authorization",
    "body",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "privatekey",
)
_SENSITIVE_FRAGMENTS = frozenset(
    {
        "apikey",
        "authorization",
        "body",
        "cookie",
        "credential",
        "databaseurl",
        "password",
        "passwd",
        "privatekey",
        "secret",
        "token",
    }
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb)://[^\s/:@]+:[^\s@]+@"),
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?i)\bhttps?://[^\s<>]+"),
)


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    max_depth: int = 8
    max_container_items: int = 128
    max_total_nodes: int = 2048
    max_string_length: int = 1024

    def __post_init__(self) -> None:
        if (
            min(
                self.max_depth,
                self.max_container_items,
                self.max_total_nodes,
                self.max_string_length,
            )
            < 1
        ):
            raise ValueError("Redaction bounds must be positive")


DEFAULT_REDACTION_POLICY = RedactionPolicy()


@dataclass(slots=True)
class _State:
    policy: RedactionPolicy
    canaries: tuple[str, ...]
    nodes: int = 0


def normalize_field_name(name: str) -> str:
    """Normalize case and every separator so aliases cannot bypass redaction."""

    return re.sub(r"[^a-z0-9]", "", name.casefold())


def is_sensitive_field(name: str) -> bool:
    normalized = normalize_field_name(name)
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith(_SENSITIVE_SUFFIXES)
        or any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS)
    )


def _sensitive_string(value: str, canaries: tuple[str, ...]) -> bool:
    return any(canary in value for canary in canaries) or any(
        pattern.search(value) for pattern in _SECRET_PATTERNS
    )


def is_sensitive_text(value: str) -> bool:
    """Return whether text matches the shared credential, PII, or URL policy."""

    return _sensitive_string(value, ())


def _safe_key(key: object, index: int, state: _State) -> str:
    if not isinstance(key, str):
        return f"non_string_key_{index}"
    if _sensitive_string(key, state.canaries):
        return f"redacted_key_{index}"
    if len(key) > state.policy.max_string_length:
        return f"{key[: state.policy.max_string_length]}{TRUNCATED}"
    return key


def _copy(value: object, *, depth: int, active: set[int], state: _State) -> object:
    state.nodes += 1
    if state.nodes > state.policy.max_total_nodes or depth > state.policy.max_depth:
        return TRUNCATED
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else REDACTED
    if isinstance(value, str):
        if _sensitive_string(value, state.canaries):
            return REDACTED
        if len(value) > state.policy.max_string_length:
            return f"{value[: state.policy.max_string_length]}{TRUNCATED}"
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return REDACTED

    identity = id(value)
    if identity in active:
        return CYCLE

    if isinstance(value, Mapping):
        active.add(identity)
        try:
            result: dict[str, object] = {}
            truncated = False
            entries = islice(value.items(), state.policy.max_container_items + 1)
            for index, (raw_key, item) in enumerate(entries):
                if index == state.policy.max_container_items:
                    truncated = True
                    break
                key = _safe_key(raw_key, index, state)
                if (isinstance(raw_key, str) and is_sensitive_field(raw_key)) or is_sensitive_field(
                    key
                ):
                    result[key] = REDACTED
                else:
                    result[key] = _copy(item, depth=depth + 1, active=active, state=state)
            if truncated:
                result["__truncated__"] = TRUNCATED
            return result
        finally:
            active.remove(identity)

    if isinstance(value, Sequence) and not isinstance(value, str):
        active.add(identity)
        try:
            copied: list[object] = []
            for index, item in enumerate(islice(value, state.policy.max_container_items + 1)):
                if index == state.policy.max_container_items:
                    copied.append(TRUNCATED)
                    break
                copied.append(_copy(item, depth=depth + 1, active=active, state=state))
            return tuple(copied) if isinstance(value, tuple) else copied
        finally:
            active.remove(identity)

    if isinstance(value, Set):
        # Set ordering is not stable enough for audit metadata; retain only a safe bounded marker.
        return (
            f"<redacted-{type(value).__name__}:{min(len(value), state.policy.max_container_items)}>"
        )
    return f"<{type(value).__name__}>"


def redact(
    value: object,
    *,
    canaries: Sequence[str] = (),
    policy: RedactionPolicy = DEFAULT_REDACTION_POLICY,
) -> object:
    """Return a bounded redacted copy and never mutate ``value``.

    Callers may supply test or provider-specific canaries. Empty canaries are ignored so they
    cannot match every string.
    """

    bounded_canaries = tuple(islice(canaries, MAX_CANARIES + 1))
    if len(bounded_canaries) > MAX_CANARIES:
        return REDACTED
    if any(not isinstance(canary, str) for canary in bounded_canaries):
        return REDACTED
    safe_canaries = tuple(
        canary[:MAX_CANARY_LENGTH]
        for canary in bounded_canaries
        if isinstance(canary, str) and canary
    )
    return _copy(value, depth=0, active=set(), state=_State(policy, safe_canaries))


def redact_value(value: object) -> object:
    """Persistence-facing default redaction contract for one metadata value."""

    return redact(value)


def mask_sensitive_spans(
    value: str,
    *,
    canaries: Sequence[str] = (),
    policy: RedactionPolicy = DEFAULT_REDACTION_POLICY,
) -> str:
    """Redact only the sensitive spans of one string, keeping the rest readable.

    ``redact`` answers "is any part of this sensitive?" by replacing the whole
    value, which is the right answer for a metadata field and the wrong one for
    an error message: the exception class and the status code an operator needs
    sit in the same string as the address or the URL that must not be kept.
    This applies the same ``_SECRET_PATTERNS`` span by span, so the diagnosable
    text survives and the secret does not.

    It is deliberately the same policy rather than a second one — a message
    that this cannot make safe still becomes ``[REDACTED]`` in full.
    """

    if not value:
        return ""
    masked = value[: policy.max_string_length]
    for canary in islice(canaries, MAX_CANARIES):
        if isinstance(canary, str) and canary:
            masked = masked.replace(canary[:MAX_CANARY_LENGTH], REDACTED)
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub(REDACTED, masked)
    if _sensitive_string(masked, ()):
        # The span pass did not make it safe, so fall back to the whole-value
        # answer rather than persisting something only partly cleaned.
        return REDACTED
    if len(value) > policy.max_string_length:
        return f"{masked}{TRUNCATED}"
    return masked


MAX_CANARY_LENGTH = 4096
MAX_CANARIES = 64
