"""Signed anonymous identities and co-host grants for Event-linked Q&A."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

from .ids import normalize_passcode

PARTICIPANT_COOKIE = "event_qna_participant"
COHOST_COOKIE = "event_qna_cohost"
PARTICIPANT_TTL = 400 * 24 * 60 * 60
COHOST_TTL = 30 * 24 * 60 * 60


def _signer(salt: str) -> TimestampSigner:
    return TimestampSigner(key=settings.SECRET_KEY, salt=salt)


def new_participant() -> tuple[str, str]:
    participant = secrets.token_urlsafe(18)
    token = _signer("events.qna.participant").sign(participant)
    return participant, token


def participant_from_token(token: object) -> str | None:
    if not isinstance(token, str) or not token:
        return None
    try:
        value = _signer("events.qna.participant").unsign(token, max_age=PARTICIPANT_TTL)
    except (BadSignature, SignatureExpired):
        return None
    return value if isinstance(value, str) and 16 <= len(value) <= 64 else None


def participant_digest(participant: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        participant.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def new_cohost_token(session_id: str, invite_id: str) -> str:
    payload = f"{session_id}:{invite_id}"
    return _signer("events.qna.cohost").sign(payload)


def cohost_claim(token: object) -> tuple[str, str] | None:
    if not isinstance(token, str) or not token:
        return None
    try:
        payload = _signer("events.qna.cohost").unsign(token, max_age=COHOST_TTL)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, str) or payload.count(":") != 1:
        return None
    session_id, invite_id = payload.split(":", 1)
    if not session_id or not invite_id:
        return None
    return session_id, invite_id


def passcode_digest(passcode: str) -> str:
    from django.contrib.auth.hashers import make_password

    return make_password(normalize_passcode(passcode))


def passcode_matches(passcode: object, digest: str) -> bool:
    from django.contrib.auth.hashers import check_password

    normalized = normalize_passcode(passcode)
    return bool(normalized) and check_password(normalized, digest)


def constant_time_equals(left: object, right: object) -> bool:
    return hmac.compare_digest(str(left), str(right))


def new_passcode() -> str:
    alphabet = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
