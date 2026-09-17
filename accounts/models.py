import secrets
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.identity_values import normalize_account_email


class CustomUser(AbstractUser):
    class IdentityState(models.TextChoices):
        LEGACY = "legacy", "Legacy-compatible"
        ACTIVE = "active", "Verified active identity"
        QUARANTINED = "quarantined", "Needs identity review"
        ABSORBED = "absorbed", "Absorbed into a survivor"

    ROLE_CHOICES = (
        ("student", "Student"),
        ("instructor", "Instructor"),
    )

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="student")
    certificate_name = models.CharField(
        verbose_name="Certificate name",
        max_length=255,
        blank=True,
        null=True,
        help_text="Your actual name that will appear on your certificates",
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
        verbose_name="Dark mode", default=False, help_text="Enable dark mode theme"
    )
    preferred_timezone = models.CharField(
        verbose_name="Preferred timezone",
        max_length=100,
        blank=True,
        default="",
        help_text=("IANA timezone used for backend-rendered deadlines and notification emails."),
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
    # When the member last changed ``newsletter_subscribed`` through an
    # in-app save (see ``CustomUser.save``), or null if no local decision
    # exists.  The Mailchimp subscription import refuses to touch any
    # account carrying a non-null value here: a recorded local decision
    # always wins over an audience-export snapshot, however fresh, so
    # replaying an older subscribed export can never resurrect an opt-out
    # (audit BE-15).  The import itself writes through ``bulk_update``
    # precisely so that its own migration writes are not mistaken for
    # local decisions.
    newsletter_preference_changed_at = models.DateTimeField(
        verbose_name="Newsletter preference locally changed at",
        null=True,
        blank=True,
        help_text=(
            "When this account's newsletter preference was last changed "
            "locally; null means the current value was never locally "
            "decided. Set automatically on save. Audience imports never "
            "overwrite a recorded local decision."
        ),
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

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=("normalized_email",),
                condition=(Q(identity_state="active") & Q(normalized_email__isnull=False)),
                name="accounts_active_normalized_email_unique",
            ),
        ]

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        if "newsletter_subscribed" in field_names:
            instance._loaded_newsletter_subscribed = values[
                field_names.index("newsletter_subscribed")
            ]
        return instance

    def refresh_from_db(self, using=None, fields=None):
        super().refresh_from_db(using=using, fields=fields)
        # Re-baseline the loaded value so a save after a refresh still
        # detects a deliberate newsletter change (a plain reload is not one).
        if fields is None or "newsletter_subscribed" in fields:
            self._loaded_newsletter_subscribed = self.newsletter_subscribed

    def save(self, *args, **kwargs):
        self.normalized_email = normalize_account_email(self.email)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "email" in update_fields:
            kwargs["update_fields"] = tuple(dict.fromkeys((*update_fields, "normalized_email")))
        # A locally made newsletter decision -- any save that changes
        # ``newsletter_subscribed`` away from its loaded value -- is stamped
        # with the decision time, and the Mailchimp import treats a non-null
        # stamp as a local decision it must never override (audit BE-15).
        # Creation is not a local decision and is never stamped, and the
        # import deliberately writes through ``bulk_update``, which skips
        # this override entirely.
        loaded = getattr(self, "_loaded_newsletter_subscribed", None)
        if self.pk is not None and loaded is not None and loaded != self.newsletter_subscribed:
            self.newsletter_preference_changed_at = timezone.now()
            if update_fields is not None:
                kwargs["update_fields"] = tuple(
                    dict.fromkeys((*kwargs["update_fields"], "newsletter_preference_changed_at"))
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


class CmpLearnerImportBinding(models.Model):
    """Script-owned binding of the resumable CMP learner-account import to its
    exact inputs.

    One singleton row (``kind`` is unique): the first run records the export's
    SHA-256 digest plus the schema and importer versions it is running; every
    later run -- including a kill-and-resume -- must present the same triple or
    the service refuses before any write. Claims and watermarks from one export
    can therefore never be applied against a different snapshot, and a rebuilt
    target database starts with no row at all, so it can only ever begin a
    fresh import rather than resume someone else's.

    This is the database-side half of the REL-03/REL-04 fix in
    ``_docs/audits/2026-09-07-backend-security-audit.md``: the claims
    themselves live in :class:`CmpLearnerClaim`, in the same transaction as
    the rows they map, so no separate file can fall behind a commit.
    """

    kind = models.CharField(max_length=32, unique=True)
    schema_version = models.IntegerField()
    importer_version = models.CharField(max_length=64)
    source_sha256 = models.CharField(max_length=64)
    target_uuid = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"cmp-import-binding:{self.kind}"


class CmpLearnerClaim(models.Model):
    """One "CMP source account id -> CustomUser pk" mapping, per imported row.

    This is the durable claims store the learner-account importer reads and
    writes; it replaced the earlier JSON file so that a claim commits inside
    the same transaction as the batch that created the account (audit
    REL-04). A claim that survives is always backed by a committed account
    row; a rolled-back batch leaves no claim behind.
    """

    source_id = models.BigIntegerField(unique=True)
    user_id = models.BigIntegerField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("source_id",)

    def __str__(self):
        return f"cmp-learner-claim:{self.source_id}"


class MailchimpSubscriptionImportRun(models.Model):
    """Provenance for one invocation of the Mailchimp subscription import.

    The import is a snapshot migration, not a live sync: what a run knew is
    exactly the subscribed CSV's digest and the operator-declared snapshot
    date recorded here.  Anything that changed locally after that snapshot
    is protected by ``CustomUser.newsletter_preference_changed_at``; this
    row is the evidence of which snapshot was applied (or rehearsed), when,
    and with what effect (audit BE-15).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_name = models.CharField(
        max_length=255,
        help_text="File name of the subscribed CSV; the containing path is never stored.",
    )
    source_sha256 = models.CharField(max_length=64)
    source_bytes = models.PositiveBigIntegerField()
    as_of = models.DateField(help_text="The operator-declared date the audience export was cut.")
    applied = models.BooleanField(
        default=True,
        help_text="False for a dry run: counts were computed, nothing was written.",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    report = models.JSONField(default=dict)

    class Meta:
        ordering = ("-recorded_at", "-id")

    def __str__(self):
        return f"mailchimp-import:{self.id}"
