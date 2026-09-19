"""Site-owned extension models for the accounts domain (plan issue D3.1).

``IdentityState`` carries the identity reconciliation columns that used to
live on the auth user model, plus the four identity evidence models that used
to be registered under ``accounts``. The evidence models' physical tables
already exist under their old names, so their ``db_table`` is pinned and
their creation is recorded state-only: tables and rows are never rebuilt or
dropped.

This app is what survives the later shared-`User` adoption: every other
``accounts`` module can be deleted around it.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from accounts.identity_values import normalize_account_email


class IdentityState(models.Model):
    """Per-account identity reconciliation state.

    The columns moved off the auth user model verbatim (plan issue D3.1). The
    save-path invariant ``normalized_email == normalize_account_email(
    user.email)`` is kept by ``accounts_ext.signals`` on every path that
    persists an email write, exactly as the old ``CustomUser.save`` override
    did; ``normalized_email`` stays ``None`` for a row whose user has never
    been saved through the ORM (bulk-created accounts), which is how an empty
    column behaved.
    """

    class States(models.TextChoices):
        LEGACY = "legacy", "Legacy-compatible"
        ACTIVE = "active", "Verified active identity"
        QUARANTINED = "quarantined", "Needs identity review"
        ABSORBED = "absorbed", "Absorbed into a survivor"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="identity",
    )
    # noqa DJ001: the column moved off the user model verbatim and is
    # nullable there; a data-preserving move must not change its null rule.
    normalized_email = models.EmailField(  # noqa: DJ001
        max_length=254,
        blank=True,
        null=True,
        editable=False,
        db_index=True,
    )
    identity_state = models.CharField(
        max_length=16,
        choices=States.choices,
        default=States.LEGACY,
        db_index=True,
    )

    # The conditional unique constraint ``accounts_active_normalized_email_unique``
    # is not declared here yet: it still exists, under that exact name, on the
    # user table, and an index name exists once per database. It moves onto
    # this model in the accounts contract phase (plan issue D3.1d), which is
    # what drops it from the user table.

    def __str__(self):
        return f"identity:{self.user_id}:{self.identity_state}"


def identity_state_row(user):
    """The user's ``IdentityState`` row, or ``None`` when it has none.

    A missing row is the bulk-created-account case: before the reconciliation
    columns moved off the user model those accounts simply carried the empty
    column defaults, so every reader treats "no row" exactly as the old
    defaults -- state ``legacy``, no normalized key.
    """

    if user is None or getattr(user, "pk", None) is None:
        return None
    try:
        return user.identity
    except IdentityState.DoesNotExist:
        return None


def identity_state_of(user) -> str:
    """The user's identity state; ``legacy`` when no state row exists."""

    row = identity_state_row(user)
    if row is None:
        return IdentityState.States.LEGACY
    return row.identity_state


def normalized_email_of(user) -> str | None:
    """The user's stored normalized email key, if its row holds one."""

    row = identity_state_row(user)
    if row is None:
        return None
    return row.normalized_email


def set_identity_state(user, state: str) -> IdentityState:
    """Point the user's identity row at ``state``, creating it if needed.

    Used by the paths that deliberately move an account between states
    (activation, quarantine, absorption, development-owner bootstrap).
    """

    normalized_email = normalize_account_email(getattr(user, "email", None))
    row, _created = IdentityState.objects.update_or_create(
        user=user,
        defaults={
            "identity_state": state,
            "normalized_email": normalized_email,
        },
    )
    return row


class AccountIdentityAlias(models.Model):
    """Durable old-account ID to reviewed survivor mapping.

    ``source_user_id`` deliberately is not a foreign key. The absorbed source
    row stays in place during the rollback window, while the alias continues
    to resolve imports and audit evidence after any later privacy-approved
    contraction.
    """

    source_user_id = models.PositiveBigIntegerField(unique=True)
    survivor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="identity_aliases",
    )
    source_snapshot_id = models.CharField(max_length=64)
    mapping_checksum = models.CharField(max_length=64)
    review_reference = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # The table was created under the accounts app; the state move to
        # accounts_ext must not rebuild it, so the name stays pinned.
        db_table = "accounts_accountidentityalias"
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
        db_table = "accounts_accountidentityquarantine"
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
    """Idempotency/concurrency record for the one-time account-merge apply.

    Nothing at request time ever reads this table -- it exists only for
    ``scripts/prod/import_account_reconciliation.py`` (via
    ``scripts.prod.account_reconciliation``), which owns every line of logic
    that reads or writes it. It stays a real model registered under
    ``accounts_ext`` because a Django model needs an installed app to get a
    migration and cheap lookups against the auth user model, and
    ``scripts/prod`` is plain scripts, not an app -- not because this is a
    live application feature. See that package's module docstring for why
    this specifically stays a database row (its ``UniqueConstraint`` below is
    the compare-and-swap that makes two simultaneous applies of the same
    mapping resolve to exactly one merge) rather than becoming script-owned
    file/dict state the way the CMP learner import's claim-tracking does.
    """

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
        db_table = "accounts_accountreconciliationrun"
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

    Import-provenance state that once lived on a *live* model
    (``CustomUser.cmp_source_user_id``, a column every future query against
    the permanent account table would have carried) moved to this importer's
    own script-owned claims file
    (``accounts.services.cmp_learner_import.CmpClaimsStore``) rather than a
    database table -- see that module's docstring. This table stays a
    database row instead, deliberately: its whole value is that
    ``last_source_id`` advances *inside the same database transaction* as
    the batch of rows it counts, so a killed process leaves nothing for the
    watermark and the data to disagree about. A JSON file cannot join that
    transaction -- moving this table to one would trade a property no file
    can replicate (perfect crash-atomicity with the writes it tracks) for
    consistency with a design that does not need it. It is not a field on
    the auth user model or any other live domain model, so the principle
    that moved the source id off the user model does not ask this table to
    move either.
    """

    table = models.CharField(max_length=64, unique=True)
    last_source_id = models.BigIntegerField(default=0)
    rows_written = models.PositiveBigIntegerField(default=0)
    rows_skipped = models.PositiveBigIntegerField(default=0)
    completed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_cmplearnerimportprogress"
        ordering = ("table",)

    def __str__(self):
        return f"cmp-learner-import-progress:{self.table}"
