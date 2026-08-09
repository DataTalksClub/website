from __future__ import annotations

import re
import uuid
from typing import Any, cast

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from core.audit import AuditWriteContext, record_audit_event
from core.context import AuditContext as ExecutionAuditContext
from core.context import current_context
from core.idempotency import (
    JsonObject,
    canonical_json,
    canonical_json_bytes,
    canonical_json_object,
)
from core.limits import MAX_OPERATION_JSON_BYTES
from core.models import AuditEvent, Operation, RevisionConflict, RevisionedModel
from core.redaction import redact_value

_OPERATION_KIND = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class InvalidOperationTransition(RuntimeError):
    """The requested lifecycle transition is not valid from the current state."""


class OperationCancellationRequested(RuntimeError):
    """The worker must stop instead of reporting further progress or success."""


class OperationNotCancellable(RuntimeError):
    """Cancellation is disabled for this operation."""


def lock_revisioned[Revisioned: RevisionedModel](
    model: type[Revisioned],
    *,
    object_id: Any,
    expected_revision: int,
    queryset: QuerySet[Revisioned] | None = None,
    using: str = "default",
) -> Revisioned:
    """Load a revisioned row and reject an already-stale mutation.

    ``RevisionedModel.save`` performs the authoritative conditional update, so
    correctness does not depend on a backend implementing row locks.
    """

    if expected_revision < 1:
        raise ValueError("expected revision must be positive")
    selected = queryset if queryset is not None else model._default_manager.all()
    instance = selected.using(using).get(pk=object_id)
    if instance.revision != expected_revision:
        raise RevisionConflict(expected=expected_revision, actual=instance.revision)
    return instance


def _operation_write_context(
    operation: Operation,
    context: AuditWriteContext | None,
) -> AuditWriteContext:
    ambient = current_context()
    if context is None:
        execution = ExecutionAuditContext(
            request_id=operation.request_id or ambient.request_id,
            correlation_id=operation.correlation_id or ambient.correlation_id,
            job_id=ambient.job_id,
        )
        return AuditWriteContext(
            actor_id=operation.actor_id,
            api_principal_id=operation.api_principal_id,
            actor_ref=operation.actor_ref,
            execution=execution,
            idempotency_key_hash=operation.idempotency_key_hash,
        )

    supplied = context.execution
    execution = ExecutionAuditContext(
        request_id=(
            supplied.request_id if supplied and supplied.request_id else operation.request_id
        )
        or None,
        correlation_id=(
            supplied.correlation_id
            if supplied and supplied.correlation_id
            else operation.correlation_id
        )
        or None,
        job_id=(supplied.job_id if supplied and supplied.job_id else ambient.job_id),
    )
    return AuditWriteContext(
        actor_id=context.actor_id if context.actor_id is not None else operation.actor_id,
        api_principal_id=(
            context.api_principal_id
            if context.api_principal_id is not None
            else operation.api_principal_id
        ),
        actor_ref=context.actor_ref or operation.actor_ref,
        execution=execution,
        idempotency_key_hash=context.idempotency_key_hash or operation.idempotency_key_hash,
        source_ip_class=context.source_ip_class,
    )


def _safe_message(message: str) -> str:
    redacted = redact_value(message)
    if not isinstance(redacted, str):
        raise ValueError("operation message must be text")
    return redacted[:255]


def _safe_object(value: Any) -> JsonObject:
    normalized = canonical_json_object(redact_value(value))
    if len(canonical_json_bytes(normalized)) > MAX_OPERATION_JSON_BYTES:
        raise ValueError("operation result exceeds 65,536 bytes")
    return normalized


def _safe_errors(value: Any) -> list[Any]:
    normalized = canonical_json(redact_value(value))
    if not isinstance(normalized, list):
        raise ValueError("operation errors must be a JSON list")
    if len(canonical_json_bytes(normalized)) > MAX_OPERATION_JSON_BYTES:
        raise ValueError("operation errors exceed 65,536 bytes")
    return cast(list[Any], normalized)


def _audit_operation(
    operation: Operation,
    *,
    action: str,
    changes: JsonObject,
    context: AuditWriteContext | None,
    using: str,
) -> None:
    record_audit_event(
        action=action,
        target_type="core.operation",
        target_id=operation.id,
        target_label=operation.kind,
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_operation_write_context(operation, context),
        changes=changes,
        metadata={},
        using=using,
    )


def create_operation(
    *,
    kind: str,
    cancellable: bool,
    progress_total: int | None = None,
    message: str = "",
    context: AuditWriteContext | None = None,
    using: str = "default",
) -> Operation:
    if not _OPERATION_KIND.fullmatch(kind):
        raise ValueError("operation kind must be a stable lowercase identifier")
    if progress_total is not None and progress_total < 0:
        raise ValueError("operation total cannot be negative")

    context = context or AuditWriteContext()
    context.validated()
    execution = context.execution or current_context()
    operation_id = uuid.uuid4()
    with transaction.atomic(using=using):
        operation = Operation.objects.using(using).create(
            id=operation_id,
            kind=kind,
            cancellable=cancellable,
            progress_total=progress_total,
            message=_safe_message(message),
            actor_id=context.actor_id,
            api_principal_id=context.api_principal_id,
            actor_ref=context.actor_ref,
            request_id=execution.request_id if execution and execution.request_id else "",
            correlation_id=(
                execution.correlation_id if execution and execution.correlation_id else ""
            ),
            idempotency_key_hash=context.idempotency_key_hash,
        )
        _audit_operation(
            operation,
            action="core.operation.create",
            changes={"status": {"before": None, "after": Operation.Status.PENDING}},
            context=context,
            using=using,
        )
        return operation


def start_operation(
    *,
    operation_id: uuid.UUID,
    expected_revision: int,
    context: AuditWriteContext | None = None,
    using: str = "default",
) -> Operation:
    with transaction.atomic(using=using):
        operation = lock_revisioned(
            Operation,
            object_id=operation_id,
            expected_revision=expected_revision,
            using=using,
        )
        if operation.status != Operation.Status.PENDING:
            raise InvalidOperationTransition(
                f"cannot start an operation in state {operation.status}"
            )
        if operation.cancellation_requested_at is not None:
            raise OperationCancellationRequested("operation cancellation was requested")

        operation.status = Operation.Status.RUNNING
        operation.started_at = timezone.now()
        operation.revision += 1
        operation.save(
            using=using,
            update_fields=("status", "started_at", "revision", "updated_at"),
        )
        _audit_operation(
            operation,
            action="core.operation.start",
            changes={
                "revision": {"before": expected_revision, "after": operation.revision},
                "status": {"before": Operation.Status.PENDING, "after": operation.status},
            },
            context=context,
            using=using,
        )
        return operation


def update_operation_progress(
    *,
    operation_id: uuid.UUID,
    expected_revision: int,
    current: int,
    total: int | None = None,
    message: str | None = None,
    context: AuditWriteContext | None = None,
    using: str = "default",
) -> Operation:
    if current < 0 or (total is not None and total < 0):
        raise ValueError("operation progress cannot be negative")

    with transaction.atomic(using=using):
        operation = lock_revisioned(
            Operation,
            object_id=operation_id,
            expected_revision=expected_revision,
            using=using,
        )
        if operation.status != Operation.Status.RUNNING:
            raise InvalidOperationTransition(f"cannot update progress in state {operation.status}")
        if operation.cancellation_requested_at is not None:
            raise OperationCancellationRequested("operation cancellation was requested")

        next_total = operation.progress_total if total is None else total
        if next_total is not None and current > next_total:
            raise ValueError("operation progress cannot exceed its total")
        previous: JsonObject = {
            "current": operation.progress_current,
            "total": operation.progress_total,
            "message": operation.message,
        }
        operation.progress_current = current
        operation.progress_total = next_total
        if message is not None:
            operation.message = _safe_message(message)
        operation.revision += 1
        operation.save(
            using=using,
            update_fields=(
                "progress_current",
                "progress_total",
                "message",
                "revision",
                "updated_at",
            ),
        )
        _audit_operation(
            operation,
            action="core.operation.progress",
            changes={
                "revision": {"before": expected_revision, "after": operation.revision},
                "progress": {
                    "before": previous,
                    "after": {
                        "current": operation.progress_current,
                        "total": operation.progress_total,
                        "message": operation.message,
                    },
                },
            },
            context=context,
            using=using,
        )
        return operation


def request_operation_cancellation(
    *,
    operation_id: uuid.UUID,
    expected_revision: int,
    context: AuditWriteContext | None = None,
    queryset: QuerySet[Operation] | None = None,
    using: str = "default",
) -> Operation:
    with transaction.atomic(using=using):
        operation = lock_revisioned(
            Operation,
            object_id=operation_id,
            expected_revision=expected_revision,
            queryset=queryset,
            using=using,
        )
        if operation.status in Operation.TERMINAL_STATUSES:
            raise InvalidOperationTransition(
                f"cannot request cancellation in state {operation.status}"
            )
        if not operation.cancellable:
            raise OperationNotCancellable("operation does not allow cancellation")
        if operation.cancellation_requested_at is not None:
            return operation

        operation.cancellation_requested_at = timezone.now()
        operation.revision += 1
        operation.save(
            using=using,
            update_fields=("cancellation_requested_at", "revision", "updated_at"),
        )
        _audit_operation(
            operation,
            action="core.operation.request_cancellation",
            changes={
                "cancellation_requested": {"before": False, "after": True},
                "revision": {"before": expected_revision, "after": operation.revision},
            },
            context=context,
            using=using,
        )
        return operation


def cancel_operation(
    *,
    operation_id: uuid.UUID,
    expected_revision: int,
    message: str = "",
    context: AuditWriteContext | None = None,
    using: str = "default",
) -> Operation:
    with transaction.atomic(using=using):
        operation = lock_revisioned(
            Operation,
            object_id=operation_id,
            expected_revision=expected_revision,
            using=using,
        )
        if operation.status not in (Operation.Status.PENDING, Operation.Status.RUNNING):
            raise InvalidOperationTransition(
                f"cannot cancel an operation in state {operation.status}"
            )
        if not operation.cancellable:
            raise OperationNotCancellable("operation does not allow cancellation")
        if operation.cancellation_requested_at is None:
            raise InvalidOperationTransition("cancellation must be requested before completion")

        previous_status = operation.status
        operation.status = Operation.Status.CANCELLED
        operation.finished_at = timezone.now()
        operation.message = _safe_message(message)
        operation.revision += 1
        operation.save(
            using=using,
            update_fields=("status", "finished_at", "message", "revision", "updated_at"),
        )
        _audit_operation(
            operation,
            action="core.operation.cancel",
            changes={
                "revision": {"before": expected_revision, "after": operation.revision},
                "status": {"before": previous_status, "after": operation.status},
            },
            context=context,
            using=using,
        )
        return operation


def finish_operation(
    *,
    operation_id: uuid.UUID,
    expected_revision: int,
    succeeded: bool,
    result_summary: JsonObject | None = None,
    errors: list[Any] | None = None,
    message: str = "",
    context: AuditWriteContext | None = None,
    using: str = "default",
) -> Operation:
    with transaction.atomic(using=using):
        operation = lock_revisioned(
            Operation,
            object_id=operation_id,
            expected_revision=expected_revision,
            using=using,
        )
        if operation.status != Operation.Status.RUNNING:
            raise InvalidOperationTransition(
                f"cannot finish an operation in state {operation.status}"
            )
        if succeeded and operation.cancellation_requested_at is not None:
            raise OperationCancellationRequested("operation cancellation was requested")

        previous_status = operation.status
        operation.status = Operation.Status.SUCCEEDED if succeeded else Operation.Status.FAILED
        operation.finished_at = timezone.now()
        operation.message = _safe_message(message)
        operation.result_summary = _safe_object(result_summary or {})
        operation.errors = _safe_errors(errors or [])
        operation.revision += 1
        operation.save(
            using=using,
            update_fields=(
                "status",
                "finished_at",
                "message",
                "result_summary",
                "errors",
                "revision",
                "updated_at",
            ),
        )
        _audit_operation(
            operation,
            action="core.operation.finish",
            changes={
                "revision": {"before": expected_revision, "after": operation.revision},
                "status": {"before": previous_status, "after": operation.status},
                "result_summary": operation.result_summary,
                "errors": operation.errors,
            },
            context=context,
            using=using,
        )
        return operation
