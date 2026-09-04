from dataclasses import replace
from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from core.capabilities import validate_capability
from management_auth.policies import (
    CREDENTIAL_CONFIRMATION_POLICY,
    HIGH_RISK_FRESH_CONFIRMATION_POLICY,
    FreshAuthenticatedConfirmationPolicy,
    require_high_risk_policy,
    resolved_high_risk_policy_keys,
)
from management_auth.runtime_capabilities import CREDENTIAL_CREATE


class RuntimeCredentialPolicyTests(SimpleTestCase):
    def test_registered_confirmation_is_exact_and_immutable(self) -> None:
        resolved = resolved_high_risk_policy_keys()
        self.assertEqual(
            resolved,
            frozenset(
                {
                    CREDENTIAL_CONFIRMATION_POLICY,
                    HIGH_RISK_FRESH_CONFIRMATION_POLICY,
                }
            ),
        )
        policy = require_high_risk_policy(CREDENTIAL_CONFIRMATION_POLICY)
        self.assertTrue(policy.authorize(confirmed=True))
        self.assertTrue(policy.authorize(confirmed=True, authenticated_at=timezone.now()))
        for value in (False, None, 1, "true"):
            with self.subTest(value=value):
                self.assertFalse(policy.authorize(confirmed=value))
        with self.assertRaises(PermissionError):
            require_high_risk_policy("management.credentials.unknown")

    def test_fresh_authentication_requires_recent_authenticated_time(self) -> None:
        policy = require_high_risk_policy(HIGH_RISK_FRESH_CONFIRMATION_POLICY)
        now = timezone.now()

        self.assertTrue(policy.authorize(confirmed=True, authenticated_at=now))
        self.assertFalse(policy.authorize(confirmed=False, authenticated_at=now))

    def test_fresh_authentication_rejects_stale_and_invalid_evidence(self) -> None:
        policy = FreshAuthenticatedConfirmationPolicy()
        now = timezone.now()
        evidence = (
            ("missing", None),
            ("wrong_type", "not-a-timestamp"),
            ("naive", now.replace(tzinfo=None)),
            ("future", now + timedelta(seconds=1)),
            ("stale", now - timedelta(hours=2)),
        )

        for label, authenticated_at in evidence:
            with self.subTest(evidence=label):
                self.assertFalse(
                    policy.authorize(
                        confirmed=True,
                        authenticated_at=authenticated_at,
                    )
                )

    def test_fresh_authentication_fails_closed_for_invalid_configuration(self) -> None:
        policy = FreshAuthenticatedConfirmationPolicy()

        for seconds in (0, -1, True, "900"):
            with (
                self.subTest(seconds=seconds),
                override_settings(STUDIO_HIGH_RISK_FRESHNESS_SECONDS=seconds),
            ):
                self.assertFalse(
                    policy.authorize(
                        confirmed=True,
                        authenticated_at=timezone.now(),
                    )
                )

    def test_unresolved_runtime_policy_fails_capability_validation(self) -> None:
        unresolved = replace(
            CREDENTIAL_CREATE,
            high_risk_policy="management.credentials.unknown",
        )
        self.assertIn(
            "management.credentials.create high-risk production policy is unresolved",
            validate_capability(
                unresolved,
                resolved_high_risk_policies=resolved_high_risk_policy_keys(),
            ),
        )
        self.assertEqual(
            validate_capability(
                CREDENTIAL_CREATE,
                resolved_high_risk_policies=resolved_high_risk_policy_keys(),
            ),
            (),
        )
