from __future__ import annotations

import uuid

from django.db.models import QuerySet

from core.audit import AuditWriteContext
from core.models import Operation
from core.operations import create_operation, request_operation_cancellation
from management_auth.models import APIPrincipal

from .concurrency import revision_etag


def principal_operations(principal: APIPrincipal) -> QuerySet[Operation]:
    return Operation.objects.filter(api_principal=principal)


def create_principal_operation(
    *,
    principal: APIPrincipal,
    kind: str,
    cancellable: bool,
    progress_total: int | None = None,
) -> Operation:
    return create_operation(
        kind=kind,
        cancellable=cancellable,
        progress_total=progress_total,
        context=AuditWriteContext(
            api_principal_id=principal.id,
            actor_ref=f"api_principal:{principal.id}",
        ),
    )


def get_principal_operation(
    *,
    principal: APIPrincipal,
    raw_operation_id: str,
) -> Operation | None:
    try:
        operation_id = uuid.UUID(raw_operation_id)
    except ValueError:
        return None
    return principal_operations(principal).filter(pk=operation_id).first()


def cancel_principal_operation(
    *,
    principal: APIPrincipal,
    operation_id: uuid.UUID,
    expected_revision: int,
) -> Operation:
    return request_operation_cancellation(
        operation_id=operation_id,
        expected_revision=expected_revision,
        queryset=principal_operations(principal),
        context=AuditWriteContext(
            api_principal_id=principal.id,
            actor_ref=f"api_principal:{principal.id}",
        ),
    )


def present_operation(operation: Operation) -> dict:
    return {
        "id": str(operation.id),
        "kind": operation.kind,
        "status": operation.status,
        "progress": {
            "current": operation.progress_current,
            "total": operation.progress_total,
        },
        "cancellable": operation.cancellable,
        "cancellation_requested": operation.cancellation_requested_at is not None,
        "message": operation.message,
        "result": operation.result_summary,
        "errors": operation.errors,
        "revision": operation.revision,
        "etag": revision_etag(operation.revision),
    }
