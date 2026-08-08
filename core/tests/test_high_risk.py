from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any, cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import Resolver404, resolve
from django.utils import timezone

from core.capabilities import (
    AdapterMetadata,
    Capability,
    CapabilityRegistry,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from core.high_risk import (
    DeterministicHighRiskPolicy,
    HighRiskDenied,
    HighRiskEvidence,
    HighRiskPolicyAdapter,
    HighRiskRequest,
    execute_test_only_high_risk,
)
from core.idempotency import JsonObject
from core.models import AuditEvent
from core.services import ServiceContext


class FixtureService:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, evidence: HighRiskEvidence, *, context: ServiceContext) -> dict:
        del context
        self.calls += 1
        return {"status": "completed", "revision": evidence.target_revision}


def fixture_factory() -> dict[str, str]:
    return {"capability": "fixture.high-risk.execute"}


def fixture_capability(service) -> Capability:
    return Capability(
        key="fixture.high-risk.execute",
        description="Execute a deterministic high-risk test fixture",
        service_kind=ServiceKind.COMMAND,
        service=service,
        django_permission="core.execute_high_risk_fixture",
        studio=AdapterMetadata(
            route="/studio/_fixtures/high-risk/",
            method="POST",
            operation_id="fixture.high-risk.html",
            test_only=True,
        ),
        admin_api=AdapterMetadata(
            route="/api/v1/admin/_fixtures/high-risk",
            method="POST",
            operation_id="fixture.high-risk.api",
            test_only=True,
        ),
        idempotency=IdempotencyPolicy.REQUIRED,
        concurrency=ConcurrencyPolicy.REVISION,
        audit_action="fixture.high-risk.attempted",
        redacted_fields=("authorization", "cookie", "token", "email"),
        test_factory=fixture_factory,
        high_risk_policy="fixture.explicit-confirmation",
        test_only=True,
    )


_POLICY_DEFAULT = object()


class HighRiskFixtureTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="high-risk-operator")
        self.now = timezone.now()
        self.evidence = HighRiskEvidence(
            capability_key="fixture.high-risk.execute",
            actor_id=self.user.pk,
            session_id=uuid.uuid4(),
            authenticated_at=self.now,
            target_revision=7,
            scope="fixture.records",
            expected_count=3,
            impact="fixture-update",
            confirmed=True,
        )
        self.request = HighRiskRequest(
            evidence=self.evidence,
            preview_evidence=self.evidence,
            idempotency_key="fixture-attempt-86",
        )
        self.context = ServiceContext(
            request_id="request-high-risk-86",
            correlation_id="correlation-high-risk-86",
            actor_ref=f"user:{self.user.pk}",
        )
        self.service = FixtureService()
        self.capability = fixture_capability(self.service)
        self.policy = DeterministicHighRiskPolicy(
            expected=self.evidence,
            fixture_not_before=self.now - timedelta(seconds=1),
        )

    def execute(
        self,
        *,
        capability: Capability | None = None,
        request: HighRiskRequest | None = None,
        policy: HighRiskPolicyAdapter | None | object = _POLICY_DEFAULT,
        context: ServiceContext | None = None,
    ) -> JsonObject:
        selected_policy = (
            self.policy if policy is _POLICY_DEFAULT else cast(HighRiskPolicyAdapter | None, policy)
        )
        return execute_test_only_high_risk(
            capability=capability or self.capability,
            request=request or self.request,
            policy=selected_policy,
            context=context or self.context,
        )

    def assert_denied(
        self,
        *,
        reason: str,
        capability: Capability | None = None,
        request: HighRiskRequest | None = None,
        policy: HighRiskPolicyAdapter | None | object = _POLICY_DEFAULT,
        context: ServiceContext | None = None,
    ) -> None:
        with self.assertRaises(HighRiskDenied) as caught:
            self.execute(
                capability=capability,
                request=request,
                policy=policy,
                context=context,
            )
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(self.service.calls, 0)
        event = AuditEvent.objects.latest("created_at")
        self.assertEqual(event.outcome, AuditEvent.Outcome.DENIED)
        self.assertEqual(event.metadata["reason"], reason)

    def test_fixture_capability_is_complete_and_wholly_test_only(self) -> None:
        registry = CapabilityRegistry((self.capability,))
        self.assertEqual(registry.require(self.capability.key), self.capability)
        self.assertTrue(self.capability.studio.test_only)
        self.assertTrue(self.capability.admin_api.test_only)

    def test_test_only_adapters_are_not_mounted_by_runtime_urlconf(self) -> None:
        for path in (
            "/studio/_fixtures/high-risk/",
            "/api/v1/admin/_fixtures/high-risk",
            "/api/v1/admin/_fixtures/studio-home",
            "/api/v1/admin/_fixtures/audit-events",
        ):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    def test_absent_unresolved_error_stale_and_confirmation_denials_precede_service(self) -> None:
        self.assert_denied(
            reason="policy_absent",
            capability=replace(self.capability, high_risk_policy=None),
        )
        self.assert_denied(reason="policy_unresolved", policy=None)
        self.assert_denied(
            reason="policy_unresolved",
            policy=replace_policy_ref(self.policy, "fixture.unknown"),
        )
        self.assert_denied(
            reason="policy_error",
            policy=DeterministicHighRiskPolicy(
                expected=self.evidence,
                fixture_not_before=self.now,
                unavailable=True,
            ),
        )
        self.assert_denied(
            reason="fixture_session_stale",
            policy=DeterministicHighRiskPolicy(
                expected=self.evidence,
                fixture_not_before=self.now + timedelta(seconds=1),
            ),
        )
        unconfirmed = replace(self.evidence, confirmed=False)
        self.assert_denied(
            reason="confirmation_missing",
            request=replace(
                self.request,
                evidence=unconfirmed,
                preview_evidence=unconfirmed,
            ),
            policy=DeterministicHighRiskPolicy(
                expected=unconfirmed,
                fixture_not_before=self.now - timedelta(seconds=1),
            ),
        )

    def test_all_evidence_dimensions_and_preview_must_match(self) -> None:
        other_user = get_user_model().objects.create_user(username="other-high-risk-operator")
        mutations = (
            {"actor_id": other_user.pk},
            {"session_id": uuid.uuid4()},
            {"capability_key": "fixture.other.execute"},
            {"target_revision": 8},
            {"scope": "fixture.other"},
            {"expected_count": 4},
            {"impact": "fixture-other-impact"},
        )
        for mutation in mutations:
            changed = replace(self.evidence, **mutation)
            request = replace(self.request, evidence=changed, preview_evidence=changed)
            with self.subTest(mutation=mutation), self.assertRaises(HighRiskDenied):
                self.execute(request=request)
            self.assertEqual(self.service.calls, 0)
        mismatched_preview = replace(self.evidence, expected_count=4)
        self.assert_denied(
            reason="preview_mismatch",
            request=replace(self.request, preview_evidence=mismatched_preview),
        )

    def test_actor_mismatch_is_denied_and_audited_as_the_current_actor(self) -> None:
        request = replace(
            self.request,
            evidence=replace(self.evidence, actor_id=999_999),
            preview_evidence=replace(self.evidence, actor_id=999_999),
        )
        self.assert_denied(reason="actor_mismatch", request=request)
        event = AuditEvent.objects.latest("created_at")
        self.assertEqual(event.actor_id, self.user.pk)
        self.assertEqual(event.actor_ref, f"user:{self.user.pk}")

    def test_cancellation_and_replay_have_one_side_effect_and_append_only_audits(self) -> None:
        cancelled = replace(self.request, cancelled=True)
        self.assert_denied(reason="cancelled", request=cancelled)

        first = self.execute()
        second = self.execute()
        self.assertEqual(first, second)
        self.assertEqual(self.service.calls, 1)
        attempts = AuditEvent.objects.filter(action=self.capability.audit_action)
        self.assertEqual(attempts.count(), 3)
        self.assertEqual(
            list(attempts.order_by("created_at").values_list("outcome", flat=True)),
            [
                AuditEvent.Outcome.DENIED,
                AuditEvent.Outcome.SUCCEEDED,
                AuditEvent.Outcome.SUCCEEDED,
            ],
        )
        self.assertTrue(attempts.latest("created_at").metadata["replayed"])

    def test_policy_and_service_errors_are_redacted_and_audited(self) -> None:
        with self.assertRaises(ValueError):
            replace(self.evidence, impact="person@example.test")

        class FailingService:
            def __call__(self, evidence, *, context):
                del evidence, context
                raise RuntimeError("service unavailable")

        with self.assertRaises(RuntimeError):
            self.execute(capability=fixture_capability(FailingService()))
        event = AuditEvent.objects.latest("created_at")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILED)
        self.assertEqual(event.metadata["reason"], "service_failed")

    def test_invalid_runtime_evidence_types_fail_during_construction(self) -> None:
        invalid = (
            {"capability_key": cast(Any, None)},
            {"actor_id": cast(Any, True)},
            {"session_id": cast(Any, "session")},
            {"authenticated_at": cast(Any, self.now.replace(tzinfo=None))},
            {"target_revision": cast(Any, True)},
            {"expected_count": cast(Any, False)},
            {"confirmed": cast(Any, "yes")},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(self.evidence, **values)
        with self.assertRaises(ValueError):
            replace(self.request, cancelled=cast(Any, "yes"))

    def test_invalid_idempotency_policy_shape_and_ref_are_audited_before_service(self) -> None:
        for invalid_key in (cast(Any, None), "", "x" * 513):
            request = replace(self.request, idempotency_key=invalid_key)
            with self.subTest(invalid_key=type(invalid_key).__name__):
                self.assert_denied(reason="invalid_idempotency", request=request)

        class RaisingReference:
            @property
            def policy_ref(self):
                raise RuntimeError("reference unavailable")

        self.assert_denied(reason="policy_error", policy=cast(Any, RaisingReference()))

        class MalformedDecision:
            policy_ref = "fixture.explicit-confirmation"

            def authorize(self, request):
                del request
                return None

        self.assert_denied(reason="policy_error", policy=cast(Any, MalformedDecision()))

    def test_same_key_with_different_evidence_conflicts_without_second_side_effect(self) -> None:
        self.execute()
        changed = replace(self.evidence, target_revision=8)
        changed_request = replace(
            self.request,
            evidence=changed,
            preview_evidence=changed,
        )
        changed_policy = DeterministicHighRiskPolicy(
            expected=changed,
            fixture_not_before=self.now - timedelta(seconds=1),
        )
        with self.assertRaises(HighRiskDenied) as caught:
            self.execute(request=changed_request, policy=changed_policy)
        self.assertEqual(caught.exception.reason, "replay_conflict")
        self.assertEqual(self.service.calls, 1)

    def test_audit_failure_prevents_durable_service_and_idempotency_state(self) -> None:
        username = "durable-fixture-side-effect"

        class DatabaseService:
            def __call__(self, evidence, *, context):
                del evidence, context
                get_user_model().objects.create_user(username=username)
                return {"status": "created"}

        capability = fixture_capability(DatabaseService())
        with patch("core.high_risk.record_audit_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.execute(capability=capability)
        self.assertFalse(get_user_model().objects.filter(username=username).exists())
        from core.models import IdempotencyRecord

        self.assertFalse(IdempotencyRecord.objects.filter(scope=capability.key).exists())

    @override_settings(STUDIO_AUDIT_REDACTION_CANARIES=("fixture-seeded-canary-86",))
    def test_configured_canary_is_redacted_in_stored_attempt(self) -> None:
        evidence = replace(self.evidence, impact="fixture-seeded-canary-86")
        request = replace(self.request, evidence=evidence, preview_evidence=evidence)
        policy = DeterministicHighRiskPolicy(
            expected=evidence,
            fixture_not_before=self.now - timedelta(seconds=1),
        )
        self.execute(request=request, policy=policy)
        event = AuditEvent.objects.latest("created_at")
        self.assertNotIn("fixture-seeded-canary-86", str(event.metadata))
        self.assertEqual(event.metadata["impact"], "[REDACTED]")


class PolicyWithReference:
    def __init__(self, delegate, policy_ref: str) -> None:
        self.delegate = delegate
        self.policy_ref = policy_ref

    def authorize(self, request):
        return self.delegate.authorize(request)


def replace_policy_ref(policy, policy_ref: str):
    return PolicyWithReference(policy, policy_ref)
