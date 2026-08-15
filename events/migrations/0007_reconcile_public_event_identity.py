from __future__ import annotations

from django.db import migrations, models
from django.db.models import Max, Q


def seed_allocator_and_aliases(apps, schema_editor) -> None:
    del schema_editor
    event_model = apps.get_model("events", "Event")
    alias_model = apps.get_model("events", "EventAlias")
    sequence_model = apps.get_model("events", "EventPublicIdSequence")

    latest_public_id = event_model.objects.aggregate(value=Max("public_id"))["value"] or 0
    sequence_model.objects.create(id=1, next_public_id=latest_public_id + 1)

    for event in event_model.objects.exclude(public_id=None).order_by("public_id"):
        source = {
            "source_repository": event.source_repository,
            "source_revision": event.source_revision,
            "source_key": event.source_key,
        }
        date_aliases = tuple(
            alias_model.objects.filter(event_id=event.id, kind="legacy_path").order_by(
                "source_path"
            )
        )
        for alias in date_aliases:
            alias.kind = "legacy_date_path"
            alias.save(update_fields=("kind",))
            alias_model.objects.get_or_create(
                source_path=f"{alias.source_path}/",
                defaults={
                    "event_id": event.id,
                    "kind": "legacy_date_path",
                    "reason": "Accepted trailing-slash spelling of the reviewed date/title alias.",
                    **source,
                },
            )
        for source_path, reason in (
            (
                f"/events/{event.id}/{event.slug}",
                "Formerly canonical UUID/current-title-slug public Event URL.",
            ),
            (
                f"/events/{event.id}",
                "Supported UUID-only public Event convenience URL.",
            ),
        ):
            alias_model.objects.get_or_create(
                source_path=source_path,
                defaults={
                    "event_id": event.id,
                    "kind": "legacy_uuid",
                    "reason": reason,
                    **source,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("events", "0006_event_public_id")]

    operations = [
        migrations.CreateModel(
            name="EventPublicIdSequence",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("next_public_id", models.PositiveIntegerField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=Q(("id", 1)), name="events_public_id_sequence_singleton"
                    ),
                    models.CheckConstraint(
                        condition=Q(("next_public_id__gt", 0)),
                        name="events_public_id_sequence_positive",
                    ),
                ]
            },
        ),
        migrations.AddConstraint(
            model_name="event",
            constraint=models.CheckConstraint(
                condition=Q(("public_id__isnull", True), ("public_id__gt", 0), _connector="OR"),
                name="events_event_public_id_positive",
            ),
        ),
        migrations.AlterField(
            model_name="eventalias",
            name="kind",
            field=models.CharField(
                choices=[
                    ("legacy_date_path", "Legacy date/title path"),
                    ("legacy_uuid", "Legacy UUID path"),
                    ("legacy_path", "Legacy path"),
                    ("title_slug", "Previous title slug"),
                    ("reviewed", "Reviewed alias"),
                ],
                max_length=24,
            ),
        ),
        migrations.RunPython(seed_allocator_and_aliases, migrations.RunPython.noop),
    ]
