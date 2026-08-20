import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0008_align_public_event_ids_to_manifest"),
        ("jobs", "0001_initial"),
    ]
    operations = [
        migrations.CreateModel(
            name="EventQnaSession",
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
                    "state",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("open", "Open"),
                            ("closed", "Closed"),
                            ("archived", "Archived"),
                        ],
                        default="draft",
                        max_length=16,
                    ),
                ),
                ("backend_key", models.CharField(default="native", max_length=32)),
                ("backend_reference", models.CharField(blank=True, max_length=255)),
                ("revision", models.PositiveBigIntegerField(default=1)),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "event",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qna_session",
                        to="events.event",
                    ),
                ),
                (
                    "provisioning_job",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="qna_provisioning_session",
                        to="jobs.durablejob",
                    ),
                ),
            ],
            options={
                "ordering": ("event_id", "id"),
                "constraints": [
                    models.CheckConstraint(
                        condition=Q(("revision__gte", 1)),
                        name="events_qna_session_revision_positive",
                    ),
                    models.CheckConstraint(
                        condition=Q(("backend_key__gt", "")),
                        name="events_qna_session_backend_nonempty",
                    ),
                ],
            },
        ),
    ]
