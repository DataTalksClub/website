from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_auth", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="APIRateSubject",
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
                ("subject_hash", models.CharField(max_length=64)),
                (
                    "cost_class",
                    models.CharField(
                        choices=[
                            ("read", "Read"),
                            ("write", "Write"),
                            ("adaptive", "Adaptive digest"),
                        ],
                        max_length=16,
                    ),
                ),
                ("revision", models.PositiveBigIntegerField(default=0)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("subject_hash", "cost_class"),
                        name="mgmt_rate_subject_class_unique",
                    )
                ],
            },
        ),
    ]
