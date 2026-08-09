from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth.models import Permission
from django.db import models
from django.db.models import F, Q

from core.models import RevisionedModel


class ImmutableManagementIdentity(ValueError):
    pass


class APIPrincipal(RevisionedModel):
    class Kind(models.TextChoices):
        HUMAN = "human", "Human"
        SERVICE = "service", "Service"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    name = models.CharField(max_length=120)
    identity_snapshot = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="management_api_principal",
    )
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name="management_api_principals",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_management_api_principals",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")
        permissions = (("read_admin_health", "Can read management API health"),)
        constraints = [
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="mgmt_principal_revision_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(kind="human", user__isnull=False) | Q(kind="service", user__isnull=True)
                ),
                name="mgmt_principal_kind_user_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=("kind", "is_active", "id"), name="mgmt_principal_active"),
        ]

    def __str__(self) -> str:
        return f"api-principal:{self.id}"

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            original = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("kind", "user_id", "identity_snapshot")
                .first()
            )
            if original is not None and (
                original["kind"] != self.kind
                or original["user_id"] != self.user_id
                or original["identity_snapshot"] != self.identity_snapshot
            ):
                raise ImmutableManagementIdentity("principal identity fields are immutable")
        super().save(*args, **kwargs)


class APICredential(RevisionedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.ForeignKey(
        APIPrincipal,
        on_delete=models.CASCADE,
        related_name="credentials",
    )
    name = models.CharField(max_length=120)
    prefix = models.CharField(max_length=16)
    secret_digest = models.CharField(max_length=256)
    digest_algorithm = models.CharField(max_length=32)
    digest_version = models.PositiveSmallIntegerField()
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    overlap_expires_at = models.DateTimeField(null=True, blank=True)
    predecessor = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="successor",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_management_api_credentials",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("prefix",),
                name="mgmt_credential_prefix_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="mgmt_credential_revision_positive",
            ),
            models.CheckConstraint(
                condition=Q(digest_version__gte=1),
                name="mgmt_credential_digest_version_positive",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("created_at")),
                name="mgmt_credential_expiry_after_create",
            ),
            models.CheckConstraint(
                condition=Q(overlap_expires_at__isnull=True)
                | Q(rotated_at__isnull=False, overlap_expires_at__gte=F("rotated_at")),
                name="mgmt_credential_overlap_consistent",
            ),
        ]
        indexes = [
            models.Index(
                fields=("principal", "revoked_at", "expires_at"),
                name="mgmt_credential_active",
            ),
        ]

    def __str__(self) -> str:
        return f"api-credential:{self.id}"

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            original = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(
                    "principal_id",
                    "prefix",
                    "secret_digest",
                    "digest_algorithm",
                    "digest_version",
                    "scopes",
                    "predecessor_id",
                )
                .first()
            )
            if original is not None and (
                original["principal_id"] != self.principal_id
                or original["prefix"] != self.prefix
                or original["secret_digest"] != self.secret_digest
                or original["digest_algorithm"] != self.digest_algorithm
                or original["digest_version"] != self.digest_version
                or original["scopes"] != self.scopes
                or original["predecessor_id"] != self.predecessor_id
            ):
                raise ImmutableManagementIdentity("credential authority fields are immutable")
        super().save(*args, **kwargs)


class ManagementIdempotencyRecord(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.ForeignKey(
        APIPrincipal,
        on_delete=models.CASCADE,
        related_name="idempotency_records",
    )
    operation = models.CharField(max_length=128)
    key_hash = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices)
    safe_result = models.JSONField(null=True, blank=True)
    owner_token = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("principal", "operation", "key_hash"),
                name="mgmt_idempotency_principal_operation_key",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="in_progress",
                        safe_result__isnull=True,
                        completed_at__isnull=True,
                    )
                    | Q(
                        status="completed",
                        safe_result__isnull=False,
                        completed_at__isnull=False,
                    )
                ),
                name="mgmt_idempotency_state_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=("expires_at",), name="mgmt_idempotency_expiry"),
        ]

    def __str__(self) -> str:
        return f"management-idempotency:{self.id}"


class APIRateAdmission(models.Model):
    class CostClass(models.TextChoices):
        READ = "read", "Read"
        WRITE = "write", "Write"
        ADAPTIVE = "adaptive", "Adaptive digest"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal = models.ForeignKey(
        APIPrincipal,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rate_admissions",
    )
    subject_hash = models.CharField(max_length=64)
    cost_class = models.CharField(max_length=16, choices=CostClass.choices)
    cost = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(
                fields=("subject_hash", "cost_class", "created_at"),
                name="mgmt_rate_window",
            ),
            models.Index(fields=("created_at",), name="mgmt_rate_cleanup"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(cost__gte=1) & Q(cost__lte=10),
                name="mgmt_rate_cost_bounded",
            )
        ]

    def __str__(self) -> str:
        return f"api-rate-admission:{self.id}"


class APIRateSubject(models.Model):
    """Portable transaction-serialization row for one rate-limit subject."""

    subject_hash = models.CharField(max_length=64)
    cost_class = models.CharField(max_length=16, choices=APIRateAdmission.CostClass.choices)
    revision = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("subject_hash", "cost_class"),
                name="mgmt_rate_subject_class_unique",
            )
        ]

    def __str__(self) -> str:
        return f"api-rate-subject:{self.subject_hash}:{self.cost_class}"
