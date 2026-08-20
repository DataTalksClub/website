import base64
import hashlib
import json
import traceback
from unittest import TestCase
from unittest.mock import patch

from courses.homework_answer_crypto import (
    HomeworkAnswerDecryptionError,
    HomeworkAnswerKeyring,
    HomeworkAnswerKeyUnavailable,
    HomeworkAnswerValidationError,
    canonical_context,
    choice_answer_payload,
    decrypt_answer,
    encrypt_answer,
    encrypt_choice_answer,
    encrypt_scalar_answer,
    parse_keyring,
    scalar_answer_payload,
)

COURSE_SLUG = "llm-zoomcamp"
HOMEWORK_SLUG = "hw1"
QUESTION_ID = "lesson-page-count"


def keyring(*, active_key_id="current", keys=None):
    return HomeworkAnswerKeyring(
        active_key_id=active_key_id,
        keys=keys or {"current": b"c" * 32},
    )


def context_kwargs(**overrides):
    values = {
        "course_slug": COURSE_SLUG,
        "homework_slug": HOMEWORK_SLUG,
        "question_id": QUESTION_ID,
    }
    values.update(overrides)
    return values


class HomeworkAnswerCryptoRoundTripTests(TestCase):
    def test_documented_aes_gcm_hkdf_envelope_vector(self):
        with patch(
            "courses.homework_answer_crypto.secrets.token_bytes",
            side_effect=[b"s" * 32, b"n" * 12],
        ):
            envelope = encrypt_scalar_answer(
                "Python",
                keyring=keyring(),
                **context_kwargs(),
            )

        self.assertEqual(
            envelope,
            {
                "version": 1,
                "algorithm": "A256GCM",
                "kdf": "HKDF-SHA256",
                "key_id": "current",
                "salt": "c3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3M",
                "nonce": "bm5ubm5ubm5ubm5u",
                "ciphertext": "nRj9d68AMidFoifs-C9ruNps-owEfLU5dGFIABEADaWjsA",
                "context_sha256": (
                    "1bb94a6b92ff9a598062228b8be1d21c3a72e38fc7c42e717c178c909ccb0654"
                ),
            },
        )

    def test_scalar_round_trip_uses_canonical_context_checksum(self):
        envelope = encrypt_scalar_answer(
            "Python",
            keyring=keyring(),
            **context_kwargs(),
        )

        payload = decrypt_answer(envelope, keyring=keyring(), **context_kwargs())

        self.assertEqual(payload, {"value": "Python"})
        context = canonical_context(**context_kwargs())
        self.assertEqual(
            context,
            b"dtc-homework-answer:v1\0llm-zoomcamp\0hw1\0lesson-page-count",
        )
        self.assertEqual(envelope["context_sha256"], hashlib.sha256(context).hexdigest())

    def test_choice_round_trip_preserves_stable_option_ids(self):
        envelope = encrypt_choice_answer(
            ["pages-72", "pages-240"],
            keyring=keyring(),
            **context_kwargs(),
        )

        payload = decrypt_answer(envelope, keyring=keyring(), **context_kwargs())

        self.assertEqual(payload, {"option_ids": ["pages-72", "pages-240"]})

    def test_scalar_and_choice_payloads_have_canonical_compact_json(self):
        self.assertEqual(
            json.dumps(
                scalar_answer_payload("Python"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            '{"value":"Python"}',
        )
        self.assertEqual(
            json.dumps(
                choice_answer_payload(["one", "two"]),
                sort_keys=True,
                separators=(",", ":"),
            ),
            '{"option_ids":["one","two"]}',
        )

    def test_random_salt_and_nonce_make_repeated_encryption_distinct(self):
        first = encrypt_scalar_answer("Python", keyring=keyring(), **context_kwargs())
        second = encrypt_scalar_answer("Python", keyring=keyring(), **context_kwargs())

        self.assertNotEqual(first["salt"], second["salt"])
        self.assertNotEqual(first["nonce"], second["nonce"])
        self.assertNotEqual(first["ciphertext"], second["ciphertext"])


class HomeworkAnswerCryptoContextTests(TestCase):
    def test_copying_envelope_to_any_other_context_fails_closed(self):
        envelope = encrypt_scalar_answer("Python", keyring=keyring(), **context_kwargs())
        mismatches = (
            {"course_slug": "de-zoomcamp"},
            {"homework_slug": "hw2"},
            {"question_id": "implementation-language"},
        )

        for mismatch in mismatches:
            with self.subTest(mismatch=mismatch):
                with self.assertRaises(HomeworkAnswerValidationError):
                    decrypt_answer(
                        envelope,
                        keyring=keyring(),
                        **context_kwargs(**mismatch),
                    )

    def test_context_components_are_strict_and_bounded(self):
        invalid_values = ("", "-leading", "trailing-", "has space", "x" * 129, "nul\0id")

        for invalid in invalid_values:
            with self.subTest(value=invalid):
                with self.assertRaises(HomeworkAnswerValidationError):
                    canonical_context(
                        course_slug=invalid,
                        homework_slug=HOMEWORK_SLUG,
                        question_id=QUESTION_ID,
                    )


class HomeworkAnswerCryptoTamperingTests(TestCase):
    def test_ciphertext_bit_flip_fails_without_exposing_crypto_error(self):
        envelope = encrypt_scalar_answer("Python", keyring=keyring(), **context_kwargs())
        tampered = dict(envelope)
        ciphertext = _decode_base64url(tampered["ciphertext"])
        ciphertext[-1] ^= 1
        tampered["ciphertext"] = _encode_base64url(ciphertext)

        with self.assertRaisesRegex(
            HomeworkAnswerDecryptionError,
            "homework answer envelope could not be decrypted",
        ):
            decrypt_answer(tampered, keyring=keyring(), **context_kwargs())

    def test_salt_nonce_and_key_id_tampering_fail_closed(self):
        envelope = encrypt_scalar_answer("Python", keyring=keyring(), **context_kwargs())
        other_keys = keyring(keys={"current": b"c" * 32, "other": b"o" * 32})

        for field in ("salt", "nonce"):
            tampered = dict(envelope)
            raw = _decode_base64url(tampered[field])
            raw[0] ^= 1
            tampered[field] = _encode_base64url(raw)
            with self.subTest(field=field):
                with self.assertRaises(HomeworkAnswerDecryptionError):
                    decrypt_answer(tampered, keyring=other_keys, **context_kwargs())

        tampered = dict(envelope)
        tampered["key_id"] = "other"
        with self.assertRaises(HomeworkAnswerDecryptionError):
            decrypt_answer(tampered, keyring=other_keys, **context_kwargs())


class HomeworkAnswerCryptoEnvelopeValidationTests(TestCase):
    def setUp(self):
        self.envelope = encrypt_scalar_answer("Python", keyring=keyring(), **context_kwargs())

    def test_malformed_envelopes_fail_with_bounded_validation_error(self):
        malformed = []
        missing = dict(self.envelope)
        missing.pop("nonce")
        malformed.append(missing)
        extra = dict(self.envelope, plaintext="Python")
        malformed.append(extra)
        malformed.extend(
            [
                dict(self.envelope, version=True),
                dict(self.envelope, version=2),
                dict(self.envelope, algorithm="AES-GCM"),
                dict(self.envelope, kdf="PBKDF2"),
                dict(self.envelope, key_id="bad key"),
                dict(self.envelope, salt="not+base64"),
                dict(self.envelope, salt=_encode_base64url(b"short")),
                dict(self.envelope, nonce=_encode_base64url(b"short")),
                dict(self.envelope, ciphertext="A"),
                dict(self.envelope, context_sha256="A" * 64),
            ]
        )

        for candidate in malformed:
            with self.subTest(candidate=candidate):
                with self.assertRaises(HomeworkAnswerValidationError) as caught:
                    decrypt_answer(candidate, keyring=keyring(), **context_kwargs())
                self.assertLess(len(str(caught.exception)), 100)
                self.assertNotIn("Python", str(caught.exception))

    def test_payload_validation_rejects_noncanonical_or_unbounded_values(self):
        invalid_payloads = (
            {},
            {"value": "ok", "option_ids": ["one"]},
            {"value": ["not", "scalar"]},
            {"value": float("nan")},
            {"value": "x" * 8_193},
            {"option_ids": []},
            {"option_ids": ["duplicate", "duplicate"]},
            {"option_ids": ["bad option"]},
            {"option_ids": [f"id-{index}" for index in range(129)]},
        )

        for payload in invalid_payloads:
            with self.subTest(payload_type=tuple(payload)):
                with self.assertRaises(HomeworkAnswerValidationError):
                    encrypt_answer(
                        payload,
                        keyring=keyring(),
                        **context_kwargs(),
                    )

    def test_missing_declared_key_does_not_fall_back_to_active_key(self):
        old_ring = keyring(active_key_id="old", keys={"old": b"o" * 32})
        envelope = encrypt_scalar_answer("Python", keyring=old_ring, **context_kwargs())

        with self.assertRaisesRegex(
            HomeworkAnswerKeyUnavailable, "homework answer key is unavailable"
        ):
            decrypt_answer(envelope, keyring=keyring(), **context_kwargs())


class HomeworkAnswerKeyringTests(TestCase):
    def test_parses_injected_json_keyring_and_does_not_reveal_keys_in_repr(self):
        root_key = bytes(range(32))
        serialized = json.dumps(
            {
                "active_key_id": "v2",
                "keys": {
                    "v1": base64.b64encode(b"1" * 32).decode("ascii"),
                    "v2": base64.urlsafe_b64encode(root_key).decode("ascii").rstrip("="),
                },
            }
        )

        parsed = parse_keyring(serialized)

        self.assertEqual(parsed.active_key_id, "v2")
        self.assertEqual(parsed.keys["v2"], root_key)
        self.assertNotIn(root_key.hex(), repr(parsed))
        self.assertNotIn(base64.b64encode(root_key).decode("ascii"), repr(parsed))

    def test_rotation_uses_active_key_and_can_decrypt_overlapping_key_ids(self):
        rotating = keyring(
            active_key_id="v2",
            keys={"v1": b"1" * 32, "v2": b"2" * 32},
        )
        current = encrypt_scalar_answer("new", keyring=rotating, **context_kwargs())
        previous = encrypt_scalar_answer("old", keyring=rotating, key_id="v1", **context_kwargs())

        self.assertEqual(current["key_id"], "v2")
        self.assertEqual(previous["key_id"], "v1")
        self.assertEqual(
            decrypt_answer(current, keyring=rotating, **context_kwargs()),
            {"value": "new"},
        )
        self.assertEqual(
            decrypt_answer(previous, keyring=rotating, **context_kwargs()),
            {"value": "old"},
        )

    def test_keyring_parser_rejects_malformed_or_unbounded_documents(self):
        valid_key = base64.b64encode(b"k" * 32).decode("ascii")
        invalid_documents = (
            "",
            "not json",
            "[]",
            json.dumps({"active_key_id": "v1"}),
            json.dumps({"active_key_id": "missing", "keys": {"v1": valid_key}}),
            json.dumps({"active_key_id": "v1", "keys": {"v1": "short"}}),
            json.dumps({"active_key_id": "v1", "keys": {"v1": valid_key}, "extra": True}),
            '{"active_key_id":"v1","active_key_id":"v2","keys":{}}',
            "x" * 16_385,
        )

        for document in invalid_documents:
            with self.subTest(document_size=len(document)):
                with self.assertRaises(HomeworkAnswerValidationError) as caught:
                    parse_keyring(document)
                self.assertLess(len(str(caught.exception)), 100)

    def test_malformed_keyring_failure_does_not_reflect_secret_input(self):
        secret = "do-not-report-this-root-key"

        try:
            parse_keyring('{"active_key_id":"v1","keys":{"v1":"' + secret)
        except HomeworkAnswerValidationError as exc:
            rendered = "".join(traceback.format_exception(exc))
        else:
            self.fail("malformed keyring unexpectedly parsed")

        self.assertNotIn(secret, rendered)

    def test_keyring_requires_exactly_32_byte_keys_and_valid_ids(self):
        cases = (
            {"active_key_id": "v1", "keys": {"v1": b"short"}},
            {"active_key_id": "bad key", "keys": {"bad key": b"k" * 32}},
            {"active_key_id": "v2", "keys": {"v1": b"k" * 32}},
            {"active_key_id": "v1", "keys": {}},
        )

        for arguments in cases:
            with self.subTest(active=arguments["active_key_id"]):
                with self.assertRaises(HomeworkAnswerValidationError):
                    HomeworkAnswerKeyring(**arguments)


class HomeworkAnswerCryptoConfidentialityTests(TestCase):
    def test_envelope_contains_no_plaintext_or_payload_field_names(self):
        plaintext = "uniquely-secret-answer-7Qp9"

        envelope = encrypt_scalar_answer(
            plaintext,
            keyring=keyring(),
            **context_kwargs(),
        )
        serialized = json.dumps(envelope, sort_keys=True)

        self.assertNotIn(plaintext, serialized)
        self.assertNotIn("value", serialized)
        self.assertNotIn("option_ids", serialized)
        self.assertEqual(
            set(envelope),
            {
                "version",
                "algorithm",
                "kdf",
                "key_id",
                "salt",
                "nonce",
                "ciphertext",
                "context_sha256",
            },
        )


def _decode_base64url(value):
    return bytearray(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))


def _encode_base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
