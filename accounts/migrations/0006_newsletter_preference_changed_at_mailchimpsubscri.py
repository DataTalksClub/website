import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_cmplearnerclaim_cmplearnerimportbinding"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="newsletter_preference_changed_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "When this account's newsletter preference was last "
                    "changed locally; null means the current value was never "
                    "locally decided. Set automatically on save. Audience "
                    "imports never overwrite a recorded local decision."
                ),
                null=True,
                verbose_name="Newsletter preference locally changed at",
            ),
        ),
        migrations.CreateModel(
            name="MailchimpSubscriptionImportRun",
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
                (
                    "source_name",
                    models.CharField(
                        help_text=(
                            "File name of the subscribed CSV; the containing path is never stored."
                        ),
                        max_length=255,
                    ),
                ),
                ("source_sha256", models.CharField(max_length=64)),
                ("source_bytes", models.PositiveBigIntegerField()),
                (
                    "as_of",
                    models.DateField(
                        help_text=("The operator-declared date the audience export was cut.")
                    ),
                ),
                (
                    "applied",
                    models.BooleanField(
                        default=True,
                        help_text=(
                            "False for a dry run: counts were computed, nothing was written."
                        ),
                    ),
                ),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("report", models.JSONField(default=dict)),
            ],
            options={
                "ordering": ("-recorded_at", "-id"),
            },
        ),
    ]
