"""Aggregate-only historical registration provenance and active pointers.

No model in this module stores a registration or attendee identity.  Provider event
identifiers are protected mapping data and must only be presented by the mapping
management capability.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.models import RevisionedModel


class HistoricalRegistrationSourceRun(RevisionedModel):
    class Provider(models.TextChoices):
        LUMA = "luma", "Luma"
        EVENTBRITE = "eventbrite", "Eventbrite"

    class State(models.TextChoices):
        STAGED = "staged", "Staged"
        VALIDATED = "validated", "Validated"
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"
        ROLLED_BACK = "rolled_back", "Rolled back"
        QUARANTINED = "quarantined", "Quarantined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=16, choices=Provider.choices)
    adapter_version = models.CharField(max_length=32)
    schema_version = models.CharField(max_length=64)
    whole_source_checksum = models.CharField(max_length=64)
    source_reference_digest = models.CharField(max_length=64)
    manifest_entry_total = models.PositiveIntegerField()
    manifest_event_total = models.PositiveIntegerField()
    parsed_row_total = models.PositiveBigIntegerField()
    eligible_row_total = models.PositiveBigIntegerField()
    excluded_row_total = models.PositiveBigIntegerField()
    quarantined_event_total = models.PositiveIntegerField(default=0)
    status_totals = models.JSONField(default=dict)
    state_totals = models.JSONField(default=dict)
    reason_codes = models.JSONField(default=list)
    mapping_set_revision = models.PositiveBigIntegerField()
    policy_version = models.CharField(max_length=32)
    state = models.CharField(max_length=16, choices=State.choices, default=State.STAGED)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="historical_registration_source_runs",
    )
    actor_ref = models.CharField(max_length=128, blank=True)
    reason_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _IMMUTABLE_FIELDS = (
        "provider",
        "adapter_version",
        "schema_version",
        "whole_source_checksum",
        "source_reference_digest",
        "manifest_entry_total",
        "manifest_event_total",
        "parsed_row_total",
        "eligible_row_total",
        "excluded_row_total",
        "quarantined_event_total",
        "status_totals",
        "state_totals",
        "reason_codes",
        "mapping_set_revision",
        "policy_version",
    )

    class Meta:
        ordering = ("-created_at", "-id")
        permissions = (
            (
                "historical_registration_import_manage",
                "Can manage historical registration aggregate imports",
            ),
            (
                "historical_registration_mapping_manage",
                "Can manage historical event mappings",
            ),
        )
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "provider",
                    "whole_source_checksum",
                    "schema_version",
                    "mapping_set_revision",
                    "policy_version",
                ),
                name="events_hist_run_replay_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="events_hist_run_revision_positive"
            ),
            models.CheckConstraint(
                condition=Q(eligible_row_total__lte=F("parsed_row_total")),
                name="events_hist_run_eligible_bounded",
            ),
            models.CheckConstraint(
                condition=Q(excluded_row_total__lte=F("parsed_row_total")),
                name="events_hist_run_excluded_bounded",
            ),
        ]
        indexes = [
            models.Index(fields=("provider", "state", "-created_at"), name="events_hist_run_state"),
            models.Index(fields=("whole_source_checksum",), name="events_hist_run_checksum"),
        ]

    def __str__(self) -> str:
        return f"historical-run:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values(*self._IMMUTABLE_FIELDS).first()
            if original is not None and any(
                original[field] != getattr(self, field) for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("source-run aggregate provenance is immutable")
        super().save(*args, **kwargs)


class HistoricalEventMapping(RevisionedModel):
    class State(models.TextChoices):
        REVIEW_REQUIRED = "review_required", "Review required"
        MAPPED = "mapped", "Mapped"
        EXCLUDED = "excluded", "Excluded"
        SOURCE_MISSING = "source_missing", "Source missing"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(
        max_length=16, choices=HistoricalRegistrationSourceRun.Provider.choices
    )
    external_event_identifier = models.CharField(max_length=512)
    canonical_repository = models.CharField(max_length=255, blank=True)
    canonical_revision = models.CharField(max_length=64, blank=True)
    canonical_source_key = models.CharField(max_length=512, blank=True)
    canonical_slug_snapshot = models.SlugField(max_length=255, blank=True)
    state = models.CharField(max_length=24, choices=State.choices)
    mapping_set_revision = models.PositiveBigIntegerField()
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_historical_event_mappings",
    )
    reviewer_ref = models.CharField(max_length=128, blank=True)
    reason_code = models.CharField(max_length=64, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("provider", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "external_event_identifier"),
                name="events_hist_mapping_provider_external_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="events_hist_mapping_revision_positive"
            ),
            models.CheckConstraint(
                condition=Q(mapping_set_revision__gte=1),
                name="events_hist_mapping_set_revision_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        state="mapped",
                        canonical_repository__gt="",
                        canonical_revision__gt="",
                        canonical_source_key__gt="",
                        canonical_slug_snapshot__gt="",
                        reviewer_ref__gt="",
                    )
                    | Q(
                        state="excluded",
                        reviewer_ref__gt="",
                        reason_code__gt="",
                    )
                    | Q(state="review_required")
                    | Q(state="source_missing")
                ),
                name="events_hist_mapping_state_evidence",
            ),
        ]
        indexes = [
            models.Index(fields=("provider", "state"), name="events_hist_mapping_state"),
            models.Index(
                fields=("canonical_repository", "canonical_revision", "canonical_source_key"),
                name="events_hist_mapping_source",
            ),
        ]

    def __str__(self) -> str:
        return f"historical-mapping:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("provider", "external_event_identifier")
                .first()
            )
            if original is not None and (
                original["provider"] != self.provider
                or original["external_event_identifier"] != self.external_event_identifier
            ):
                raise ValueError("provider mapping identity is immutable")
        super().save(*args, **kwargs)


class HistoricalRegistrationAggregateRevision(RevisionedModel):
    class State(models.TextChoices):
        STAGED = "staged", "Staged"
        VALIDATED = "validated", "Validated"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"
        QUARANTINED = "quarantined", "Quarantined"
        ROLLED_BACK = "rolled_back", "Rolled back"

    class CombinationPolicy(models.TextChoices):
        ADDITIVE_DISJOINT = "additive_disjoint", "Additive, disjoint"
        REPLACEMENT = "replacement", "Replacement"
        EXCLUDE = "exclude", "Exclude"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_run = models.ForeignKey(
        HistoricalRegistrationSourceRun,
        on_delete=models.PROTECT,
        related_name="aggregate_revisions",
    )
    mapping = models.ForeignKey(
        HistoricalEventMapping,
        on_delete=models.PROTECT,
        related_name="aggregate_revisions",
    )
    eligible_count = models.PositiveBigIntegerField()
    excluded_count = models.PositiveBigIntegerField(default=0)
    quarantined_count = models.PositiveBigIntegerField(default=0)
    coverage_boundary = models.CharField(max_length=128)
    status_policy_version = models.CharField(max_length=32)
    combination_policy = models.CharField(max_length=24, choices=CombinationPolicy.choices)
    aggregate_checksum = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=State.choices, default=State.STAGED)
    reason_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _IMMUTABLE_FIELDS = (
        "source_run_id",
        "mapping_id",
        "eligible_count",
        "excluded_count",
        "quarantined_count",
        "coverage_boundary",
        "status_policy_version",
        "combination_policy",
        "aggregate_checksum",
    )

    class Meta:
        ordering = ("source_run_id", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("source_run", "mapping", "aggregate_checksum"),
                name="events_hist_aggregate_revision_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="events_hist_aggregate_revision_positive"
            ),
        ]
        indexes = [
            models.Index(fields=("mapping", "state", "-created_at"), name="events_hist_agg_state"),
            models.Index(fields=("aggregate_checksum",), name="events_hist_agg_checksum"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values(*self._IMMUTABLE_FIELDS).first()
            if original is not None and any(
                original[field] != getattr(self, field) for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("aggregate revision provenance and counts are immutable")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"historical-aggregate:{self.id}"


class HistoricalRegistrationAggregateSlot(RevisionedModel):
    """The one active aggregate pointer for an event/provider/coverage slot."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canonical_repository = models.CharField(max_length=255)
    canonical_revision = models.CharField(max_length=64)
    canonical_source_key = models.CharField(max_length=512)
    canonical_slug_snapshot = models.SlugField(max_length=255)
    provider = models.CharField(
        max_length=16, choices=HistoricalRegistrationSourceRun.Provider.choices
    )
    coverage_boundary = models.CharField(max_length=128)
    active_revision = models.OneToOneField(
        HistoricalRegistrationAggregateRevision,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="active_slot",
    )
    replacement_revision_id = models.UUIDField(null=True, blank=True)
    replacement_eligible_count = models.PositiveBigIntegerField(null=True, blank=True)
    replacement_combination_policy = models.CharField(
        max_length=24,
        choices=HistoricalRegistrationAggregateRevision.CombinationPolicy.choices,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("canonical_source_key", "provider", "coverage_boundary")
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "canonical_repository",
                    "canonical_revision",
                    "canonical_source_key",
                    "provider",
                    "coverage_boundary",
                ),
                name="events_hist_active_slot_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="events_hist_slot_revision_positive"
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        active_revision__isnull=False,
                        replacement_revision_id__isnull=True,
                        replacement_eligible_count__isnull=True,
                        replacement_combination_policy="",
                    )
                    | Q(
                        active_revision__isnull=True,
                        replacement_revision_id__isnull=False,
                        replacement_eligible_count__isnull=False,
                        replacement_combination_policy__gt="",
                    )
                    | Q(
                        active_revision__isnull=True,
                        replacement_revision_id__isnull=True,
                        replacement_eligible_count__isnull=True,
                        replacement_combination_policy="",
                    )
                ),
                name="events_hist_slot_one_pointer",
            ),
        ]
        indexes = [
            models.Index(
                fields=("canonical_repository", "canonical_revision", "canonical_source_key"),
                name="events_hist_slot_source",
            )
        ]

    def __str__(self) -> str:
        return f"historical-slot:{self.id}"


class HistoricalRegistrationPointerDisplacement(models.Model):
    """Immutable snapshot of one pointer displaced by an aggregate replacement."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    replacing_revision = models.ForeignKey(
        HistoricalRegistrationAggregateRevision,
        on_delete=models.PROTECT,
        related_name="pointer_displacements",
    )
    slot = models.ForeignKey(
        HistoricalRegistrationAggregateSlot,
        on_delete=models.PROTECT,
        related_name="displacement_history",
    )
    displaced_revision = models.ForeignKey(
        HistoricalRegistrationAggregateRevision,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="displaced_by_replacements",
    )
    row_replacement_revision_id = models.UUIDField(null=True, blank=True)
    row_replacement_eligible_count = models.PositiveBigIntegerField(null=True, blank=True)
    row_replacement_combination_policy = models.CharField(
        max_length=24,
        choices=HistoricalRegistrationAggregateRevision.CombinationPolicy.choices,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    _IMMUTABLE_FIELDS = (
        "replacing_revision_id",
        "slot_id",
        "displaced_revision_id",
        "row_replacement_revision_id",
        "row_replacement_eligible_count",
        "row_replacement_combination_policy",
    )

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("replacing_revision", "slot"),
                name="events_hist_displacement_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        displaced_revision__isnull=False,
                        row_replacement_revision_id__isnull=True,
                        row_replacement_eligible_count__isnull=True,
                        row_replacement_combination_policy="",
                    )
                    | Q(
                        displaced_revision__isnull=True,
                        row_replacement_revision_id__isnull=False,
                        row_replacement_eligible_count__isnull=False,
                        row_replacement_combination_policy__gt="",
                    )
                ),
                name="events_hist_displacement_one_pointer",
            ),
        ]

    def __str__(self) -> str:
        return f"historical-displacement:{self.id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values(*self._IMMUTABLE_FIELDS).first()
            if original is not None and any(
                original[field] != getattr(self, field) for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("historical pointer displacement is immutable")
        super().save(*args, **kwargs)


class HistoricalRegistrationTotalState(RevisionedModel):
    """One public representation revision and completeness gate per canonical event."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canonical_repository = models.CharField(max_length=255)
    canonical_revision = models.CharField(max_length=64)
    canonical_source_key = models.CharField(max_length=512)
    canonical_slug_snapshot = models.SlugField(max_length=255)
    complete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("canonical_repository", "canonical_revision", "canonical_source_key"),
                name="events_hist_total_source_unique",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="events_hist_total_revision_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"historical-total:{self.id}"
