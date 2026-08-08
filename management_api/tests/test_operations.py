from django.contrib.auth.models import Permission
from django.test import TestCase

from core.models import Operation
from core.operations import finish_operation, start_operation
from management_api.operations import (
    cancel_principal_operation,
    create_principal_operation,
    get_principal_operation,
    present_operation,
)
from management_auth.models import APIPrincipal
from management_auth.services import create_principal


class PrincipalOperationTests(TestCase):
    def setUp(self) -> None:
        permission = Permission.objects.get(
            content_type__app_label="core", codename="access_studio"
        )
        self.principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="operation service",
            identity_snapshot="service:operation",
            permissions=(permission,),
        )
        self.other = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="other operation service",
            identity_snapshot="service:operation-other",
            permissions=(permission,),
        )

    def test_operation_is_principal_scoped_revisioned_and_cancellable(self) -> None:
        operation = create_principal_operation(
            principal=self.principal,
            kind="fixture.bulk",
            cancellable=True,
            progress_total=100,
        )
        self.assertEqual(operation.api_principal_id, self.principal.id)
        self.assertIsNone(
            get_principal_operation(
                principal=self.other,
                raw_operation_id=str(operation.id),
            )
        )
        presented = present_operation(operation)
        self.assertEqual(presented["etag"], '"rev-1"')
        cancelled = cancel_principal_operation(
            principal=self.principal,
            operation_id=operation.id,
            expected_revision=operation.revision,
        )
        self.assertIsNotNone(cancelled.cancellation_requested_at)
        self.assertEqual(cancelled.revision, 2)
        self.assertEqual(Operation.objects.count(), 1)

        principal_ref = str(self.principal.id)
        self.principal.delete()
        operation.refresh_from_db()
        self.assertIsNone(operation.api_principal_id)
        self.assertIn(principal_ref, operation.actor_ref)

    def test_operation_result_and_errors_are_capped_at_65536_bytes(self) -> None:
        result_operation = create_principal_operation(
            principal=self.principal,
            kind="fixture.bound.result",
            cancellable=False,
        )
        result_operation = start_operation(
            operation_id=result_operation.id,
            expected_revision=result_operation.revision,
        )
        with self.assertRaises(ValueError):
            finish_operation(
                operation_id=result_operation.id,
                expected_revision=result_operation.revision,
                succeeded=False,
                result_summary={"values": ["x" * 1_000 for _ in range(70)]},
            )
        result_operation.refresh_from_db()
        self.assertEqual(result_operation.status, Operation.Status.RUNNING)

        error_operation = create_principal_operation(
            principal=self.principal,
            kind="fixture.bound.errors",
            cancellable=False,
        )
        error_operation = start_operation(
            operation_id=error_operation.id,
            expected_revision=error_operation.revision,
        )
        with self.assertRaises(ValueError):
            finish_operation(
                operation_id=error_operation.id,
                expected_revision=error_operation.revision,
                succeeded=False,
                errors=[{"message": "x" * 1_000} for _ in range(70)],
            )
        error_operation.refresh_from_db()
        self.assertEqual(error_operation.status, Operation.Status.RUNNING)
