import secrets
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import F, Q

from accounts.identity_values import normalize_account_email


class CustomUser(AbstractUser):
    class IdentityState(models.TextChoices):
        LEGACY = 'legacy', 'Legacy-compatible'
        ACTIVE = 'active', 'Verified active identity'
        QUARANTINED = 'quarantined', 'Needs identity review'
        ABSORBED = 'absorbed', 'Absorbed into a survivor'

    ROLE_CHOICES = (
        ('student', 'Student'),
        ('instructor', 'Instructor'),
    )

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='student')
    certificate_name = models.CharField(
        verbose_name="Certificate name",
        max_length=255,
        blank=True,
        null=True,
        help_text="Your actual name that will appear on your certificates"
    )
    country = models.CharField(
        verbose_name="Country",
        max_length=100,
        blank=True,
    )
    region = models.CharField(
        verbose_name="Region",
        max_length=100,
        blank=True,
    )
    registration_role = models.CharField(
        verbose_name="Registration role",
        max_length=40,
        blank=True,
        help_text="Role last used on a course registration form",
    )
    github_url = models.URLField(
        verbose_name="GitHub URL",
        blank=True,
        null=True,
    )
    linkedin_url = models.URLField(
        verbose_name="LinkedIn URL",
        blank=True,
        null=True,
    )
    personal_website_url = models.URLField(
        verbose_name="Personal website URL",
        blank=True,
        null=True,
    )
    about_me = models.TextField(
        verbose_name="About me",
        blank=True,
        null=True,
    )
    dark_mode = models.BooleanField(
        verbose_name="Dark mode",
        default=False,
        help_text="Enable dark mode theme"
    )
    preferred_timezone = models.CharField(
        verbose_name="Preferred timezone",
        max_length=100,
        blank=True,
        default="",
        help_text=(
            "IANA timezone used for backend-rendered deadlines and "
            "notification emails."
        ),
    )
    # Every account starts subscribed, regardless of how it was created (a new
    # signup, the legacy zoomcamp importer, the CMP learner importer -- none of
    # those need to know this field exists). The only importer that writes this
    # field at all, ``scripts/prod/import_mailchimp_subscriptions.py``, only
    # ever confirms the default: a match in Mailchimp's subscribed export
    # writes ``True`` explicitly (Mailchimp's own record is the authority, not
    # the absence of contrary evidence). It does not read Mailchimp's separate
    # unsubscribed/cleaned exports, so nothing today ever writes ``False``.
    newsletter_subscribed = models.BooleanField(
        verbose_name="Newsletter subscribed",
        default=True,
        help_text="Whether this account receives newsletter email.",
    )
    home_dismissals = models.JSONField(
        verbose_name="Home dismissals",
        default=dict,
        blank=True,
        help_text=(
            "Allowlisted signed-in-home checklist/nudge keys this member has "
            "already skipped, completed, or dismissed."
        ),
    )

    # This is an expand-only identity key. The legacy ``email`` and
    # ``username`` columns remain available throughout the compatibility
    # window; a later contract migration may remove neither without the
    # production-like rehearsal owned by issue #60.
    normalized_email = models.EmailField(
        max_length=254,
        blank=True,
        null=True,
        editable=False,
        db_index=True,
    )
    identity_state = models.CharField(
        max_length=16,
        choices=IdentityState.choices,
        default=IdentityState.LEGACY,
        db_index=True,
    )

    # Set only by ``scripts/prod/import_cmp_learners.py``, from the CMP export's
    # ``accounts_customuser.id``. It is not an identity key -- nothing outside that
    # importer resolves an account by it -- it exists so the import can tell, on
    # resume, which source rows already landed (instead of re-scanning 20,009
    # accounts), and so the export's ``account_emailaddress.user_id`` can be
    # resolved to the account it belongs to.
    cmp_source_user_id = models.BigIntegerField(
        verbose_name="CMP source user ID",
        null=True,
        blank=True,
        editable=False,
        db_index=True,
    )

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=("normalized_email",),
                condition=(
                    Q(identity_state="active")
                    & Q(normalized_email__isnull=False)
                ),
                name="accounts_active_normalized_email_unique",
            ),
            models.UniqueConstraint(
                fields=("cmp_source_user_id",),
                condition=Q(cmp_source_user_id__isnull=False),
                name="accounts_cmp_source_user_id_unique",
            ),
        ]

    def save(self, *args, **kwargs):
        self.normalized_email = normalize_account_email(self.email)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "email" in update_fields:
            kwargs["update_fields"] = tuple(
                dict.fromkeys((*update_fields, "normalized_email"))
            )
        super().save(*args, **kwargs)

    def __str__(self):
        # safest is to display something stable
        if self.username:
            return self.username
        if self.email:
            return self.email
        pk_text = str(self.pk)
        return pk_text


class Token(models.Model):
    key = models.CharField(max_length=40, primary_key=True)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = secrets.token_urlsafe(16)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.key


class AccountIdentityAlias(models.Model):
    """Durable old-account ID to reviewed survivor mapping.

    ``source_user_id`` deliberately is not a foreign key. The absorbed source
    row stays in place during the rollback window, while the alias continues
    to resolve imports and audit evidence after any later privacy-approved
    contraction.
    """

    source_user_id = models.PositiveBigIntegerField(unique=True)
    survivor = models.ForeignKey(
        CustomUser,
        on_delete=models.PROTECT,
        related_name="identity_aliases",
    )
    source_snapshot_id = models.CharField(max_length=64)
    mapping_checksum = models.CharField(max_length=64)
    review_reference = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("source_user_id",)
        constraints = [
            models.CheckConstraint(
                condition=~Q(source_user_id=F("survivor_id")),
                name="accounts_identity_alias_distinct",
            )
        ]
        indexes = [
            models.Index(
                fields=("survivor", "source_user_id"),
                name="accounts_alias_survivor_source",
            )
        ]

    def __str__(self):
        return f"account-alias:{self.source_user_id}->{self.survivor_id}"


class AccountIdentityQuarantine(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fingerprint = models.CharField(max_length=64, unique=True)
    source_snapshot_id = models.CharField(max_length=64)
    source_user_ids = models.JSONField(default=list)
    reason_codes = models.JSONField(default=list)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
    )
    resolution_reference = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [
            models.Index(
                fields=("status", "created_at"),
                name="accounts_quarantine_status",
            )
        ]

    def __str__(self):
        return f"account-quarantine:{self.fingerprint}"


class AccountReconciliationRun(models.Model):
    class Mode(models.TextChoices):
        APPLY = "apply", "Apply"
        ROLLBACK_CHECK = "rollback_check", "Rollback check"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_snapshot_id = models.CharField(max_length=64)
    mapping_checksum = models.CharField(max_length=64)
    mode = models.CharField(max_length=16, choices=Mode.choices)
    source_account_count = models.PositiveBigIntegerField()
    survivor_account_count = models.PositiveBigIntegerField()
    alias_count = models.PositiveBigIntegerField(default=0)
    quarantine_count = models.PositiveBigIntegerField(default=0)
    relationship_counts = models.JSONField(default=dict)
    relationship_checksums = models.JSONField(default=dict)
    report_checksum = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("source_snapshot_id", "mapping_checksum", "mode"),
                name="accounts_reconciliation_run_unique",
            )
        ]

    def __str__(self):
        return f"account-reconciliation:{self.id}"


class CmpLearnerImportProgress(models.Model):
    """Per-table high-water mark for the resumable CMP learner-account import.

    ``scripts/prod/import_cmp_learners.py`` walks a source table in ascending
    source-id order, in fixed-size batches. Each batch's writes and the advance
    of ``last_source_id`` happen inside one database transaction, so a process
    killed mid-batch leaves that batch fully rolled back rather than partially
    written -- there is nothing for the stored watermark to disagree with. A
    re-run resumes with ``select id > last_source_id order by id``, so it never
    re-scans rows it already committed, and ``rows_written`` /
    ``last_source_id`` are enough to report "imported N of 20,009, last
    committed batch was X" without touching the source export again.

    ``table`` names either a literal source table (``accounts_customuser``,
    ``account_emailaddress``) or a derived phase of the same import that has
    no table of its own (``account_emailaddress_synthesized``, for the
    verified address synthesised onto an account the export carried no email
    row for). Either way it is one countable, resumable unit of this import.
    """

    table = models.CharField(max_length=64, unique=True)
    last_source_id = models.BigIntegerField(default=0)
    rows_written = models.PositiveBigIntegerField(default=0)
    rows_skipped = models.PositiveBigIntegerField(default=0)
    completed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("table",)

    def __str__(self):
        return f"cmp-learner-import-progress:{self.table}"
