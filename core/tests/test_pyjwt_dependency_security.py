"""Real cryptographic coverage for the locked PyJWT security update."""

import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import SimpleTestCase
from jwt.algorithms import RSAAlgorithm

AUDIENCE = "synthetic-course-client"
ISSUER = "synthetic-issuer"
NESTING_DEPTH = 20_000


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def deeply_nested_token(*, algorithm: str = "none", key_id: str | None = None) -> str:
    header = {"alg": algorithm, "typ": "JWT"}
    if key_id is not None:
        header["kid"] = key_id
    payload = b"[" * NESTING_DEPTH + b"0" + b"]" * NESTING_DEPTH
    return f"{base64url(json.dumps(header).encode())}.{base64url(payload)}."


def signed_token(private_key, *, expires_at=None, audience=AUDIENCE):
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(minutes=5)
    claims = {"sub": "synthetic-learner", "iss": ISSUER, "aud": audience, "exp": expires_at}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "valid"})


def verified_claims(token, public_key, *, algorithms=None, audience=AUDIENCE):
    if algorithms is None:
        algorithms = ["RS256"]
    return jwt.decode(token, public_key, algorithms=algorithms, audience=audience, issuer=ISSUER)


class PyJWTDependencySecurityTests(SimpleTestCase):
    def setUp(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_key = self.private_key.public_key()

    def test_malformed_rsa_jwk_preserves_valid_key_and_signed_token(self):
        valid_jwk = RSAAlgorithm.to_jwk(self.public_key, as_dict=True)
        valid_jwk["kid"] = "valid"
        malformed_jwk = {**valid_jwk, "kid": "malformed", "d": "AAAAAA"}

        key_set = jwt.PyJWKSet.from_dict({"keys": [malformed_jwk, valid_jwk]})

        self.assertEqual([key.key_id for key in key_set], ["valid"])
        claims = verified_claims(signed_token(self.private_key), key_set["valid"].key)
        self.assertEqual(claims["sub"], "synthetic-learner")

    def test_valid_signed_token_and_invalid_signature(self):
        token = signed_token(self.private_key)
        self.assertEqual(verified_claims(token, self.public_key)["sub"], "synthetic-learner")
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        with self.assertRaises(jwt.InvalidSignatureError):
            verified_claims(signed_token(other_key), self.public_key)

    def test_expired_wrong_audience_and_disallowed_algorithm_are_rejected(self):
        expired_at = datetime.now(UTC) - timedelta(minutes=5)
        with self.assertRaises(jwt.ExpiredSignatureError):
            verified_claims(signed_token(self.private_key, expires_at=expired_at), self.public_key)

        with self.assertRaises(jwt.InvalidAudienceError):
            wrong_audience = signed_token(self.private_key, audience="other-client")
            verified_claims(wrong_audience, self.public_key)

        with self.assertRaises(jwt.InvalidAlgorithmError):
            verified_claims(signed_token(self.private_key), self.public_key, algorithms=["HS256"])

    def test_unverified_nested_payload_raises_documented_decode_error(self):
        token = deeply_nested_token()

        with self.assertRaisesRegex(jwt.DecodeError, "Invalid payload") as raised:
            jwt.decode(token, options={"verify_signature": False})

        self.assertIsInstance(raised.exception.__cause__, RecursionError)

    def test_jwk_client_rejects_nested_payload_before_fetching_keys(self):
        token = deeply_nested_token(algorithm="RS256", key_id="synthetic-key")
        client = jwt.PyJWKClient("https://example.invalid/.well-known/jwks.json")

        with patch.object(client, "fetch_data") as fetch:
            with self.assertRaisesRegex(jwt.DecodeError, "Invalid payload") as raised:
                client.get_signing_key_from_jwt(token)

        self.assertIsInstance(raised.exception.__cause__, RecursionError)
        fetch.assert_not_called()
