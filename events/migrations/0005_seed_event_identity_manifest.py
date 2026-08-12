from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations


def seed_event_identity_manifest(apps, schema_editor) -> None:
    event_model = apps.get_model("events", "Event")
    alias_model = apps.get_model("events", "EventAlias")
    manifest_path = Path(__file__).resolve().parents[1] / "event_identity_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = payload["events"]
    for item in entries:
        source = item["source"]
        event, created = event_model.objects.get_or_create(
            id=item["id"],
            defaults={
                "title": item["title"],
                "slug": item["slug"],
                "source_repository": source["repository"],
                "source_revision": source["revision"],
                "source_key": source["source_key"],
                "source_path": item["source_path"],
                "source_checksum": item["source_checksum"],
            },
        )
        if not created and (
            event.source_repository != source["repository"]
            or event.source_revision != source["revision"]
            or event.source_key != source["source_key"]
        ):
            raise RuntimeError("event identity manifest UUID/source conflict")
        if not created:
            event.title = item["title"]
            event.slug = item["slug"]
            event.source_path = item["source_path"]
            event.source_checksum = item["source_checksum"]
            event.save(update_fields=("title", "slug", "source_path", "source_checksum", "updated_at"))
        for alias in item["aliases"]:
            alias_row, alias_created = alias_model.objects.get_or_create(
                source_path=alias["source_path"],
                defaults={
                    "event_id": event.id,
                    "kind": alias["kind"],
                    "reason": alias["reason"],
                    "source_repository": source["repository"],
                    "source_revision": source["revision"],
                    "source_key": source["source_key"],
                },
            )
            if not alias_created and alias_row.event_id != event.id:
                raise RuntimeError("event alias target conflict")


class Migration(migrations.Migration):
    dependencies = [("events", "0004_event_historicaleventmapping_event_eventalias")]

    operations = [migrations.RunPython(seed_event_identity_manifest, migrations.RunPython.noop)]
