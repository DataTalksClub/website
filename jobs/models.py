from __future__ import annotations

import uuid

from django.db import models
from django.db.models import F, Q


class DurableJob(models.Model):
    """Authoritative job intent; Django-Q messages are only disposable wakeups."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        RETRY_WAIT = "retry_wait", "Waiting to retry"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    TERMINAL_STATUSES = frozenset({Status.SUCCEEDED, Status.FAILED, Status.CANCELLED})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(
        "core.Operation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="durable_jobs",
    )
    handler = models.CharField(max_length=128)
    deduplication_key_hash = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    request_id = models.CharField(max_length=128, blank=True)
    correlation_id = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    available_at = models.DateTimeField()
    next_wakeup_at = models.DateTimeField()
    wakeup_count = models.PositiveIntegerField(default=0)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.CharField(max_length=128, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("available_at", "created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(max_attempts__gte=1),
                name="jobs_durable_max_attempts_positive",
            ),
            models.UniqueConstraint(
                fields=("handler", "deduplication_key_hash"),
                name="jobs_durable_handler_dedupe_unique",
            ),
            models.CheckConstraint(
                condition=Q(attempt_count__lte=F("max_attempts")),
                name="jobs_durable_attempts_bounded",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="running",
                        lease_token__isnull=False,
                        lease_expires_at__isnull=False,
                    )
                    & ~Q(claimed_by="")
                )
                | (
                    ~Q(status="running")
                    & Q(lease_token__isnull=True)
                    & Q(lease_expires_at__isnull=True)
                    & Q(claimed_by="")
                ),
                name="jobs_durable_lease_state_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status__in=("succeeded", "failed", "cancelled"), completed_at__isnull=False)
                    | (
                        ~Q(status__in=("succeeded", "failed", "cancelled"))
                        & Q(completed_at__isnull=True)
                    )
                ),
                name="jobs_durable_terminal_time_consistent",
            ),
        ]
        indexes = [
            models.Index(
                fields=("status", "available_at", "next_wakeup_at"),
                name="jobs_durable_due",
            ),
            models.Index(fields=("status", "lease_expires_at"), name="jobs_durable_lease"),
            models.Index(fields=("correlation_id",), name="jobs_durable_correlation"),
        ]

    def __str__(self) -> str:
        return f"{self.handler}:{self.id}"


class WorkerHeartbeat(models.Model):
    """Shared database heartbeat; unlike Django-Q Stat it works across containers."""

    worker_id = models.CharField(max_length=128, primary_key=True)
    lease_token = models.UUIDField(default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField()
    heartbeat_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ("worker_id",)
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("heartbeat_at")),
                name="jobs_heartbeat_expiry_after_beat",
            ),
            models.CheckConstraint(
                condition=Q(heartbeat_at__gte=F("started_at")),
                name="jobs_heartbeat_after_start",
            ),
        ]
        indexes = [models.Index(fields=("expires_at",), name="jobs_heartbeat_expiry")]

    def __str__(self) -> str:
        return self.worker_id


class SchedulerLease(models.Model):
    """The one fenced owner allowed to register and run code-owned schedules."""

    SINGLETON_KEY = "default"

    key = models.CharField(max_length=32, primary_key=True, default=SINGLETON_KEY, editable=False)
    owner_id = models.CharField(max_length=128, blank=True)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    acquired_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(key="default"),
                name="jobs_scheduler_lease_singleton",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        lease_token__isnull=False,
                        acquired_at__isnull=False,
                        heartbeat_at__isnull=False,
                        expires_at__isnull=False,
                    )
                    & ~Q(owner_id="")
                )
                | (
                    Q(lease_token__isnull=True)
                    & Q(acquired_at__isnull=True)
                    & Q(heartbeat_at__isnull=True)
                    & Q(expires_at__isnull=True)
                    & Q(owner_id="")
                ),
                name="jobs_scheduler_lease_state_consistent",
            ),
        ]

    def __str__(self) -> str:
        return self.owner_id or self.key
