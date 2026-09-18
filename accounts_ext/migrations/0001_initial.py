"""The accounts_ext extension models (plan issue D3.1).

Schema only. ``IdentityState`` is a genuinely new table; it receives the
identity reconciliation columns moved off the auth user model, and
``0002_identity_state_data`` -- a separate migration, deliberately -- copies
the values into it. The create and the copy are split because a migration
that does both cannot be half-reversed: unapplying it would run the
back-copy, drop the table and unwind four state-only model moves in one
step, so there is no state in which the values are restored onto the user
columns and the expand still stands. That state is exactly what a rollback of
the reader switch needs (playbook P7, decision D41), so it has to be
reachable: reverse ``0002`` alone and the values are back on the user columns
with this migration still applied.

The conditional unique constraint ``accounts_active_normalized_email_unique``
is NOT created here: it still exists, under that exact name, on the user table
(accounts.0001), and the index name cannot exist twice. The accounts contract
migration (plan issue D3.1d) drops the old index, and
``accounts_ext.0004_identitystate_unique`` creates the identically named one
on this table -- the constraint moves without a rename.

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
    ]
