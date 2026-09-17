"""The accounts_ext extension models (plan issue D3.1).

``IdentityState`` is a genuinely new table; it receives the identity
reconciliation columns moved off the auth user model, and the data copy below
fills it from the user table. The conditional unique constraint
``accounts_active_normalized_email_unique`` is NOT created here: it still
exists, under that exact name, on the user table (accounts.0001), and the
index name cannot exist twice. The accounts contract migration (plan issue
D3.1d) drops the old index, and ``accounts_ext.0002`` creates the identically
named one on this table -- the constraint moves without a rename.

The four identity evidence models (``AccountIdentityAlias``,
``AccountIdentityQuarantine``, ``AccountReconciliationRun``,
``CmpLearnerImportProgress``) move their app registration from ``accounts``
to ``accounts_ext``. Their physical tables already exist under the pinned
``db_table`` names, so their creation here is recorded state-only
(``SeparateDatabaseAndState`` with empty database operations); the matching
state-only deletion is recorded by ``accounts.0007_move_identity_models_state``,
which this migration depends on. Tables
and rows are never rebuilt or dropped.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

BATCH_SIZE = 1000


def copy_identity_state(apps, schema_editor):
    """Copy the reconciliation values off every user row, verbatim.

    Guarded: users that already carry an ``IdentityState`` row are skipped,
    so the copy stays idempotent on databases where rows appeared between the
    code deploy and the migrate run.
    """

    CustomUser = apps.get_model("accounts", "CustomUser")
    IdentityState = apps.get_model("accounts_ext", "IdentityState")

    existing_user_ids = set(
        IdentityState.objects.values_list("user_id", flat=True).iterator()
    )
    identities = []
    for user in CustomUser.objects.iterator():
        if user.pk in existing_user_ids:
            continue
        identities.append(
            IdentityState(
                user_id=user.pk,
                normalized_email=user.normalized_email,
                identity_state=user.identity_state,
            )
        )
        if len(identities) >= BATCH_SIZE:
            IdentityState.objects.bulk_create(identities, batch_size=BATCH_SIZE)
            identities = []
    if identities:
        IdentityState.objects.bulk_create(identities, batch_size=BATCH_SIZE)


def restore_user_identity_columns(apps, schema_editor):
    """Reverse copy: write the reconciliation values back onto the user rows.

    The user-table columns still exist while this runs (the accounts contract
    migration has not been reversed yet). Users without a row keep whatever
    their columns hold, mirroring a row-less bulk-created account.
    """

    CustomUser = apps.get_model("accounts", "CustomUser")
    IdentityState = apps.get_model("accounts_ext", "IdentityState")

    identities_by_user_id = dict(
        IdentityState.objects.values_list("user_id", "normalized_email", "identity_state")
    )
    for user in CustomUser.objects.iterator():
        identity = identities_by_user_id.get(user.pk)
        if identity is None:
            continue
        user.normalized_email = identity[0]
        user.identity_state = identity[1]
        user.save()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0007_move_identity_models_state"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # The physical table exists under the pinned name; only the app
            # registration state moves from accounts to accounts_ext.
            state_operations=[
                migrations.CreateModel(
                    name="CmpLearnerImportProgress",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("table", models.CharField(max_length=64, unique=True)),
                        ("last_source_id", models.BigIntegerField(default=0)),
                        ("rows_written", models.PositiveBigIntegerField(default=0)),
                        ("rows_skipped", models.PositiveBigIntegerField(default=0)),
                        ("completed", models.BooleanField(default=False)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        "db_table": "accounts_cmplearnerimportprogress",
                        "ordering": ("table",),
                    },
                ),
            ],
            database_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="AccountIdentityQuarantine",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                            ),
                        ),
                        ("fingerprint", models.CharField(max_length=64, unique=True)),
                        ("source_snapshot_id", models.CharField(max_length=64)),
                        ("source_user_ids", models.JSONField(default=list)),
                        ("reason_codes", models.JSONField(default=list)),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("open", "Open"),
                                    ("resolved", "Resolved"),
                                ],
                                default="open",
                                max_length=16,
                            ),
                        ),
                        ("resolution_reference", models.CharField(max_length=128, blank=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("resolved_at", models.DateTimeField(blank=True, null=True)),
                    ],
                    options={
                        "db_table": "accounts_accountidentityquarantine",
                        "ordering": ("created_at", "id"),
                        "indexes": [
                            models.Index(
                                fields=["status", "created_at"], name="accounts_quarantine_status"
                            )
                        ],
                    },
                ),
            ],
            database_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="AccountReconciliationRun",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                            ),
                        ),
                        ("source_snapshot_id", models.CharField(max_length=64)),
                        ("mapping_checksum", models.CharField(max_length=64)),
                        (
                            "mode",
                            models.CharField(
                                choices=[("apply", "Apply"), ("rollback_check", "Rollback check")],
                                max_length=16,
                            ),
                        ),
                        ("source_account_count", models.PositiveBigIntegerField()),
                        ("survivor_account_count", models.PositiveBigIntegerField()),
                        ("alias_count", models.PositiveBigIntegerField(default=0)),
                        ("quarantine_count", models.PositiveBigIntegerField(default=0)),
                        ("relationship_counts", models.JSONField(default=dict)),
                        ("relationship_checksums", models.JSONField(default=dict)),
                        ("report_checksum", models.CharField(max_length=64)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                    ],
                    options={
                        "db_table": "accounts_accountreconciliationrun",
                        "ordering": ("created_at", "id"),
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("source_snapshot_id", "mapping_checksum", "mode"),
                                name="accounts_reconciliation_run_unique",
                            )
                        ],
                    },
                ),
            ],
            database_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="AccountIdentityAlias",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("source_user_id", models.PositiveBigIntegerField(unique=True)),
                        ("source_snapshot_id", models.CharField(max_length=64)),
                        ("mapping_checksum", models.CharField(max_length=64)),
                        ("review_reference", models.CharField(max_length=128)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "survivor",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="identity_aliases",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "accounts_accountidentityalias",
                        "ordering": ("source_user_id",),
                        "indexes": [
                            models.Index(
                                fields=["survivor", "source_user_id"],
                                name="accounts_alias_survivor_source",
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    ("source_user_id", models.F("survivor_id")), _negated=True
                                ),
                                name="accounts_identity_alias_distinct",
                            )
                        ],
                    },
                ),
            ],
            database_operations=[],
        ),
        migrations.CreateModel(
            name="IdentityState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "normalized_email",
                    models.EmailField(
                        blank=True, db_index=True, editable=False, max_length=254, null=True
                    ),
                ),
                (
                    "identity_state",
                    models.CharField(
                        choices=[
                            ("legacy", "Legacy-compatible"),
                            ("active", "Verified active identity"),
                            ("quarantined", "Needs identity review"),
                            ("absorbed", "Absorbed into a survivor"),
                        ],
                        db_index=True,
                        default="legacy",
                        max_length=16,
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="identity",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.RunPython(copy_identity_state, restore_user_identity_columns),
    ]
