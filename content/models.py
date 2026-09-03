from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from content.migration_validators import (
    validate_exact_public_path,
    validate_storage_key_shape,
)
from core.models import RevisionedModel

SHA1_PATTERN = r"^[0-9a-f]{40}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
PUBLIC_CONTRACT_DIGEST = "31f505350566bfcde0a30109dadcfb3565042fd395b4c1bd151966f94d361332"
LEGACY_PUBLIC_CONTRACT_DIGEST = "50f875806217865ef35b74f58ed885c4b5c832284391dbea7f84344d3416f66d"
SUPPORTED_PUBLIC_CONTRACT_DIGESTS = (
    PUBLIC_CONTRACT_DIGEST,
    LEGACY_PUBLIC_CONTRACT_DIGEST,
)
FROZEN_RELEASE_STATUSES = frozenset({"ready", "active", "superseded", "invalid", "failed"})

sha1_validator = RegexValidator(SHA1_PATTERN, "Enter a full lowercase Git SHA.")
sha256_validator = RegexValidator(SHA256_PATTERN, "Enter a lowercase SHA-256 digest.")


def expected_storage_prefix(source_stable_id: str, release_id: uuid.UUID) -> str:
    return f"content/{source_stable_id}/{release_id}/"


def active_content_path_digest(exact_public_path: str) -> str:
    """Return the fixed-width identity used by active namespace claims."""

    return hashlib.sha256(exact_public_path.encode("utf-8")).hexdigest()


class ContentSource(RevisionedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stable_id = models.SlugField(max_length=128)
    display_name = models.CharField(max_length=255)
    repository_owner = models.CharField(max_length=128)
    repository_name = models.CharField(max_length=128)
    branch = models.CharField(max_length=255)
    path_allowlist = models.JSONField(default=list, blank=True)
    adapter_type = models.CharField(max_length=64)
    mount_path = models.CharField(max_length=512, validators=[validate_exact_public_path])
    enabled = models.BooleanField(default=False)
    max_files = models.PositiveIntegerField(default=10_000)
    max_bytes = models.PositiveBigIntegerField(default=100_000_000)
    active_release = models.ForeignKey(
        "ContentRelease",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="active_for_sources",
    )
    last_webhook_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    sync_locked_at = models.DateTimeField(null=True, blank=True)
    pending_follow_up = models.BooleanField(default=False)
    freshness_target_minutes = models.PositiveIntegerField(default=60)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("stable_id",)
        constraints = [
            models.UniqueConstraint(fields=("stable_id",), name="content_source_stable_uq"),
            models.UniqueConstraint(
                Lower("repository_owner"),
                Lower("repository_name"),
                Lower("branch"),
                name="content_source_repo_branch_uq",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="content_source_revision_ck",
            ),
            models.CheckConstraint(
                condition=Q(max_files__gte=1),
                name="content_source_files_ck",
            ),
            models.CheckConstraint(
                condition=Q(max_bytes__gte=1),
                name="content_source_bytes_ck",
            ),
            models.CheckConstraint(
                condition=Q(freshness_target_minutes__gte=1),
                name="content_source_freshness_ck",
            ),
            models.CheckConstraint(
                condition=Q(stable_id__regex=r"^[a-z0-9][a-z0-9._-]{0,127}$"),
                name="content_source_stable_shape_ck",
            ),
            models.CheckConstraint(
                condition=Q(repository_owner__regex=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
                name="content_source_repo_owner_ck",
            ),
            models.CheckConstraint(
                condition=Q(repository_name__regex=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
                name="content_source_repo_name_ck",
            ),
            models.CheckConstraint(
                condition=Q(branch__regex=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$"),
                name="content_source_branch_ck",
            ),
        ]
        indexes = [
            models.Index(fields=("enabled", "stable_id"), name="content_source_enabled_id"),
            models.Index(
                fields=("repository_owner", "repository_name", "branch"),
                name="content_source_repository",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.display_name} ({self.stable_id})"

    def clean(self) -> None:
        super().clean()
        patterns = {
            "stable_id": r"[a-z0-9][a-z0-9._-]{0,127}",
            "repository_owner": r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            "repository_name": r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            "branch": r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}",
        }
        errors = {
            field_name: "Value must use canonical unpadded repository spelling."
            for field_name, pattern in patterns.items()
            if re.fullmatch(pattern, getattr(self, field_name)) is None
        }
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.using(self._state.db).only("stable_id").get(pk=self.pk)
            if previous.stable_id != self.stable_id:
                raise ValidationError("Content source stable_id is immutable.")
        super().save(*args, **kwargs)


class ContentRelease(RevisionedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        FETCHING = "fetching", "Fetching"
        VALIDATING = "validating", "Validating"
        READY = "ready", "Ready"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"
        INVALID = "invalid", "Invalid"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(ContentSource, on_delete=models.PROTECT, related_name="releases")
    sequence = models.PositiveBigIntegerField()
    based_on_release = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="based_candidates",
    )
    commit_sha = models.CharField(max_length=40, validators=[sha1_validator])
    parser_version = models.CharField(max_length=128)
    rendering_version = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    requested_at = models.DateTimeField()
    fetched_at = models.DateTimeField(null=True, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    request_provenance = models.JSONField(default=dict, blank=True)
    document_count = models.PositiveIntegerField(default=0)
    relation_count = models.PositiveIntegerField(default=0)
    asset_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list, blank=True)
    structured_errors = models.JSONField(default=list, blank=True)
    search_build_id = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    graph_build_id = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    asset_manifest_checksum = models.CharField(
        max_length=64,
        blank=True,
        validators=[sha256_validator],
    )
    public_contracts_sha256 = models.CharField(
        max_length=64,
        default=PUBLIC_CONTRACT_DIGEST,
        validators=[sha256_validator],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    IMMUTABLE_FIELDS = (
        "source_id",
        "sequence",
        "based_on_release_id",
        "commit_sha",
        "parser_version",
        "rendering_version",
        "requested_at",
        "request_provenance",
        "public_contracts_sha256",
    )
    PREPARATION_FIELDS = (
        "document_count",
        "relation_count",
        "asset_count",
        "warning_count",
        "warnings",
        "structured_errors",
        "search_build_id",
        "graph_build_id",
        "asset_manifest_checksum",
    )

    class Meta:
        ordering = ("source_id", "-sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("source", "sequence"),
                name="content_release_source_seq_uq",
            ),
            models.UniqueConstraint(
                fields=("source", "commit_sha", "parser_version", "rendering_version"),
                name="content_release_build_uq",
            ),
            models.UniqueConstraint(
                fields=("source",),
                condition=Q(status="active"),
                name="content_release_one_active_uq",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="content_release_revision_ck",
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=1),
                name="content_release_sequence_ck",
            ),
            models.CheckConstraint(
                condition=Q(commit_sha__regex=SHA1_PATTERN),
                name="content_release_commit_sha_ck",
            ),
            models.CheckConstraint(
                condition=Q(asset_manifest_checksum="")
                | Q(asset_manifest_checksum__regex=SHA256_PATTERN),
                name="content_release_manifest_sha_ck",
            ),
            models.CheckConstraint(
                condition=Q(public_contracts_sha256__in=SUPPORTED_PUBLIC_CONTRACT_DIGESTS),
                name="content_release_contract_sha_ck",
            ),
            models.CheckConstraint(
                condition=Q(document_count__gte=0)
                & Q(relation_count__gte=0)
                & Q(asset_count__gte=0)
                & Q(warning_count__gte=0),
                name="content_release_counts_ck",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=("ready", "active", "superseded"))
                | Q(asset_manifest_checksum__regex=SHA256_PATTERN),
                name="content_release_ready_sha_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="queued",
                        fetched_at__isnull=True,
                        validated_at__isnull=True,
                        activated_at__isnull=True,
                        superseded_at__isnull=True,
                        failed_at__isnull=True,
                    )
                    | Q(
                        status="fetching",
                        fetched_at__isnull=True,
                        validated_at__isnull=True,
                        activated_at__isnull=True,
                        superseded_at__isnull=True,
                        failed_at__isnull=True,
                    )
                    | Q(
                        status="validating",
                        fetched_at__isnull=False,
                        validated_at__isnull=True,
                        activated_at__isnull=True,
                        superseded_at__isnull=True,
                        failed_at__isnull=True,
                    )
                    | Q(
                        status="ready",
                        fetched_at__isnull=False,
                        validated_at__isnull=False,
                        activated_at__isnull=True,
                        superseded_at__isnull=True,
                        failed_at__isnull=True,
                    )
                    | Q(
                        status="active",
                        fetched_at__isnull=False,
                        validated_at__isnull=False,
                        activated_at__isnull=False,
                        superseded_at__isnull=True,
                        failed_at__isnull=True,
                    )
                    | Q(
                        status="superseded",
                        fetched_at__isnull=False,
                        validated_at__isnull=False,
                        activated_at__isnull=False,
                        superseded_at__isnull=False,
                        failed_at__isnull=True,
                    )
                    | Q(
                        status="invalid",
                        fetched_at__isnull=False,
                        validated_at__isnull=False,
                        activated_at__isnull=True,
                        superseded_at__isnull=True,
                        failed_at__isnull=True,
                    )
                    | Q(
                        status="failed",
                        validated_at__isnull=True,
                        activated_at__isnull=True,
                        superseded_at__isnull=True,
                        failed_at__isnull=False,
                    )
                ),
                name="content_release_timestamps_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("source", "status", "-sequence"),
                name="content_release_state_seq",
            ),
            models.Index(fields=("status", "-sequence"), name="content_release_status_seq"),
            models.Index(fields=("commit_sha",), name="content_release_commit"),
        ]

    def __str__(self) -> str:
        return f"{self.source.stable_id}@{self.sequence}:{self.commit_sha[:12]} ({self.status})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.using(self._state.db).get(pk=self.pk)
            for field_name in self.IMMUTABLE_FIELDS:
                if getattr(previous, field_name) != getattr(self, field_name):
                    raise ValidationError(f"Release field {field_name} is immutable.")
            if previous.status in FROZEN_RELEASE_STATUSES:
                for field_name in self.PREPARATION_FIELDS:
                    if getattr(previous, field_name) != getattr(self, field_name):
                        raise ValidationError("Frozen release evidence cannot be changed.")
        super().save(*args, **kwargs)


class ActiveContentPath(models.Model):
    """A transactionally swapped claim on one active public path.

    The fixed-width digest avoids depending on backend-specific index limits for the complete
    2,048-character path contract. The content service derives and replaces these rows in the
    same transaction as the release and source-pointer swap.
    """

    path_digest = models.CharField(max_length=64, primary_key=True, validators=[sha256_validator])
    exact_public_path = models.CharField(
        max_length=2048,
        validators=[validate_exact_public_path],
    )
    source = models.ForeignKey(
        ContentSource,
        on_delete=models.PROTECT,
        related_name="active_path_claims",
    )
    release = models.ForeignKey(
        ContentRelease,
        on_delete=models.PROTECT,
        related_name="active_path_claims",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("exact_public_path",)
        constraints = [
            models.CheckConstraint(
                condition=Q(path_digest__regex=SHA256_PATTERN),
                name="content_active_path_digest_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(exact_public_path__startswith="/")
                    & ~Q(exact_public_path__startswith="//")
                    & ~Q(exact_public_path__contains="?")
                    & ~Q(exact_public_path__contains="#")
                ),
                name="content_active_path_shape_ck",
            ),
        ]
        indexes = [
            models.Index(fields=("source", "release"), name="content_active_path_owner"),
            models.Index(fields=("release",), name="content_active_path_release"),
        ]

    def __str__(self) -> str:
        return self.exact_public_path


class FrozenReleaseChild(models.Model):
    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        using = kwargs.get("using") or self._state.db or "default"
        self._guard_frozen_release(using=using)
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        using = kwargs.get("using") or self._state.db or "default"
        self._guard_frozen_release(using=using)
        return super().delete(*args, **kwargs)

    def release_id_for_guard(self, *, using: str) -> uuid.UUID:
        del using
        raise NotImplementedError

    def _guard_frozen_release(self, *, using: str) -> None:
        release_id = self.release_id_for_guard(using=using)
        if (
            ContentRelease.objects.using(using)
            .filter(
                pk=release_id,
                status__in=FROZEN_RELEASE_STATUSES,
            )
            .exists()
        ):
            raise ValidationError("Frozen release children cannot be changed.")
        if not self._state.adding:
            previous = type(self)._default_manager.using(using).get(pk=self.pk)
            previous_release_id = previous.release_id_for_guard(using=using)
            if (
                ContentRelease.objects.using(using)
                .filter(
                    pk=previous_release_id,
                    status__in=FROZEN_RELEASE_STATUSES,
                )
                .exists()
            ):
                raise ValidationError("Frozen release children cannot be changed.")


class ContentDocument(FrozenReleaseChild):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    release = models.ForeignKey(ContentRelease, on_delete=models.CASCADE, related_name="documents")
    content_kind = models.CharField(max_length=64)
    stable_key = models.CharField(max_length=255)
    source_path = models.CharField(max_length=1024)
    checksum = models.CharField(max_length=64, validators=[sha256_validator])
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True)
    exact_public_path = models.CharField(  # noqa: DJ001
        max_length=2048,
        null=True,
        blank=True,
        validators=[validate_exact_public_path],
    )
    slug = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=512)
    summary = models.TextField(blank=True)
    canonical_url = models.URLField(max_length=2048, blank=True)
    seo_title = models.CharField(max_length=512, blank=True)
    seo_description = models.TextField(blank=True)
    seo_image_url = models.URLField(max_length=2048, blank=True)
    raw_frontmatter = models.JSONField(default=dict, blank=True)
    raw_body = models.TextField(blank=True)
    raw_structured_data = models.TextField(blank=True)
    rendered_html = models.TextField(blank=True)
    adapter_metadata = models.JSONField(default=dict, blank=True)
    is_published = models.BooleanField(default=False)
    noindex = models.BooleanField(default=False)
    edit_url = models.URLField(max_length=2048, blank=True)
    contract_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    contract_source_id = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    contract_source_revision = models.CharField(  # noqa: DJ001
        max_length=40,
        null=True,
        blank=True,
        validators=[sha1_validator],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("release_id", "content_kind", "stable_key")
        constraints = [
            models.UniqueConstraint(
                fields=("release", "content_kind", "stable_key"),
                name="content_doc_stable_uq",
            ),
            models.UniqueConstraint(
                fields=("release", "exact_public_path"),
                condition=Q(exact_public_path__isnull=False),
                name="content_doc_release_path_uq",
            ),
            models.UniqueConstraint(
                fields=("release", "contract_id"),
                condition=Q(contract_id__isnull=False),
                name="content_doc_contract_uq",
            ),
            models.CheckConstraint(
                condition=Q(checksum__regex=SHA256_PATTERN),
                name="content_doc_checksum_ck",
            ),
            models.CheckConstraint(
                condition=Q(exact_public_path__isnull=True)
                | (
                    Q(exact_public_path__startswith="/")
                    & ~Q(exact_public_path__startswith="//")
                    & ~Q(exact_public_path__contains="?")
                    & ~Q(exact_public_path__contains="#")
                ),
                name="content_doc_exact_path_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        contract_id__isnull=True,
                        contract_source_id__isnull=True,
                        contract_source_revision__isnull=True,
                    )
                    | Q(
                        contract_id__isnull=False,
                        contract_source_id__isnull=False,
                        contract_source_revision__regex=SHA1_PATTERN,
                    )
                ),
                name="content_doc_provenance_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("exact_public_path", "release"),
                name="content_document_exact_path",
            ),
            models.Index(fields=("source_path", "release"), name="content_document_source_path"),
            models.Index(fields=("content_kind", "stable_key"), name="content_document_kind_key"),
        ]

    def __str__(self) -> str:
        return f"{self.content_kind}:{self.stable_key}@{self.release_id}"

    def release_id_for_guard(self, *, using: str) -> uuid.UUID:
        del using
        return self.release_id


class ContentRelation(FrozenReleaseChild):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_document = models.ForeignKey(
        ContentDocument,
        on_delete=models.CASCADE,
        related_name="relations",
    )
    relation_type = models.CharField(max_length=64)
    target_kind = models.CharField(max_length=64)
    target_key = models.CharField(max_length=255)
    resolved_target_document = models.ForeignKey(
        ContentDocument,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="incoming_relations",
    )
    resolved_public_path = models.CharField(  # noqa: DJ001
        max_length=2048,
        null=True,
        blank=True,
        validators=[validate_exact_public_path],
    )
    label = models.CharField(max_length=512, blank=True)
    order = models.PositiveIntegerField(default=0)
    timestamp_seconds = models.PositiveIntegerField(null=True, blank=True)
    is_required = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("source_document_id", "relation_type", "order")
        constraints = [
            models.UniqueConstraint(
                fields=("source_document", "relation_type", "order"),
                name="content_relation_order_uq",
            ),
            models.CheckConstraint(
                condition=~(
                    Q(resolved_target_document__isnull=False)
                    & Q(resolved_public_path__isnull=False)
                ),
                name="content_relation_resolution_ck",
            ),
            models.CheckConstraint(
                condition=Q(is_required=False)
                | Q(resolved_target_document__isnull=False)
                | Q(resolved_public_path__isnull=False),
                name="content_relation_required_ck",
            ),
            models.CheckConstraint(
                condition=Q(order__gte=0),
                name="content_relation_order_ck",
            ),
        ]
        indexes = [
            models.Index(fields=("target_kind", "target_key"), name="content_relation_target"),
            models.Index(
                fields=("resolved_public_path",),
                name="content_relation_public_path",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_document_id}:{self.relation_type}[{self.order}]"

    def release_id_for_guard(self, *, using: str) -> uuid.UUID:
        if "source_document" in self._state.fields_cache:
            return self.source_document.release_id
        return (
            ContentDocument.objects.using(using)
            .only("release_id")
            .get(pk=self.source_document_id)
            .release_id
        )


class ContentAsset(FrozenReleaseChild):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    release = models.ForeignKey(ContentRelease, on_delete=models.CASCADE, related_name="assets")
    source_path = models.CharField(max_length=1024)
    stable_public_path = models.CharField(
        max_length=2048,
        validators=[validate_exact_public_path],
    )
    storage_key = models.CharField(
        max_length=2048,
        validators=[validate_storage_key_shape],
    )
    content_type = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    checksum = models.CharField(max_length=64, validators=[sha256_validator])
    contract_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    contract_source_id = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    contract_source_revision = models.CharField(  # noqa: DJ001
        max_length=40,
        null=True,
        blank=True,
        validators=[sha1_validator],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("release_id", "stable_public_path")
        constraints = [
            models.UniqueConstraint(fields=("storage_key",), name="content_asset_storage_key_uq"),
            models.UniqueConstraint(
                fields=("release", "source_path"),
                name="content_asset_source_path_uq",
            ),
            models.UniqueConstraint(
                fields=("release", "stable_public_path"),
                name="content_asset_public_path_uq",
            ),
            models.UniqueConstraint(
                fields=("release", "contract_id"),
                condition=Q(contract_id__isnull=False),
                name="content_asset_contract_uq",
            ),
            models.CheckConstraint(
                condition=Q(checksum__regex=SHA256_PATTERN),
                name="content_asset_checksum_ck",
            ),
            models.CheckConstraint(
                condition=Q(size__gte=0),
                name="content_asset_size_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(stable_public_path__startswith="/")
                    & ~Q(stable_public_path__startswith="//")
                    & ~Q(stable_public_path__contains="?")
                    & ~Q(stable_public_path__contains="#")
                ),
                name="content_asset_public_path_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        contract_id__isnull=True,
                        contract_source_id__isnull=True,
                        contract_source_revision__isnull=True,
                    )
                    | Q(
                        contract_id__isnull=False,
                        contract_source_id__isnull=False,
                        contract_source_revision__regex=SHA1_PATTERN,
                    )
                ),
                name="content_asset_provenance_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("stable_public_path", "release"),
                name="content_asset_public_path",
            ),
            models.Index(fields=("source_path", "release"), name="content_asset_source_path"),
        ]

    def __str__(self) -> str:
        return f"{self.stable_public_path}@{self.release_id}"

    def release_id_for_guard(self, *, using: str) -> uuid.UUID:
        del using
        return self.release_id
