from __future__ import annotations

import logging
import uuid
from typing import Any

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.validators import RegexValidator
from django.db import models, router
from django.db.models import F, Q

logger = logging.getLogger(__name__)


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
        retention_fields = {
            AuditEvent: frozenset({"actor", "api_principal"}),
        }.get(self.model, frozenset())
        supplied_field = next(iter(kwargs), "").removesuffix("_id")
        if (
            len(kwargs) == 1
            and supplied_field in retention_fields
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

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Use one portable conditional update for revisioned service mutations.

        Callers increment ``revision`` and include it in ``update_fields``. The
        update succeeds only while the persisted row still has the immediately
        preceding revision, so SQLite and deployed PostgreSQL exercise the same
        optimistic compare-and-swap contract.
        """

        update_fields = kwargs.get("update_fields")
        if self._state.adding or update_fields is None or "revision" not in update_fields:
            super().save(*args, **kwargs)
            return
        if args or kwargs.get("force_insert"):
            raise ValueError(
                "revisioned conditional updates do not support positional/insert flags"
            )
        if self.revision < 2:
            raise ValueError("revisioned updates must increment revision exactly once")

        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        values: dict[str, Any] = {}
        for field_name in update_fields:
            field = self._meta.get_field(field_name)
            if not isinstance(field, models.Field) or field.primary_key:
                raise ValueError("revisioned update_fields must name concrete non-key fields")
            value = field.pre_save(self, add=False)
            values[field.attname] = value

        expected_revision = self.revision - 1
        queryset = (
            type(self)
            ._default_manager.using(using)
            .filter(
                pk=self.pk,
                revision=expected_revision,
            )
        )
        if queryset.update(**values) != 1:
            actual_revision = (
                type(self)
                ._default_manager.using(using)
                .filter(pk=self.pk)
                .values_list("revision", flat=True)
                .first()
            )
            if actual_revision is None:
                raise type(self).DoesNotExist(self.pk)
            raise RevisionConflict(expected=expected_revision, actual=actual_revision)
        self._state.db = using


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
    api_principal = models.ForeignKey(
        "management_auth.APIPrincipal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
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
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-authenticated_at", "-id")
        permissions = (
            ("access_studio", "Can access Studio"),
            ("browse_audit", "Can browse Studio audit events"),
            ("export_audit", "Can export Studio audit events"),
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
        permissions = (
            ("read_operational_settings", "Can read operational settings"),
            ("change_operational_settings", "Can change operational settings"),
        )
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


#: A key names a location inside one configured prefix and nothing else: no
#: scheme, no host, no leading slash, no traversal, no backslash.  See
#: ``courses.models.testimonial.ASSET_KEY_PATTERN`` for the full rationale;
#: the pattern is duplicated here rather than imported so a migration
#: serializes it *by value* and never imports another app's module.
SPONSOR_LOGO_ASSET_KEY_PATTERN = r"^(?!/)(?!.*\.\.)(?!.*\\)(?!\w+:)[\w.-]+(?:/[\w.-]+)*$"
SPONSOR_LOGO_ASSET_KEY_MESSAGE = (
    "Enter a key relative to the site-assets prefix, e.g. 'sponsors/example.png'."
)

#: Where a ``site-assets/`` key still resolves while the CDN move is in flight.
#: See ``courses.models.testimonial.INTERIM_SITE_ASSET_STATIC_PREFIX`` -- the
#: same interim layout, duplicated for the same self-containment reason.
SPONSOR_INTERIM_SITE_ASSET_STATIC_PREFIX = "core/"


class Sponsor(RevisionedModel):
    """One organization in the sponsor directory, public or Studio-managed.

    ``description`` and ``logo_asset_key`` back the public sponsor directory
    (the homepage teaser and ``/sponsors``); today they arrive only through
    :func:`core.sponsors.import_public_sponsor_directory` from the reviewed
    ``core/sponsor_directory.json`` and are not yet Studio-writable fields --
    a deliberate, narrower scope than the rest of this model, noted here so it
    is not mistaken for an oversight.

    Read the logo through :attr:`logo_url`, never by resolving
    ``logo_asset_key`` directly; that property is what keeps a bad row from
    taking the page down with it, the same guard
    ``courses.models.Testimonial.portrait_url`` uses for portraits.
    """

    class Lifecycle(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    url = models.CharField(max_length=500, blank=True)
    tagline = models.CharField(max_length=200, blank=True)
    description = models.TextField(
        blank=True,
        help_text=(
            "Longer copy shown on the public sponsor directory. Short-line "
            "placements such as events_hub use tagline instead and can leave "
            "this empty."
        ),
    )
    logo_asset_key = models.CharField(
        max_length=200,
        blank=True,
        validators=[RegexValidator(SPONSOR_LOGO_ASSET_KEY_PATTERN, SPONSOR_LOGO_ASSET_KEY_MESSAGE)],
        help_text=(
            "Logo key relative to the site-assets prefix, e.g. 'sponsors/example.png'. "
            "Empty renders no logo."
        ),
    )
    lifecycle = models.CharField(max_length=16, choices=Lifecycle.choices)
    source = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("key",)
        permissions = (
            ("read_sponsors", "Can read sponsors"),
            ("change_sponsors", "Can change sponsors"),
            ("export_sponsors", "Can export the sponsor directory"),
        )
        constraints = [
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="core_sponsor_revision_positive",
            ),
            models.CheckConstraint(
                condition=Q(lifecycle__in=("draft", "active", "archived")),
                name="core_sponsor_lifecycle_allowlist",
            ),
        ]
        indexes = [
            models.Index(fields=("lifecycle", "key"), name="core_sponsor_lifecycle_key"),
        ]

    def __str__(self) -> str:
        return self.key

    @property
    def logo_url(self) -> str:
        """The logo's public URL, or ``""`` when it cannot be resolved.

        **This never raises, and that is the whole point of it.**  See
        ``courses.models.Testimonial.portrait_url`` for the full rationale:
        under ``CompressedManifestStaticFilesStorage`` an unknown reference is
        a ``ValueError``, not a missing file, and this is read inside the
        homepage's and the sponsor directory's render loops, so one stale row
        must not abandon the whole page.  The broad ``except`` is deliberate
        for the same reason it is there -- no failure mode of asset
        resolution, present or future, may reach the caller.
        """

        key = self.logo_asset_key.strip()
        if not key:
            return ""
        try:
            return staticfiles_storage.url(f"{SPONSOR_INTERIM_SITE_ASSET_STATIC_PREFIX}{key}")
        except Exception:
            # The key is editorial, not secret, and naming it is what makes the
            # warning actionable; there is no request or person in it.
            logger.warning(
                "Sponsor logo could not be resolved; rendering without it.",
                extra={"logo_asset_key": key},
            )
            return ""


class SponsorPlacementAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sponsor = models.ForeignKey(
        Sponsor,
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    placement_key = models.CharField(max_length=64)
    position = models.PositiveIntegerField()
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("placement_key", "position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("sponsor", "placement_key"),
                name="core_sponsor_assignment_unique",
            ),
            models.UniqueConstraint(
                fields=("placement_key", "position"),
                condition=Q(enabled=True),
                name="core_sponsor_assignment_position_unique",
            ),
            models.CheckConstraint(
                condition=Q(placement_key__in=("events_hub", "public_directory")),
                name="core_sponsor_placement_allowlist",
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1),
                name="core_sponsor_assignment_position_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=("placement_key", "enabled", "position"),
                name="core_sponsor_assignment_public",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sponsor_id}:{self.placement_key}"


class SiteNavigationMenu(RevisionedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=32, unique=True)
    source = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("key",)
        permissions = (
            ("read_site_navigation", "Can read site navigation"),
            ("change_site_navigation", "Can change site navigation"),
        )
        constraints = [
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="core_navigation_revision_positive",
            ),
            models.CheckConstraint(
                condition=Q(key="primary"),
                name="core_navigation_menu_key_allowlist",
            ),
            models.CheckConstraint(
                condition=Q(source__in=("studio", "admin_api")),
                name="core_navigation_source_allowlist",
            ),
        ]
        indexes = [models.Index(fields=("key",), name="core_navigation_menu_key")]

    def __str__(self) -> str:
        return self.key


class SiteNavigationEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    menu = models.ForeignKey(
        SiteNavigationMenu,
        on_delete=models.PROTECT,
        related_name="entries",
    )
    key = models.CharField(max_length=32)
    label = models.CharField(max_length=80)
    target = models.CharField(max_length=64)
    position = models.PositiveIntegerField()
    visible = models.BooleanField(default=True)

    class Meta:
        ordering = ("position", "key")
        constraints = [
            models.UniqueConstraint(
                fields=("menu", "key"),
                name="core_navigation_entry_key_unique",
            ),
            models.UniqueConstraint(
                fields=("menu", "position"),
                name="core_navigation_entry_position_unique",
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1) & Q(position__lte=12),
                name="core_navigation_entry_position_bounded",
            ),
        ]
        indexes = [
            models.Index(
                fields=("menu", "position"),
                name="core_navigation_entry_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.menu_id}:{self.key}"


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
    api_principal = models.ForeignKey(
        "management_auth.APIPrincipal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operations",
    )
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
