import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("account", "0009_emailaddress_unique_primary_email"),
        ("accounts", "0010_remove_customuser_email_course_updates_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="identity_state",
            field=models.CharField(
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
        migrations.AddField(
            model_name="customuser",
            name="normalized_email",
            field=models.EmailField(
                blank=True,
                db_index=True,
                editable=False,
                max_length=254,
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="AccountIdentityQuarantine",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("source_snapshot_id", models.CharField(max_length=64)),
                ("source_user_ids", models.JSONField(default=list)),
                ("reason_codes", models.JSONField(default=list)),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("resolved", "Resolved")],
                        default="open",
                        max_length=16,
                    ),
                ),
                (
                    "resolution_reference",
                    models.CharField(blank=True, max_length=128),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ("created_at", "id"),
                "indexes": [
                    models.Index(
                        fields=["status", "created_at"],
                        name="accounts_quarantine_status",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="AccountReconciliationRun",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("source_snapshot_id", models.CharField(max_length=64)),
                ("mapping_checksum", models.CharField(max_length=64)),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("apply", "Apply"),
                            ("rollback_check", "Rollback check"),
                        ],
                        max_length=16,
                    ),
                ),
                ("source_account_count", models.PositiveBigIntegerField()),
                ("survivor_account_count", models.PositiveBigIntegerField()),
                (
                    "alias_count",
                    models.PositiveBigIntegerField(default=0),
                ),
                (
                    "quarantine_count",
                    models.PositiveBigIntegerField(default=0),
                ),
                ("relationship_counts", models.JSONField(default=dict)),
                ("relationship_checksums", models.JSONField(default=dict)),
                ("report_checksum", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ("created_at", "id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("source_snapshot_id", "mapping_checksum", "mode"),
                        name="accounts_reconciliation_run_unique",
                    )
                ],
            },
        ),
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
                (
                    "source_user_id",
                    models.PositiveBigIntegerField(unique=True),
                ),
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
                "ordering": ("source_user_id",),
                "indexes": [
                    models.Index(
                        fields=["survivor", "source_user_id"],
                        name="accounts_alias_survivor_source",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=~models.Q(
                            source_user_id=models.F("survivor_id")
                        ),
                        name="accounts_identity_alias_distinct",
                    )
                ],
            },
        ),
    ]
