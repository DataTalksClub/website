from __future__ import annotations

from django.db import migrations, models


def backfill_public_ids(apps, schema_editor) -> None:
    del schema_editor
    event_model = apps.get_model("events", "Event")
    event_ids = event_model.objects.order_by("source_key", "id").values_list("id", flat=True)
    for public_id, event_id in enumerate(event_ids, start=1):
        event_model.objects.filter(pk=event_id).update(public_id=public_id)


class Migration(migrations.Migration):
    dependencies = [("events", "0005_seed_event_identity_manifest")]

    operations = [
        migrations.AddField(
            model_name="event",
            name="public_id",
            field=models.PositiveIntegerField(
                db_index=True,
                editable=False,
                null=True,
                unique=True,
            ),
        ),
        migrations.RunPython(backfill_public_ids, migrations.RunPython.noop),
    ]
