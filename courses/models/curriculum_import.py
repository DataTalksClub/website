from __future__ import annotations

import json
import re
import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from courses.curriculum_source_validators import validate_source_path

SHA1_PATTERN = r"^[0-9a-f]{40}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
SOURCE_STABLE_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
SOURCE_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
REPOSITORY_COMPONENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
REPOSITORY_BRANCH_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$"
IMPORT_RUN_STATES = (
    "received",
    "validating",
    "applying",
    "succeeded",
    "rejected",
    "failed",
)

sha1_validator = RegexValidator(
    SHA1_PATTERN,
    "Enter a full lowercase Git SHA.",
)
sha256_validator = RegexValidator(
    SHA256_PATTERN,
    "Enter a lowercase SHA-256 digest.",
)
source_stable_id_validator = RegexValidator(
    SOURCE_STABLE_ID_PATTERN,
    "Enter a canonical source stable ID.",
)
source_version_validator = RegexValidator(
    SOURCE_VERSION_PATTERN,
    "Enter a canonical source version.",
)
source_path_validator = RegexValidator(
    r"^(?!/)(?!.*\\).+$",
    "Enter a repository-relative POSIX path.",
)
repository_component_validator = RegexValidator(
    REPOSITORY_COMPONENT_PATTERN,
    "Enter a canonical repository owner or name.",
)
repository_branch_validator = RegexValidator(
    REPOSITORY_BRANCH_PATTERN,
    "Enter a canonical repository branch.",
)


def source_provenance_constraint(
    *,
    name: str,
    identity_fields: tuple[str, ...] = ("source_content_id",),
) -> models.CheckConstraint:
    fields = (
        *identity_fields,
        "source_path",
        "source_commit_sha",
        "source_checksum",
    )
    absent = Q(**{f"{field}__isnull": True for field in fields})
    present = Q(**{f"{field}__isnull": False for field in fields})
    present &= Q(source_commit_sha__regex=SHA1_PATTERN)
    present &= Q(source_checksum__regex=SHA256_PATTERN)
    return models.CheckConstraint(condition=absent | present, name=name)


class SourceProvenanceModel(models.Model):
    """Nullable provenance shared by source-managed curriculum records."""

    source_content_id = models.UUIDField(null=True, blank=True)
    source_path = models.CharField(  # noqa: DJ001 -- null identifies DB-managed rows.
        max_length=1024,
        null=True,
        blank=True,
        validators=[source_path_validator],
    )
    source_commit_sha = models.CharField(  # noqa: DJ001 -- null identifies DB-managed rows.
        max_length=40,
        null=True,
        blank=True,
        validators=[sha1_validator],
    )
    source_checksum = models.CharField(  # noqa: DJ001 -- null identifies DB-managed rows.
        max_length=64,
        null=True,
        blank=True,
        validators=[sha256_validator],
    )

    SOURCE_IDENTITY_FIELDS = ("source_content_id",)

    class Meta:
        abstract = True

    def clean(self) -> None:
        super().clean()
        field_names = (
            *self.SOURCE_IDENTITY_FIELDS,
            "source_path",
            "source_commit_sha",
            "source_checksum",
        )
        values = {field_name: getattr(self, field_name) for field_name in field_names}
        populated = {field_name for field_name, value in values.items() if value is not None}
        if populated and len(populated) != len(values):
            missing = sorted(set(values) - populated)
            raise ValidationError(
                {
                    field_name: "Source provenance must be supplied as a complete set."
                    for field_name in missing
                }
            )
        empty = {
            field_name
            for field_name, value in values.items()
            if value is not None and isinstance(value, str) and not value
        }
        if empty:
            raise ValidationError(
                {field_name: "Source provenance values cannot be empty." for field_name in empty}
            )
        if self.source_path:
            validate_source_path(self.source_path)


class CourseCurriculumImportRun(models.Model):
    """Bounded, redacted evidence for one source commit import attempt."""

    class State(models.TextChoices):
        RECEIVED = "received", "Received"
        VALIDATING = "validating", "Validating"
        APPLYING = "applying", "Applying"
        SUCCEEDED = "succeeded", "Succeeded"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"

    MAX_DIAGNOSTICS = 100
    MAX_DIAGNOSTICS_BYTES = 65_536
    MAX_COUNT_KEYS = 100
    MAX_COUNTS_BYTES = 16_384

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_uuid = models.UUIDField()
    source_stable_id = models.CharField(
        max_length=128,
        validators=[source_stable_id_validator],
    )
    repository_owner = models.CharField(
        max_length=128,
        validators=[repository_component_validator],
    )
    repository_name = models.CharField(
        max_length=128,
        validators=[repository_component_validator],
    )
    repository_branch = models.CharField(
        max_length=255,
        validators=[repository_branch_validator],
    )
    commit_sha = models.CharField(max_length=40, validators=[sha1_validator])
    schema_version = models.PositiveIntegerField()
    parser_version = models.CharField(
        max_length=128,
        validators=[source_version_validator],
    )
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.RECEIVED,
    )
    manifest_checksum = models.CharField(  # noqa: DJ001 -- absent until calculated.
        max_length=64,
        null=True,
        blank=True,
        validators=[sha256_validator],
    )
    diagnostics = models.JSONField(default=list, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("source_uuid", "commit_sha", "parser_version"),
                name="courses_curriculum_import_identity_uq",
            ),
            models.CheckConstraint(
                condition=Q(state__in=IMPORT_RUN_STATES),
                name="courses_curriculum_import_state_ck",
            ),
            models.CheckConstraint(
                condition=Q(commit_sha__regex=SHA1_PATTERN),
                name="courses_curriculum_import_commit_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(manifest_checksum__isnull=True) | Q(manifest_checksum__regex=SHA256_PATTERN)
                ),
                name="courses_curriculum_import_manifest_ck",
            ),
            models.CheckConstraint(
                condition=Q(schema_version__gte=1),
                name="courses_curriculum_import_schema_ck",
            ),
            models.CheckConstraint(
                condition=Q(source_stable_id__regex=SOURCE_STABLE_ID_PATTERN),
                name="courses_curriculum_import_source_ck",
            ),
            models.CheckConstraint(
                condition=Q(repository_owner__regex=REPOSITORY_COMPONENT_PATTERN),
                name="courses_curriculum_import_owner_ck",
            ),
            models.CheckConstraint(
                condition=Q(repository_name__regex=REPOSITORY_COMPONENT_PATTERN),
                name="courses_curriculum_import_repo_ck",
            ),
            models.CheckConstraint(
                condition=Q(repository_branch__regex=REPOSITORY_BRANCH_PATTERN),
                name="courses_curriculum_import_branch_ck",
            ),
            models.CheckConstraint(
                condition=Q(parser_version__regex=SOURCE_VERSION_PATTERN),
                name="courses_curriculum_import_parser_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=("source_uuid", "state", "-created_at"),
                name="courses_curr_import_state_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_stable_id}@{self.commit_sha[:12]} ({self.state})"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if not isinstance(self.diagnostics, list):
            errors["diagnostics"] = "Diagnostics must be a list."
        elif len(self.diagnostics) > self.MAX_DIAGNOSTICS:
            errors["diagnostics"] = "Diagnostics exceed the item limit."
        elif any(not isinstance(item, dict) for item in self.diagnostics):
            errors["diagnostics"] = "Each diagnostic must be an object."
        elif self._json_size(self.diagnostics) > self.MAX_DIAGNOSTICS_BYTES:
            errors["diagnostics"] = "Diagnostics exceed the encoded size limit."

        if not isinstance(self.counts, dict):
            errors["counts"] = "Counts must be an object."
        elif len(self.counts) > self.MAX_COUNT_KEYS:
            errors["counts"] = "Counts exceed the key limit."
        elif any(
            not isinstance(key, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for key, value in self.counts.items()
        ):
            errors["counts"] = "Counts require canonical keys and non-negative integers."
        elif self._json_size(self.counts) > self.MAX_COUNTS_BYTES:
            errors["counts"] = "Counts exceed the encoded size limit."

        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            errors["finished_at"] = "Finished time cannot be earlier than started time."
        if errors:
            raise ValidationError(errors)

    @staticmethod
    def _json_size(value: Any) -> int:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError):
            return 2**63 - 1
        return len(encoded)
