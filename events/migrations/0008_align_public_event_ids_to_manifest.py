from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "event_identity_manifest.json"


def align_public_ids_to_manifest(apps, schema_editor) -> None:
    del schema_editor
    event_model = apps.get_model("events", "Event")
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_ids = {item["id"]: item["public_id"] for item in payload["events"]}
    # The bootstrap backfill in 0006 orders by (source_key, id), which follows the database
    # collation: PostgreSQL sorted four hyphenated tie groups differently from the reviewed
    # manifest's byte-order numbering.  Re-align the divergent rows to the manifest so every
    # environment serves the same canonical numeric paths.
    divergent = [
        event
        for event in event_model.objects.exclude(public_id=None).order_by("id")
        if str(event.id) in manifest_ids and event.public_id != manifest_ids[str(event.id)]
    ]
    if not divergent:
        return
    # public_id is UNIQUE, so park every divergent row on a temporary offset above the real
    # range before writing the target values; the offset keeps each parked value distinct.
    # Queryset updates keep the immutable-runtime-ID save guard out of the historical path.
    offset = max(manifest_ids.values()) + len(manifest_ids)
    for event in divergent:
        event_model.objects.filter(pk=event.pk).update(public_id=event.public_id + offset)
    for event in divergent:
        event_model.objects.filter(pk=event.pk).update(public_id=manifest_ids[str(event.id)])


class Migration(migrations.Migration):
    dependencies = [("events", "0007_reconcile_public_event_identity")]

    operations = [
        migrations.RunPython(align_public_ids_to_manifest, migrations.RunPython.noop),
    ]
