"""Provider-neutral webhook authentication and delivery fencing.

This module deliberately stops at the authenticity and idempotency boundary.  A future
content-sync endpoint may parse an authenticated payload and enqueue work, but this primitive
does not know about GitHub events, repositories, branches, or source content.

Only the digest of the raw body is supplied to :func:`core.execute_idempotent`.  The shared
primitive persists the scoped/key hash, request hash, and small result; neither the delivery ID,
raw body, signature, nor secret is persisted by this module.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from core.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    JsonObject,
    execute_idempotent,
)
from core.security import MAX_WEBHOOK_BODY_BYTES

MAX_BODY_BYTES: Final = MAX_WEBHOOK_BODY_BYTES
MAX_SECRET_BYTES: Final = 4 * 1024
MAX_DELIVERY_ID_BYTES: Final = 128
MAX_NAMESPACE_LENGTH: Final = 128

_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$", re.ASCII)
_SIGNATURE_PATTERN = re.compile(r"^sha256=[0-9a-f]{64}$", re.ASCII)
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_OUTCOMES = ("accepted", "replayed")


class WebhookDeliveryError(ValueError):
    """A bounded, non-sensitive webhook delivery input or fence error."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class WebhookSignatureInvalid(WebhookDeliveryError):
    """The supplied secret/signature/body combination is not valid."""

    def __init__(self) -> None:
        super().__init__("webhook_signature_invalid")


class WebhookDeliveryConflict(IdempotencyConflict):
    """A delivery ID was previously fenced for a different body digest."""

    def __init__(self) -> None:
        super().__init__("webhook_delivery_conflict")


class WebhookDeliveryInProgress(IdempotencyInProgress):
    """A committed fence needs reconciliation before it can be replayed."""

    def __init__(self) -> None:
        super().__init__("webhook_delivery_in_progress")


class WebhookDeliveryCommandFailed(RuntimeError):
    """The first-seen command failed and the delivery fence was rolled back."""

    def __init__(self) -> None:
        super().__init__("webhook_delivery_command_failed")


@dataclass(frozen=True, slots=True)
class WebhookDeliveryResult:
    """The safe result of authenticating and fencing one delivery."""

    outcome: Literal["accepted", "replayed"]
    body_sha256: str
    record_id: uuid.UUID

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise ValueError("webhook delivery outcome is invalid")
        if _DIGEST_PATTERN.fullmatch(self.body_sha256) is None:
            raise ValueError("webhook body digest is invalid")
        if not isinstance(self.record_id, uuid.UUID):
            raise ValueError("webhook delivery record ID is invalid")

    @property
    def status(self) -> Literal["accepted", "replayed"]:
        """Alias suitable for transport adapters that call the outcome a status."""

        return self.outcome

    @property
    def accepted(self) -> bool:
        return self.outcome == "accepted"

    @property
    def replayed(self) -> bool:
        return self.outcome == "replayed"

    def as_dict(self) -> dict[str, str]:
        """Return only bounded, non-secret result fields for a future adapter."""

        return {
            "outcome": self.outcome,
            "body_sha256": self.body_sha256,
            "record_id": str(self.record_id),
        }


def _invalid(reason: str) -> WebhookDeliveryError:
    return WebhookDeliveryError(reason)


def _validate_body(body: object, *, max_body_bytes: int) -> bytes:
    if (
        not isinstance(max_body_bytes, int)
        or isinstance(max_body_bytes, bool)
        or not 1 <= max_body_bytes <= MAX_BODY_BYTES
    ):
        raise _invalid("webhook_body_limit_invalid")
    if not isinstance(body, bytes):
        raise _invalid("webhook_body_invalid")
    if not body:
        raise _invalid("webhook_body_empty")
    if len(body) > max_body_bytes:
        raise _invalid("webhook_body_too_large")
    return body


def _validate_secret(secret: object) -> bytes:
    if isinstance(secret, bytes):
        value = secret
    elif isinstance(secret, str):
        try:
            value = secret.encode("utf-8")
        except UnicodeEncodeError:
            raise _invalid("webhook_secret_invalid") from None
    else:
        raise _invalid("webhook_secret_invalid")
    if not value:
        raise _invalid("webhook_secret_empty")
    if len(value) > MAX_SECRET_BYTES:
        raise _invalid("webhook_secret_too_large")
    return value


def _validate_signature(signature: object) -> str:
    if not isinstance(signature, str) or _SIGNATURE_PATTERN.fullmatch(signature) is None:
        raise WebhookSignatureInvalid()
    return signature.removeprefix("sha256=")


def _validate_namespace(namespace: object) -> str:
    if (
        not isinstance(namespace, str)
        or len(namespace) > MAX_NAMESPACE_LENGTH
        or _NAMESPACE_PATTERN.fullmatch(namespace) is None
    ):
        raise _invalid("webhook_namespace_invalid")
    return namespace


def _validate_delivery_id(delivery_id: object) -> str:
    if not isinstance(delivery_id, str) or not delivery_id:
        raise _invalid("webhook_delivery_id_invalid")
    try:
        encoded = delivery_id.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid("webhook_delivery_id_invalid") from None
    if len(encoded) > MAX_DELIVERY_ID_BYTES or any(
        character.isspace() or not character.isprintable() for character in delivery_id
    ):
        raise _invalid("webhook_delivery_id_invalid")
    return delivery_id


def _validate_body_digest(body_sha256: object) -> str:
    if not isinstance(body_sha256, str) or _DIGEST_PATTERN.fullmatch(body_sha256) is None:
        raise _invalid("webhook_body_digest_invalid")
    return body_sha256


def verify_webhook_signature(
    *,
    body: bytes,
    secret: str | bytes,
    signature: str,
    max_body_bytes: int = MAX_BODY_BYTES,
) -> str:
    """Verify an exact ``sha256=<lowercase hex>`` signature and return the body digest.

    The body is never decoded or normalized.  Signature and secret errors expose only stable
    reason codes and never include rejected input.
    """

    raw_body = _validate_body(body, max_body_bytes=max_body_bytes)
    raw_secret = _validate_secret(secret)
    provided_digest = _validate_signature(signature)
    expected_digest = hmac.new(raw_secret, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_digest, provided_digest):
        raise WebhookSignatureInvalid()
    return hashlib.sha256(raw_body).hexdigest()


def fence_webhook_delivery(
    *,
    namespace: str,
    delivery_id: str,
    body_sha256: str,
    command: Callable[[], object] | None = None,
) -> WebhookDeliveryResult:
    """Atomically fence a delivery ID against one exact raw-body digest.

    ``body_sha256`` is intentionally the only body-derived value accepted here.  Callers that
    receive an untrusted request should use :func:`authenticate_and_fence_webhook_delivery`,
    which verifies the signature before entering this idempotent boundary.
    """

    safe_namespace = _validate_namespace(namespace)
    safe_delivery_id = _validate_delivery_id(delivery_id)
    safe_digest = _validate_body_digest(body_sha256)

    if command is not None and not callable(command):
        raise _invalid("webhook_delivery_command_invalid")

    def persist_fence() -> JsonObject:
        if command is not None:
            try:
                command()
            except Exception:
                # Domain command details may contain provider data.  Keep the public boundary
                # content-free while allowing execute_idempotent to roll back the fence.
                raise WebhookDeliveryCommandFailed() from None
        return {"body_sha256": safe_digest}

    try:
        result = execute_idempotent(
            scope=safe_namespace,
            key=safe_delivery_id,
            request={"body_sha256": safe_digest},
            command=persist_fence,
        )
    except IdempotencyConflict:
        raise WebhookDeliveryConflict() from None
    except IdempotencyInProgress:
        raise WebhookDeliveryInProgress() from None

    persisted_digest = result.value.get("body_sha256")
    if persisted_digest != safe_digest:
        # This should be unreachable with core.execute_idempotent's request hash fence.  Keep the
        # adapter fail-closed if a future shared primitive ever returns a malformed result.
        raise WebhookDeliveryConflict()
    return WebhookDeliveryResult(
        outcome="replayed" if result.replayed else "accepted",
        body_sha256=safe_digest,
        record_id=result.record_id,
    )


def authenticate_and_fence_webhook_delivery(
    *,
    namespace: str,
    delivery_id: str,
    body: bytes,
    secret: str | bytes,
    signature: str,
    max_body_bytes: int = MAX_BODY_BYTES,
    command: Callable[[], object] | None = None,
) -> WebhookDeliveryResult:
    """Authenticate one raw body, then atomically fence its delivery identity."""

    body_sha256 = verify_webhook_signature(
        body=body,
        secret=secret,
        signature=signature,
        max_body_bytes=max_body_bytes,
    )
    return fence_webhook_delivery(
        namespace=namespace,
        delivery_id=delivery_id,
        body_sha256=body_sha256,
        command=command,
    )


# Explicit aliases keep the small boundary discoverable for future endpoint adapters while the
# descriptive names above remain the canonical API.
verify_and_fence_webhook_delivery = authenticate_and_fence_webhook_delivery
record_webhook_delivery = fence_webhook_delivery
