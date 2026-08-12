"""Privacy-safe course registration-count provenance and active pointers.

These models deliberately contain aggregate facts only.  They must never grow a
registration identifier, attendee digest, source locator, or learner field.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.models import RevisionedModel


class CourseRegistrationCountSourceRun(RevisionedModel):
    class State(models.TextChoices):
        STAGED = "staged", "Staged"
        VALIDATED = "validated", "Validated"
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"
        ROLLED_BACK = "rolled_back", "Rolled back"
        QUARANTINED = "quarantined", "Quarantined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    adapter_version = models.CharField(max_length=32)
    schema_version = models.CharField(max_length=64)
    count_policy_version = models.CharField(max_length=32)
    whole_source_checksum = models.CharField(max_length=64)
    source_byte_size = models.PositiveBigIntegerField()
    schema_contract_checksum = models.CharField(max_length=64)
    aggregate_manifest_checksum = models.CharField(max_length=64)
    source_reference_digest = models.CharField(max_length=64, unique=True)
    captured_at = models.DateTimeField()
    source_frozen_at = models.DateTimeField()
    campaign_total = models.PositiveIntegerField()
    row_total = models.PositiveBigIntegerField()
    state = models.CharField(max_length=16, choices=State.choices, default=State.STAGED)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="course_registration_count_source_runs",
    )
    actor_ref = models.CharField(max_length=128, blank=True)
    reason_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _IMMUTABLE_FIELDS = (
        "adapter_version",
        "schema_version",
        "count_policy_version",
        "whole_source_checksum",
        "source_byte_size",
        "schema_contract_checksum",
        "aggregate_manifest_checksum",
        "source_reference_digest",
        "captured_at",
        "source_frozen_at",
        "campaign_total",
        "row_total",
    )

    class Meta:
        ordering = ("-created_at", "-id")
        permissions = (
            (
                "registration_count_baseline_manage",
                "Can manage course registration count baselines",
            ),
        )
        constraints = [
            models.UniqueConstraint(
                fields=("whole_source_checksum",),
                name="courses_count_run_checksum_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="courses_count_run_revision_positive",
            ),
            models.CheckConstraint(
                condition=Q(source_frozen_at__lte=F("captured_at")),
                name="courses_count_run_frozen_before_capture",
            ),
            models.CheckConstraint(
                condition=Q(
                    state__in=(
                        "staged",
                        "validated",
                        "active",
                        "cancelled",
                        "rolled_back",
                        "quarantined",
                    )
                ),
                name="courses_count_run_state_valid",
            ),
        ]
        indexes = [
            models.Index(fields=("state", "-created_at"), name="courses_count_run_state"),
        ]

    def __str__(self) -> str:
        return f"course-registration-count-run:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values(*self._IMMUTABLE_FIELDS).first()
            if original is not None and any(
                original[field] != getattr(self, field) for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("course count source provenance is immutable")
        super().save(*args, **kwargs)


class CourseRegistrationCountRevision(RevisionedModel):
    class State(models.TextChoices):
        STAGED = "staged", "Staged"
        VALIDATED = "validated", "Validated"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"
        ROLLED_BACK = "rolled_back", "Rolled back"
        QUARANTINED = "quarantined", "Quarantined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_run = models.ForeignKey(
        CourseRegistrationCountSourceRun,
        on_delete=models.PROTECT,
        related_name="count_revisions",
    )
    campaign = models.ForeignKey(
        "courses.RegistrationCampaign",
        on_delete=models.PROTECT,
        related_name="count_revisions",
    )
    cohort = models.ForeignKey(
        "courses.Course",
        on_delete=models.PROTECT,
        related_name="registration_count_revisions",
    )
    campaign_slug_snapshot = models.SlugField(max_length=255)
    cohort_slug_snapshot = models.SlugField(max_length=255)
    baseline_count = models.PositiveBigIntegerField()
    source_min_created_at = models.DateTimeField(null=True, blank=True)
    source_max_created_at = models.DateTimeField(null=True, blank=True)
    coverage_cutoff_at = models.DateTimeField()
    proposed_native_start_at = models.DateTimeField()
    aggregate_checksum = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=State.choices, default=State.STAGED)
    reason_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _IMMUTABLE_FIELDS = (
        "source_run_id",
        "campaign_id",
        "cohort_id",
        "campaign_slug_snapshot",
        "cohort_slug_snapshot",
        "baseline_count",
        "source_min_created_at",
        "source_max_created_at",
        "coverage_cutoff_at",
        "proposed_native_start_at",
        "aggregate_checksum",
    )

    class Meta:
        ordering = ("source_run_id", "campaign_slug_snapshot", "cohort_slug_snapshot")
        constraints = [
            models.UniqueConstraint(
                fields=("source_run", "campaign", "cohort"),
                name="courses_count_revision_target_unique",
            ),
            models.UniqueConstraint(
                fields=("campaign", "cohort"),
                condition=Q(state="active"),
                name="courses_count_revision_one_active",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="courses_count_revision_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(source_min_created_at__isnull=True, source_max_created_at__isnull=True)
                    | Q(
                        source_min_created_at__isnull=False,
                        source_max_created_at__isnull=False,
                        source_min_created_at__lte=F("source_max_created_at"),
                    )
                ),
                name="courses_count_revision_timestamp_pair",
            ),
            models.CheckConstraint(
                condition=Q(coverage_cutoff_at__lt=F("proposed_native_start_at")),
                name="courses_count_revision_nonoverlap",
            ),
            models.CheckConstraint(
                condition=Q(
                    state__in=(
                        "staged",
                        "validated",
                        "active",
                        "superseded",
                        "rolled_back",
                        "quarantined",
                    )
                ),
                name="courses_count_revision_state_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        baseline_count=0,
                        source_min_created_at__isnull=True,
                        source_max_created_at__isnull=True,
                    )
                    | Q(
                        baseline_count__gt=0,
                        source_min_created_at__isnull=False,
                        source_max_created_at__isnull=False,
                    )
                ),
                name="courses_count_revision_evidence",
            ),
        ]
        indexes = [
            models.Index(
                fields=("campaign", "cohort", "state"),
                name="courses_count_revision_target",
            ),
            models.Index(fields=("aggregate_checksum",), name="courses_count_rev_checksum"),
        ]

    def __str__(self) -> str:
        return f"course-registration-count-revision:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values(*self._IMMUTABLE_FIELDS).first()
            if original is not None and any(
                original[field] != getattr(self, field) for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("course count revision provenance and count are immutable")
        super().save(*args, **kwargs)


class CourseRegistrationCountSlot(RevisionedModel):
    class Mode(models.TextChoices):
        BASELINE_PLUS_NATIVE = "baseline_plus_native", "Baseline plus native rows"
        ROWS_ONLY = "rows_only", "Rows only"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        "courses.RegistrationCampaign",
        on_delete=models.PROTECT,
        related_name="registration_count_slots",
    )
    cohort = models.ForeignKey(
        "courses.Course",
        on_delete=models.PROTECT,
        related_name="registration_count_slots",
    )
    campaign_slug_snapshot = models.SlugField(max_length=255)
    cohort_slug_snapshot = models.SlugField(max_length=255)
    mode = models.CharField(max_length=24, choices=Mode.choices, blank=True)
    active_baseline_revision = models.OneToOneField(
        CourseRegistrationCountRevision,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="active_slot",
    )
    native_start_at = models.DateTimeField(null=True, blank=True)
    row_replacement_revision_id = models.UUIDField(null=True, blank=True)
    prior_mode = models.CharField(max_length=24, choices=Mode.choices, blank=True)
    prior_baseline_revision = models.ForeignKey(
        CourseRegistrationCountRevision,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="prior_slots",
    )
    prior_native_start_at = models.DateTimeField(null=True, blank=True)
    prior_row_replacement_revision_id = models.UUIDField(null=True, blank=True)
    public_total_revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("campaign_slug_snapshot", "cohort_slug_snapshot")
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "cohort"),
                name="courses_count_slot_target_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="courses_count_slot_revision_positive",
            ),
            models.CheckConstraint(
                condition=Q(public_total_revision__gte=1),
                name="courses_count_slot_public_revision_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        mode="",
                        active_baseline_revision__isnull=True,
                        native_start_at__isnull=True,
                        row_replacement_revision_id__isnull=True,
                    )
                    | Q(
                        mode="baseline_plus_native",
                        active_baseline_revision__isnull=False,
                        native_start_at__isnull=False,
                        row_replacement_revision_id__isnull=True,
                    )
                    | Q(
                        mode="rows_only",
                        active_baseline_revision__isnull=True,
                        native_start_at__isnull=False,
                        row_replacement_revision_id__isnull=False,
                    )
                ),
                name="courses_count_slot_mode_pointer",
            ),
            models.CheckConstraint(
                condition=Q(prior_mode__in=("", "baseline_plus_native", "rows_only")),
                name="courses_count_slot_prior_mode_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"course-registration-count-slot:{self.id}"
