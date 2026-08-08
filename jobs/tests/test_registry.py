import math

from django.test import SimpleTestCase
from django_q.models import Schedule  # type: ignore[import-untyped]

from jobs.registry import (
    RegistryError,
    ScheduleDefinition,
    validate_payload,
)


class RegistryTests(SimpleTestCase):
    def test_payload_accepts_scalar_domain_identifiers(self) -> None:
        payload = {
            "email_delivery_id": "123e4567-e89b-12d3-a456-426614174000",
            "registration_ids": ["one", "two"],
            "attempt": 2,
        }
        self.assertEqual(validate_payload(payload), payload)

    def test_payload_rejects_protected_keys_without_rejecting_safe_ids(self) -> None:
        for key in (
            "authorization",
            "access_token",
            "request-body",
            "password",
            "cookie_value",
        ):
            with self.subTest(key=key), self.assertRaises(RegistryError):
                validate_payload({key: "redaction-canary"})

    def test_payload_rejects_camel_case_and_plaintext_sensitive_identifier_bypasses(self) -> None:
        for key in (
            "accessToken",
            "requestBody",
            "xApiKey",
            "password_id",
            "password_hash",
            "emailDeliveryId",
        ):
            with self.subTest(key=key), self.assertRaises(RegistryError):
                validate_payload({key: "plain-secret-canary"})

        opaque_id = "123e4567-e89b-12d3-a456-426614174000"
        self.assertEqual(
            validate_payload({"emailDeliveryId": opaque_id}),
            {"emailDeliveryId": opaque_id},
        )

    def test_payload_rejects_protected_values_under_innocent_keys(self) -> None:
        values = (
            "Bearer redaction-canary",
            "https://example.invalid/private-link",
            "person@example.invalid",
            "abcdefgh.ijklmnop.qrstuvwx",
            "AKIAABCDEFGHIJKLMNOP",
            "ghp_abcdefghijklmnopqrstuvwxyz",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(RegistryError):
                validate_payload({"reference": value})

    def test_payload_rejects_non_finite_numbers_and_unbounded_shapes(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(RegistryError):
                validate_payload({"number": value})
        with self.assertRaises(RegistryError):
            validate_payload(
                {"nested": {"next": {"x": {"y": {"z": {"a": {"b": {"c": {"d": 1}}}}}}}}}
            )

    def test_code_schedules_require_owned_prefix_and_bounded_interval(self) -> None:
        valid = ScheduleDefinition(
            key="dtc:test-schedule",
            func="jobs.tasks.sweep_and_relay",
            schedule_type=Schedule.MINUTES,
            minutes=1,
        )
        self.assertEqual(valid.key, "dtc:test-schedule")
        for key, minutes in (("third-party", 1), ("dtc:invalid", 0)):
            with self.subTest(key=key, minutes=minutes), self.assertRaises(RegistryError):
                ScheduleDefinition(
                    key=key,
                    func="jobs.tasks.sweep_and_relay",
                    schedule_type=Schedule.MINUTES,
                    minutes=minutes,
                )
