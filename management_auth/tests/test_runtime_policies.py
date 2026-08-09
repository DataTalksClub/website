from dataclasses import replace

from django.test import SimpleTestCase

from core.capabilities import validate_capability
from management_auth.policies import (
    CREDENTIAL_CONFIRMATION_POLICY,
    require_high_risk_policy,
    resolved_high_risk_policy_keys,
)
from management_auth.runtime_capabilities import CREDENTIAL_CREATE


class RuntimeCredentialPolicyTests(SimpleTestCase):
    def test_registered_confirmation_is_exact_and_immutable(self) -> None:
        resolved = resolved_high_risk_policy_keys()
        self.assertEqual(resolved, frozenset({CREDENTIAL_CONFIRMATION_POLICY}))
        policy = require_high_risk_policy(CREDENTIAL_CONFIRMATION_POLICY)
        self.assertTrue(policy.authorize(confirmed=True))
        for value in (False, None, 1, "true"):
            with self.subTest(value=value):
                self.assertFalse(policy.authorize(confirmed=value))
        with self.assertRaises(PermissionError):
            require_high_risk_policy("management.credentials.unknown")

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
