"""Authenticated encryption boundary for source-managed homework answers.

This module deliberately has no dependency on Django settings. Callers must inject a
validated keyring, which keeps secret loading at the application boundary and makes the
cryptographic behavior independently testable.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

type JsonScalar = str | int | float | bool | None
type AnswerPayload = dict[str, JsonScalar | list[str]]
type AnswerEnvelope = dict[str, int | str]

_CONTEXT_PREFIX: Final = b"dtc-homework-answer:v1\0"
_KDF_SALT_PREFIX: Final = b"dtc-hw-kdf-salt:v1\0"
_KDF_INFO_PREFIX: Final = b"dtc-homework-answer-key:v1\0"
_ENVELOPE_FIELDS: Final = frozenset(
    {
        "version",
        "algorithm",
        "kdf",
        "key_id",
        "salt",
        "nonce",
        "ciphertext",
        "context_sha256",
    }
)
_IDENTIFIER_RE: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")
_LOWER_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
_BASE64URL_RE: Final = re.compile(r"[A-Za-z0-9_-]+\Z")
_KEY_BASE64_RE: Final = re.compile(r"[A-Za-z0-9+/_-]+={0,2}\Z")

_ROOT_KEY_BYTES: Final = 32
_SALT_BYTES: Final = 32
_NONCE_BYTES: Final = 12
_TAG_BYTES: Final = 16
_MAX_KEYRING_BYTES: Final = 16_384
_MAX_KEYS: Final = 32
_MAX_PAYLOAD_BYTES: Final = 16_384
_MAX_SCALAR_STRING_CHARS: Final = 8_192
_MAX_OPTION_IDS: Final = 128
_INVALID_JSON: Final = object()


class HomeworkAnswerCryptoError(Exception):
    """Base class for bounded homework-answer crypto failures."""


class HomeworkAnswerValidationError(HomeworkAnswerCryptoError):
    """The keyring, context, payload, or envelope is malformed."""


class HomeworkAnswerKeyUnavailable(HomeworkAnswerCryptoError):
    """The envelope's declared key ID is not available."""


class HomeworkAnswerDecryptionError(HomeworkAnswerCryptoError):
    """The envelope could not be authenticated or decrypted."""


@dataclass(frozen=True, slots=True)
class HomeworkAnswerKeyring:
    """Validated root keys and the key ID used for new envelopes."""

    active_key_id: str
    keys: Mapping[str, bytes] = field(repr=False)

    def __post_init__(self) -> None:
        _validate_identifier(self.active_key_id, "active key ID")
        if not isinstance(self.keys, Mapping):
            raise HomeworkAnswerValidationError("homework answer keyring is invalid")
        if not 1 <= len(self.keys) <= _MAX_KEYS:
            raise HomeworkAnswerValidationError("homework answer keyring is invalid")

        validated: dict[str, bytes] = {}
        for key_id, root_key in self.keys.items():
            _validate_identifier(key_id, "key ID")
            if not isinstance(root_key, bytes) or len(root_key) != _ROOT_KEY_BYTES:
                raise HomeworkAnswerValidationError("homework answer keyring is invalid")
            validated[key_id] = root_key

        if self.active_key_id not in validated:
            raise HomeworkAnswerValidationError("homework answer keyring is invalid")
        object.__setattr__(self, "keys", MappingProxyType(validated))

    def __repr__(self) -> str:
        key_ids = ", ".join(sorted(self.keys))
        return f"HomeworkAnswerKeyring(active_key_id={self.active_key_id!r}, key_ids=[{key_ids}])"


def parse_keyring(serialized: str) -> HomeworkAnswerKeyring:
    """Parse a strict JSON keyring without consulting settings or the environment.

    Expected shape::

        {"active_key_id": "current", "keys": {"current": "<base64 key>"}}
    """

    if not isinstance(serialized, str):
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")
    try:
        encoded = serialized.encode("utf-8")
    except UnicodeError:
        raise HomeworkAnswerValidationError("homework answer keyring is invalid") from None
    if not encoded or len(encoded) > _MAX_KEYRING_BYTES:
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")

    document = _load_strict_json(serialized)
    if document is _INVALID_JSON:
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")

    if not isinstance(document, dict) or set(document) != {"active_key_id", "keys"}:
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")
    active_key_id = document["active_key_id"]
    encoded_keys = document["keys"]
    if not isinstance(active_key_id, str) or not isinstance(encoded_keys, dict):
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")
    if not 1 <= len(encoded_keys) <= _MAX_KEYS:
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")

    keys: dict[str, bytes] = {}
    for key_id, encoded_key in encoded_keys.items():
        if not isinstance(key_id, str) or not isinstance(encoded_key, str):
            raise HomeworkAnswerValidationError("homework answer keyring is invalid")
        _validate_identifier(key_id, "key ID")
        keys[key_id] = _decode_root_key(encoded_key)
    return HomeworkAnswerKeyring(active_key_id=active_key_id, keys=keys)


def canonical_context(*, course_slug: str, homework_slug: str, question_id: str) -> bytes:
    """Build the versioned authenticated context for one answer."""

    _validate_identifier(course_slug, "course slug")
    _validate_identifier(homework_slug, "homework slug")
    _validate_identifier(question_id, "question ID")
    return _CONTEXT_PREFIX + b"\0".join(
        value.encode("utf-8") for value in (course_slug, homework_slug, question_id)
    )


def scalar_answer_payload(value: JsonScalar) -> AnswerPayload:
    """Return a validated canonical scalar-answer payload."""

    payload: AnswerPayload = {"value": value}
    _canonical_payload_bytes(payload)
    return payload


def choice_answer_payload(option_ids: Sequence[str]) -> AnswerPayload:
    """Return a validated canonical choice-answer payload."""

    if isinstance(option_ids, (str, bytes)):
        raise HomeworkAnswerValidationError("homework answer payload is invalid")
    payload: AnswerPayload = {"option_ids": list(option_ids)}
    _canonical_payload_bytes(payload)
    return payload


def encrypt_answer(
    payload: Mapping[str, object],
    *,
    course_slug: str,
    homework_slug: str,
    question_id: str,
    keyring: HomeworkAnswerKeyring,
    key_id: str | None = None,
) -> AnswerEnvelope:
    """Encrypt a canonical scalar or choice payload into a version-1 envelope."""

    _require_keyring(keyring)
    context = canonical_context(
        course_slug=course_slug,
        homework_slug=homework_slug,
        question_id=question_id,
    )
    plaintext = _canonical_payload_bytes(payload)
    selected_key_id = key_id if key_id is not None else keyring.active_key_id
    _validate_identifier(selected_key_id, "key ID")
    root_key = keyring.keys.get(selected_key_id)
    if root_key is None:
        raise HomeworkAnswerKeyUnavailable("homework answer key is unavailable")

    random_salt = secrets.token_bytes(_SALT_BYTES)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    derived_key = _derive_key(root_key=root_key, random_salt=random_salt, context=context)
    ciphertext = AESGCM(derived_key).encrypt(nonce, plaintext, context)

    return {
        "version": 1,
        "algorithm": "A256GCM",
        "kdf": "HKDF-SHA256",
        "key_id": selected_key_id,
        "salt": _encode_base64url(random_salt),
        "nonce": _encode_base64url(nonce),
        "ciphertext": _encode_base64url(ciphertext),
        "context_sha256": hashlib.sha256(context).hexdigest(),
    }


def encrypt_scalar_answer(
    value: JsonScalar,
    *,
    course_slug: str,
    homework_slug: str,
    question_id: str,
    keyring: HomeworkAnswerKeyring,
    key_id: str | None = None,
) -> AnswerEnvelope:
    return encrypt_answer(
        scalar_answer_payload(value),
        course_slug=course_slug,
        homework_slug=homework_slug,
        question_id=question_id,
        keyring=keyring,
        key_id=key_id,
    )


def encrypt_choice_answer(
    option_ids: Sequence[str],
    *,
    course_slug: str,
    homework_slug: str,
    question_id: str,
    keyring: HomeworkAnswerKeyring,
    key_id: str | None = None,
) -> AnswerEnvelope:
    return encrypt_answer(
        choice_answer_payload(option_ids),
        course_slug=course_slug,
        homework_slug=homework_slug,
        question_id=question_id,
        keyring=keyring,
        key_id=key_id,
    )


def decrypt_answer(
    envelope: Mapping[str, object],
    *,
    course_slug: str,
    homework_slug: str,
    question_id: str,
    keyring: HomeworkAnswerKeyring,
) -> AnswerPayload:
    """Authenticate and decrypt an envelope, failing closed on every mismatch."""

    _require_keyring(keyring)
    context = canonical_context(
        course_slug=course_slug,
        homework_slug=homework_slug,
        question_id=question_id,
    )
    parsed = _validate_envelope(envelope, context=context)
    root_key = keyring.keys.get(parsed["key_id"])
    if root_key is None:
        raise HomeworkAnswerKeyUnavailable("homework answer key is unavailable")

    derived_key = _derive_key(
        root_key=root_key,
        random_salt=parsed["salt"],
        context=context,
    )
    try:
        plaintext = AESGCM(derived_key).decrypt(parsed["nonce"], parsed["ciphertext"], context)
    except (InvalidTag, ValueError):
        raise HomeworkAnswerDecryptionError(
            "homework answer envelope could not be decrypted"
        ) from None

    if len(plaintext) > _MAX_PAYLOAD_BYTES:
        raise HomeworkAnswerDecryptionError("homework answer envelope could not be decrypted")
    payload = _load_strict_json(plaintext)
    if payload is _INVALID_JSON:
        raise HomeworkAnswerDecryptionError("homework answer envelope could not be decrypted")

    try:
        canonical = _canonical_payload_bytes(payload)
    except HomeworkAnswerValidationError:
        raise HomeworkAnswerDecryptionError(
            "homework answer envelope could not be decrypted"
        ) from None
    if not hmac.compare_digest(canonical, plaintext):
        raise HomeworkAnswerDecryptionError("homework answer envelope could not be decrypted")
    return payload


def _validate_envelope(envelope: Mapping[str, object], *, context: bytes) -> dict[str, str | bytes]:
    if (
        not isinstance(envelope, Mapping)
        or len(envelope) != len(_ENVELOPE_FIELDS)
        or set(envelope) != _ENVELOPE_FIELDS
    ):
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")
    if type(envelope["version"]) is not int or envelope["version"] != 1:
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")
    if envelope["algorithm"] != "A256GCM" or envelope["kdf"] != "HKDF-SHA256":
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")

    string_fields = (
        "key_id",
        "salt",
        "nonce",
        "ciphertext",
        "context_sha256",
    )
    if any(not isinstance(envelope[field], str) for field in string_fields):
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")

    key_id = envelope["key_id"]
    context_checksum = envelope["context_sha256"]
    assert isinstance(key_id, str)
    assert isinstance(context_checksum, str)
    _validate_identifier(key_id, "key ID")
    expected_checksum = hashlib.sha256(context).hexdigest()
    if not _LOWER_SHA256_RE.fullmatch(context_checksum) or not hmac.compare_digest(
        context_checksum, expected_checksum
    ):
        raise HomeworkAnswerValidationError("homework answer context does not match")

    salt = _decode_base64url(envelope["salt"], exact_bytes=_SALT_BYTES)
    nonce = _decode_base64url(envelope["nonce"], exact_bytes=_NONCE_BYTES)
    ciphertext = _decode_base64url(
        envelope["ciphertext"], max_bytes=_MAX_PAYLOAD_BYTES + _TAG_BYTES
    )
    if len(ciphertext) < _TAG_BYTES:
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")
    return {
        "key_id": key_id,
        "salt": salt,
        "nonce": nonce,
        "ciphertext": ciphertext,
    }


def _canonical_payload_bytes(payload: object) -> bytes:
    if not isinstance(payload, Mapping) or len(payload) != 1:
        raise HomeworkAnswerValidationError("homework answer payload is invalid")
    if set(payload) == {"value"}:
        _validate_scalar(payload["value"])
    elif set(payload) == {"option_ids"}:
        _validate_option_ids(payload["option_ids"])
    else:
        raise HomeworkAnswerValidationError("homework answer payload is invalid")

    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise HomeworkAnswerValidationError("homework answer payload is invalid") from None
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise HomeworkAnswerValidationError("homework answer payload is invalid")
    return encoded


def _validate_scalar(value: object) -> None:
    if isinstance(value, str):
        if len(value) > _MAX_SCALAR_STRING_CHARS:
            raise HomeworkAnswerValidationError("homework answer payload is invalid")
        return
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    raise HomeworkAnswerValidationError("homework answer payload is invalid")


def _validate_option_ids(value: object) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_OPTION_IDS:
        raise HomeworkAnswerValidationError("homework answer payload is invalid")
    seen: set[str] = set()
    for option_id in value:
        if not isinstance(option_id, str):
            raise HomeworkAnswerValidationError("homework answer payload is invalid")
        _validate_identifier(option_id, "option ID")
        if option_id in seen:
            raise HomeworkAnswerValidationError("homework answer payload is invalid")
        seen.add(option_id)


def _validate_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise HomeworkAnswerValidationError(f"{label} is invalid")


def _require_keyring(keyring: object) -> None:
    if not isinstance(keyring, HomeworkAnswerKeyring):
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")


def _derive_key(*, root_key: bytes, random_salt: bytes, context: bytes) -> bytes:
    hkdf_salt = hashlib.sha256(_KDF_SALT_PREFIX + random_salt + context).digest()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=hkdf_salt,
        info=_KDF_INFO_PREFIX + context,
    ).derive(root_key)


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(
    value: object, *, exact_bytes: int | None = None, max_bytes: int | None = None
) -> bytes:
    if not isinstance(value, str) or not _BASE64URL_RE.fullmatch(value):
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")
    if len(value) > 4 * ((_MAX_PAYLOAD_BYTES + _TAG_BYTES + 2) // 3):
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise HomeworkAnswerValidationError("homework answer envelope is invalid") from None
    if _encode_base64url(decoded) != value:
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")
    if exact_bytes is not None and len(decoded) != exact_bytes:
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")
    if max_bytes is not None and len(decoded) > max_bytes:
        raise HomeworkAnswerValidationError("homework answer envelope is invalid")
    return decoded


def _decode_root_key(value: str) -> bytes:
    if len(value) > 48 or not _KEY_BASE64_RE.fullmatch(value):
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")
    if "=" in value[:-2] or len(value) % 4 == 1:
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise HomeworkAnswerValidationError("homework answer keyring is invalid") from None
    if len(decoded) != _ROOT_KEY_BYTES:
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")
    standard = base64.b64encode(decoded).decode("ascii").rstrip("=")
    urlsafe = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if value.rstrip("=") not in {standard, urlsafe}:
        raise HomeworkAnswerValidationError("homework answer keyring is invalid")
    return decoded


def _mapping_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_strict_json(value: str | bytes) -> object:
    try:
        return json.loads(
            value,
            object_pairs_hook=_mapping_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        return _INVALID_JSON


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")
