from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from django.core.checks import run_checks
from django.test import SimpleTestCase

from core.capabilities import (
    AdapterMetadata,
    CapabilityRegistry,
    CapabilityRegistryError,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from studio.registry import STUDIO_HOME


class CapabilityRegistryTests(SimpleTestCase):
    def test_representative_read_capability_is_complete_and_checkable(self) -> None:
        registry = CapabilityRegistry((STUDIO_HOME,))

        self.assertEqual(registry.require("studio.home.read"), STUDIO_HOME)
        self.assertTrue(STUDIO_HOME.admin_api.test_only)
        self.assertFalse(STUDIO_HOME.studio.test_only)
        self.assertEqual([error for error in run_checks() if error.id == "studio.E001"], [])

    def test_registry_rejects_duplicate_keys_routes_and_operations(self) -> None:
        variants = (
            replace(
                STUDIO_HOME,
                studio=replace(STUDIO_HOME.studio, route="studio:other"),
                admin_api=replace(
                    STUDIO_HOME.admin_api,
                    route="/api/v1/admin/_fixtures/other",
                    operation_id="studio.other.api",
                ),
            ),
            replace(
                STUDIO_HOME,
                key="studio.other.read",
                admin_api=replace(
                    STUDIO_HOME.admin_api,
                    route="/api/v1/admin/_fixtures/other-2",
                    operation_id="studio.other2.api",
                ),
            ),
            replace(
                STUDIO_HOME,
                key="studio.third.read",
                studio=replace(STUDIO_HOME.studio, route="studio:third"),
                admin_api=replace(
                    STUDIO_HOME.admin_api,
                    route="/api/v1/admin/_fixtures/third",
                ),
            ),
            replace(
                STUDIO_HOME,
                key="studio.fourth.read",
                studio=replace(STUDIO_HOME.studio, route="studio:fourth"),
                admin_api=replace(
                    STUDIO_HOME.admin_api,
                    operation_id="studio.fourth.api",
                ),
            ),
        )
        for variant in variants:
            with self.subTest(variant=variant.key):
                with self.assertRaises(CapabilityRegistryError):
                    CapabilityRegistry((STUDIO_HOME, variant))

    def test_registry_rejects_incomplete_and_contradictory_metadata(self) -> None:
        malformed = (
            replace(STUDIO_HOME, service=cast(Any, None)),
            replace(STUDIO_HOME, description=""),
            replace(STUDIO_HOME, django_permission=""),
            replace(STUDIO_HOME, audit_action=""),
            replace(STUDIO_HOME, redacted_fields=()),
            replace(STUDIO_HOME, test_factory=cast(Any, None)),
            replace(STUDIO_HOME, studio=replace(STUDIO_HOME.studio, method="TRACE")),
            replace(STUDIO_HOME, studio=replace(STUDIO_HOME.studio, route="/public")),
            replace(
                STUDIO_HOME,
                admin_api=replace(STUDIO_HOME.admin_api, route="studio:wrong-namespace"),
            ),
            replace(STUDIO_HOME, idempotency=IdempotencyPolicy.REQUIRED),
            replace(STUDIO_HOME, concurrency=ConcurrencyPolicy.REVISION),
            replace(STUDIO_HOME, object_policy=lambda actor, target: True),
            replace(STUDIO_HOME, function_policy=cast(Any, "not-callable")),
            replace(STUDIO_HOME, object_scope=cast(Any, "not-callable")),
            replace(STUDIO_HOME, field_policy=cast(Any, "not-callable")),
            replace(STUDIO_HOME, test_only=True),
            replace(STUDIO_HOME, studio=replace(STUDIO_HOME.studio, test_only=True)),
            replace(
                STUDIO_HOME,
                key="studio.high-risk.read",
                high_risk_policy="owner.decision.pending",
            ),
        )
        for capability in malformed:
            with self.subTest(capability=capability):
                with self.assertRaises(CapabilityRegistryError):
                    CapabilityRegistry((capability,))

        unsafe_command = replace(
            STUDIO_HOME,
            key="studio.fixture.command",
            service_kind=ServiceKind.COMMAND,
        )
        with self.assertRaises(CapabilityRegistryError):
            CapabilityRegistry((unsafe_command,))

    def test_registry_total_validation_fails_closed_for_runtime_type_errors(self) -> None:
        malformed = (
            replace(STUDIO_HOME, key=cast(Any, [])),
            replace(STUDIO_HOME, description=cast(Any, None)),
            replace(STUDIO_HOME, django_permission=cast(Any, [])),
            replace(STUDIO_HOME, redacted_fields=cast(Any, "cookie")),
            replace(
                STUDIO_HOME,
                studio=cast(
                    Any,
                    AdapterMetadata(
                        route=cast(Any, []),
                        method="GET",
                        operation_id="studio.bad.html",
                    ),
                ),
            ),
            replace(
                STUDIO_HOME,
                studio=replace(STUDIO_HOME.studio, method=cast(Any, [])),
            ),
            replace(STUDIO_HOME, studio=cast(Any, object())),
        )
        for capability in malformed:
            with self.subTest(capability=capability):
                with self.assertRaises(CapabilityRegistryError):
                    CapabilityRegistry((capability,))

    def test_unknown_capability_denies_and_registry_does_not_mutate(self) -> None:
        registry = CapabilityRegistry((STUDIO_HOME,))
        with self.assertRaises(PermissionError):
            registry.require("studio.unknown")
        self.assertEqual(tuple(registry), (STUDIO_HOME,))
