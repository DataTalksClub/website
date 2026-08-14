from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import cast

from django.db import transaction
from django.test import SimpleTestCase, TestCase

from content_sync.webhook_delivery import (
    MAX_BODY_BYTES,
    MAX_SECRET_BYTES,
    WebhookDeliveryCommandFailed,
    WebhookDeliveryConflict,
    WebhookDeliveryError,
    WebhookDeliveryInProgress,
    WebhookDeliveryResult,
    WebhookSignatureInvalid,
    authenticate_and_fence_webhook_delivery,
    verify_webhook_signature,
)
from core.idempotency import hash_idempotency_key, hash_idempotency_request
from core.models import IdempotencyRecord, Operation

SECRET = b"synthetic-webhook-secret"
BODY = b'{"action":"push","bytes":[0,255]}'
NAMESPACE = "tests.webhook"
DELIVERY_ID = "delivery-1234"


def signature_for(body: bytes = BODY, secret: bytes = SECRET) -> str:
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WebhookSignatureTests(SimpleTestCase):
    def test_valid_signature_uses_byte_exact_body_and_returns_digest(self) -> None:
        body = b"raw\x00\xff\nbytes"
        self.assertEqual(
            verify_webhook_signature(
                body=body,
                secret=SECRET,
                signature=signature_for(body),
            ),
            hashlib.sha256(body).hexdigest(),
        )

    def test_wrong_body_or_secret_is_rejected_without_echoing_input(self) -> None:
        canaries = (BODY.decode("latin1"), SECRET.decode(), signature_for())
        for body, secret, signature in (
            (BODY + b"!", SECRET, signature_for()),
            (BODY, b"different-secret", signature_for()),
        ):
            with self.subTest(body=body, secret=secret):
                with self.assertRaises(WebhookSignatureInvalid) as caught:
                    verify_webhook_signature(body=body, secret=secret, signature=signature)
                self.assertEqual(str(caught.exception), "webhook_signature_invalid")
                self.assertTrue(all(canary not in str(caught.exception) for canary in canaries))

    def test_signature_requires_exact_lowercase_sha256_prefix_and_hex(self) -> None:
        valid_digest = signature_for().removeprefix("sha256=")
        malformed = (
            valid_digest,
            f"SHA256={valid_digest}",
            f"sha256:{valid_digest}",
            f"sha256={valid_digest.upper()}",
            f"sha256={valid_digest[:-1]}",
            f"sha256={valid_digest}0",
            f"sha256={valid_digest[:-1]}g",
            f"sha256={valid_digest} ",
        )
        for signature in malformed:
            with self.subTest(signature=signature):
                with self.assertRaises(WebhookSignatureInvalid):
                    verify_webhook_signature(body=BODY, secret=SECRET, signature=signature)

    def test_empty_oversized_and_ambiguous_inputs_fail_closed(self) -> None:
        cases = (
            ("empty-body", {"body": b""}, "webhook_body_empty"),
            (
                "oversized-body",
                {"body": b"x" * (MAX_BODY_BYTES + 1)},
                "webhook_body_too_large",
            ),
            ("body-type", {"body": bytearray(BODY)}, "webhook_body_invalid"),
            ("empty-secret", {"secret": b""}, "webhook_secret_empty"),
            (
                "oversized-secret",
                {"secret": b"x" * (MAX_SECRET_BYTES + 1)},
                "webhook_secret_too_large",
            ),
            ("secret-type", {"secret": object()}, "webhook_secret_invalid"),
            ("signature-type", {"signature": b"not-a-signature"}, "webhook_signature_invalid"),
        )
        for name, overrides, reason in cases:
            with self.subTest(name=name):
                kwargs: dict[str, object] = {
                    "body": BODY,
                    "secret": SECRET,
                    "signature": signature_for(),
                    **overrides,
                }
                with self.assertRaises(WebhookDeliveryError) as caught:
                    verify_webhook_signature(
                        body=cast(bytes, kwargs["body"]),
                        secret=cast(str | bytes, kwargs["secret"]),
                        signature=cast(str, kwargs["signature"]),
                    )
                self.assertEqual(str(caught.exception), reason)

        with self.assertRaises(WebhookDeliveryError) as caught:
            verify_webhook_signature(
                body=BODY,
                secret=SECRET,
                signature=signature_for(),
                max_body_bytes=MAX_BODY_BYTES + 1,
            )
        self.assertEqual(str(caught.exception), "webhook_body_limit_invalid")


class WebhookDeliveryFenceTests(TestCase):
    def _authenticate(
        self,
        *,
        namespace: str = NAMESPACE,
        delivery_id: str = DELIVERY_ID,
        body: bytes = BODY,
        secret: str | bytes = SECRET,
        signature: str | None = None,
        command: Callable[[], object] | None = None,
    ) -> WebhookDeliveryResult:
        if signature is None:
            signing_secret = secret if isinstance(secret, bytes) else secret.encode()
            signature = signature_for(body, signing_secret)
        return authenticate_and_fence_webhook_delivery(
            namespace=namespace,
            delivery_id=delivery_id,
            body=body,
            secret=secret,
            signature=signature,
            command=command,
        )

    def test_first_delivery_is_accepted_and_only_hash_bound_data_is_persisted(self) -> None:
        result = self._authenticate()

        self.assertEqual(result.outcome, "accepted")
        self.assertTrue(result.accepted)
        self.assertFalse(result.replayed)
        self.assertEqual(result.body_sha256, hashlib.sha256(BODY).hexdigest())
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        record = IdempotencyRecord.objects.get()
        self.assertEqual(record.scope, NAMESPACE)
        self.assertEqual(record.key_hash, hash_idempotency_key(NAMESPACE, DELIVERY_ID))
        self.assertEqual(
            record.request_hash,
            hash_idempotency_request(NAMESPACE, {"body_sha256": result.body_sha256}),
        )
        self.assertEqual(record.result, {"body_sha256": result.body_sha256})
        persisted = repr(record) + repr(record.result)
        self.assertNotIn(DELIVERY_ID, persisted)
        self.assertNotIn(SECRET.decode(), persisted)
        self.assertNotIn(BODY.decode("latin1"), persisted)
        self.assertNotIn("signature", persisted)

    def test_same_delivery_and_byte_exact_body_is_a_replay_without_running_command(self) -> None:
        executions = 0

        def command() -> None:
            nonlocal executions
            executions += 1

        first = self._authenticate(command=command)
        replay = self._authenticate(command=command)

        self.assertEqual(first.outcome, "accepted")
        self.assertEqual(replay.outcome, "replayed")
        self.assertEqual(first.record_id, replay.record_id)
        self.assertEqual(executions, 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_same_delivery_with_different_body_is_a_provider_neutral_conflict(self) -> None:
        changed_body = BODY + b"!"
        self._authenticate()
        with self.assertRaises(WebhookDeliveryConflict) as caught:
            self._authenticate(body=changed_body, signature=signature_for(changed_body))

        self.assertEqual(str(caught.exception), "webhook_delivery_conflict")
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_invalid_delivery_identity_never_creates_a_fence(self) -> None:
        invalid = (
            ("namespace", "UPPER"),
            ("namespace", ""),
            ("delivery_id", ""),
            ("delivery_id", "bad\x00id"),
            ("delivery_id", "x" * 129),
        )
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                with self.assertRaises(WebhookDeliveryError):
                    if field == "namespace":
                        self._authenticate(namespace=value)
                    else:
                        self._authenticate(delivery_id=value)
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_command_failure_rolls_back_fence_and_redacts_failure(self) -> None:
        canary = "command-body-secret"

        def command() -> None:
            Operation.objects.create(kind="tests.webhook.command")
            raise RuntimeError(f"provider payload {canary}")

        with self.assertRaises(WebhookDeliveryCommandFailed) as caught:
            self._authenticate(
                body=canary.encode(), signature=signature_for(canary.encode()), command=command
            )

        self.assertEqual(str(caught.exception), "webhook_delivery_command_failed")
        self.assertNotIn(canary, str(caught.exception))
        self.assertFalse(IdempotencyRecord.objects.exists())
        self.assertFalse(Operation.objects.exists())

    def test_enclosing_transaction_rollback_removes_fence_and_domain_write(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "outer rollback"):
            with transaction.atomic():
                self._authenticate(command=lambda: Operation.objects.create(kind="tests.webhook"))
                raise RuntimeError("outer rollback")

        self.assertFalse(IdempotencyRecord.objects.exists())
        self.assertFalse(Operation.objects.exists())

    def test_committed_in_progress_fence_is_not_treated_as_accepted(self) -> None:
        digest = hashlib.sha256(BODY).hexdigest()
        IdempotencyRecord.objects.create(
            scope=NAMESPACE,
            key_hash=hash_idempotency_key(NAMESPACE, DELIVERY_ID),
            request_hash=hash_idempotency_request(NAMESPACE, {"body_sha256": digest}),
        )

        with self.assertRaises(WebhookDeliveryInProgress) as caught:
            self._authenticate()
        self.assertEqual(str(caught.exception), "webhook_delivery_in_progress")
