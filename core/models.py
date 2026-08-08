from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class AppendOnlyViolation(RuntimeError):
    """Raised when application code attempts to rewrite immutable evidence."""


class RevisionConflict(RuntimeError):
    """Raised when a compare-and-swap mutation uses a stale revision."""

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"stale revision: expected {expected}, current revision is {actual}")


class AppendOnlyQuerySet(models.QuerySet[Any]):
    def update(self, **kwargs: Any) -> int:
        retention_field = {
            AuditEvent: "actor",
            OperationalSettingRevision: "changed_by",
        }.get(self.model)
        if (
            retention_field is not None
            and set(kwargs)
            in (
                {retention_field},
                {f"{retention_field}_id"},
            )
            and next(iter(kwargs.values())) is None
        ):
            return super().update(**kwargs)
        raise AppendOnlyViolation("append-only records cannot be updated")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyViolation("append-only records cannot be deleted")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        del args, kwargs
        raise AppendOnlyViolation("append-only records must be inserted through their writer")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        del args, kwargs
        raise AppendOnlyViolation("append-only records cannot be updated")


class AppendOnlyManager(models.Manager.from_queryset(AppendOnlyQuerySet)):  # type: ignore[misc]
    pass


class RevisionedModel(models.Model):
    revision = models.PositiveBigIntegerField(default=1)

    class Meta:
        abstract = True


class AuditEvent(models.Model):
    class Outcome(models.TextChoices):
        SUCCEEDED = "succeeded", "Succeeded"
        DENIED = "denied", "Denied"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor_ref = models.CharField(max_length=128, blank=True)
    action = models.CharField(max_length=128)
    target_type = models.CharField(max_length=128)
    target_id = models.UUIDField(null=True, blank=True)
    target_label = models.CharField(max_length=255, blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    request_id = models.CharField(max_length=128, blank=True)
    correlation_id = models.CharField(max_length=128, blank=True)
    job_id = models.CharField(max_length=128, blank=True)
    idempotency_key_hash = models.CharField(max_length=64, blank=True)
    changes = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    source_ip_class = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyManager()

    class Meta:
        base_manager_name = "objects"
        default_manager_name = "objects"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("action", "-created_at"), name="core_audit_action_time"),
            models.Index(
                fields=("target_type", "target_id", "-created_at"),
                name="core_audit_target_time",
            ),
            models.Index(fields=("request_id",), name="core_audit_request"),
            models.Index(fields=("correlation_id",), name="core_audit_correlation"),
            models.Index(fields=("job_id",), name="core_audit_job"),
            models.Index(fields=("actor_ref",), name="core_audit_actor_ref"),
        ]

    def __str__(self) -> str:
        return f"{self.action}:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyViolation("audit events cannot be updated")
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        del args, kwargs
        raise AppendOnlyViolation("audit events cannot be deleted")


class StaffSession(models.Model):
    """Provider-neutral Studio session reference; it never stores a browser cookie key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="studio_staff_sessions",
    )
    authenticated_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-authenticated_at", "-id")
        permissions = (
            ("access_studio", "Can access Studio"),
            ("browse_audit", "Can browse Studio audit events"),
            ("execute_high_risk_fixture", "Can execute the test-only high-risk fixture"),
        )
        constraints = [
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True) | Q(revoked_at__gte=F("authenticated_at")),
                name="core_staff_session_revoked_after_auth",
            )
        ]
        indexes = [
            models.Index(
                fields=("user", "revoked_at", "-authenticated_at"),
                name="core_staff_session_user",
            )
        ]

    def __str__(self) -> str:
        return f"staff-session:{self.id}"


class OperationalSetting(RevisionedModel):
    class ValueType(models.TextChoices):
        BOOLEAN = "boolean", "Boolean"
        INTEGER = "integer", "Integer"
        STRING = "string", "String"
        STRING_LIST = "string_list", "String list"
        JSON_OBJECT = "json_object", "JSON object"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=128, unique=True)
    value_type = models.CharField(max_length=16, choices=ValueType.choices)
    value = models.JSONField()
    source = models.CharField(max_length=64)
    definition_version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("key",)
        constraints = [
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="core_setting_revision_positive",
            ),
            models.CheckConstraint(
                condition=Q(definition_version__gte=1),
                name="core_setting_definition_positive",
            ),
        ]
        indexes = [models.Index(fields=("source", "key"), name="core_setting_source_key")]

    def __str__(self) -> str:
        return self.key


class OperationalSettingRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    setting = models.ForeignKey(
        OperationalSetting,
        on_delete=models.PROTECT,
        related_name="history",
    )
    key = models.CharField(max_length=128)
    value_type = models.CharField(max_length=16, choices=OperationalSetting.ValueType.choices)
    value = models.JSONField()
    source = models.CharField(max_length=64)
    definition_version = models.PositiveIntegerField()
    revision = models.PositiveBigIntegerField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operational_setting_revisions",
    )
    changed_by_ref = models.CharField(max_length=128, blank=True)
    audit_event = models.OneToOneField(
        AuditEvent,
        on_delete=models.PROTECT,
        related_name="setting_revision",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyManager()

    class Meta:
        base_manager_name = "objects"
        default_manager_name = "objects"
        ordering = ("setting_id", "revision")
        constraints = [
            models.UniqueConstraint(
                fields=("setting", "revision"),
                name="core_setting_history_revision_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="core_setting_history_revision_positive",
            ),
        ]
        indexes = [models.Index(fields=("key", "-revision"), name="core_setting_history_key")]

    def __str__(self) -> str:
        return f"{self.key}@{self.revision}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyViolation("setting history cannot be updated")
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        del args, kwargs
        raise AppendOnlyViolation("setting history cannot be deleted")


class IdempotencyRecord(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=128)
    key_hash = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_PROGRESS)
    owner_token = models.UUIDField(default=uuid.uuid4, editable=False)
    result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("scope", "key_hash"),
                name="core_idempotency_scope_key_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="in_progress",
                        result__isnull=True,
                        completed_at__isnull=True,
                    )
                    | Q(
                        status="completed",
                        result__isnull=False,
                        completed_at__isnull=False,
                    )
                ),
                name="core_idempotency_state_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "created_at"), name="core_idempotency_status"),
            models.Index(fields=("request_hash",), name="core_idempotency_request"),
        ]

    def __str__(self) -> str:
        return f"{self.scope}:{self.id}"


class Operation(RevisionedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    TERMINAL_STATUSES = frozenset({Status.SUCCEEDED, Status.FAILED, Status.CANCELLED})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    progress_current = models.PositiveBigIntegerField(default=0)
    progress_total = models.PositiveBigIntegerField(null=True, blank=True)
    cancellable = models.BooleanField(default=False)
    cancellation_requested_at = models.DateTimeField(null=True, blank=True)
    message = models.CharField(max_length=255, blank=True)
    result_summary = models.JSONField(default=dict)
    errors = models.JSONField(default=list)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operations",
    )
    actor_ref = models.CharField(max_length=128, blank=True)
    request_id = models.CharField(max_length=128, blank=True)
    correlation_id = models.CharField(max_length=128, blank=True)
    idempotency_key_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="core_operation_revision_positive",
            ),
            models.CheckConstraint(
                condition=Q(progress_total__isnull=True)
                | Q(progress_current__lte=F("progress_total")),
                name="core_operation_progress_bounded",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status__in=("succeeded", "failed", "cancelled"),
                        finished_at__isnull=False,
                    )
                    | Q(
                        status__in=("pending", "running"),
                        finished_at__isnull=True,
                    )
                ),
                name="core_operation_finish_state_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "-created_at"), name="core_operation_status_time"),
            models.Index(fields=("kind", "-created_at"), name="core_operation_kind_time"),
            models.Index(fields=("correlation_id",), name="core_operation_correlation"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"
