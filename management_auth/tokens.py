from __future__ import annotations

import base64
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from django.contrib.auth.hashers import PBKDF2PasswordHasher

from .constants import (
    DIGEST_ALGORITHM,
    DIGEST_VERSION,
    PREFIX_BYTES,
    PREFIX_LENGTH,
    SECRET_BYTES,
    SECRET_LENGTH,
    TOKEN_MARKER,
)

_PREFIX_PATTERN = rf"[A-Za-z0-9_-]{{{PREFIX_LENGTH}}}"
_SECRET_PATTERN = rf"[A-Za-z0-9_-]{{{SECRET_LENGTH}}}"
TOKEN_PATTERN = re.compile(rf"^{TOKEN_MARKER}_({_PREFIX_PATTERN})_({_SECRET_PATTERN})$")
PREFIX_PATTERN = re.compile(rf"^{_PREFIX_PATTERN}$")
_HASHER = PBKDF2PasswordHasher()
_DUMMY_DIGEST = _HASHER.encode("management-api-dummy-secret", "dtc-admin-dummy-salt")


@dataclass(frozen=True, slots=True)
class ParsedToken:
    prefix: str
    secret: str


@dataclass(frozen=True, slots=True)
class GeneratedToken:
    raw: str
    prefix: str
    secret: str


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_token(
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> GeneratedToken:
    prefix = _base64url(random_bytes(PREFIX_BYTES))
    secret = _base64url(random_bytes(SECRET_BYTES))
    if len(prefix) != PREFIX_LENGTH or len(secret) != SECRET_LENGTH:
        raise RuntimeError("credential generator returned an invalid byte count")
    return GeneratedToken(
        raw=f"{TOKEN_MARKER}_{prefix}_{secret}",
        prefix=prefix,
        secret=secret,
    )


def parse_token(raw: object) -> ParsedToken | None:
    if not isinstance(raw, str):
        return None
    match = TOKEN_PATTERN.fullmatch(raw)
    if match is None:
        return None
    return ParsedToken(prefix=match.group(1), secret=match.group(2))


def encode_secret(secret: str) -> str:
    return _HASHER.encode(secret, _HASHER.salt())


def verify_secret(secret: str, encoded: str) -> bool:
    if not encoded.startswith(f"{DIGEST_ALGORITHM}$"):
        return False
    try:
        return _HASHER.verify(secret, encoded)
    except (TypeError, ValueError):
        return False


def dummy_verify(secret: str) -> None:
    _HASHER.verify(secret, _DUMMY_DIGEST)


def hasher_contract_is_valid() -> bool:
    try:
        return (
            _HASHER.algorithm == DIGEST_ALGORITHM
            and _DUMMY_DIGEST.startswith(f"{DIGEST_ALGORITHM}$")
            and _HASHER.verify("management-api-dummy-secret", _DUMMY_DIGEST)
        )
    except (TypeError, ValueError):
        return False


def digest_metadata() -> tuple[str, int]:
    return DIGEST_ALGORITHM, DIGEST_VERSION
