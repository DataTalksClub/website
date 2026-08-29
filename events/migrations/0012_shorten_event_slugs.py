from __future__ import annotations

import html
import re

from django.db import migrations

MAX_EVENT_SLUG_LENGTH = 64
SLUG_PARTS = re.compile(r"[^a-z0-9]+")


def _event_title_slug(title: str) -> str:
    slug = SLUG_PARTS.sub("-", html.unescape(title).casefold().strip()).strip("-")
    if len(slug) <= MAX_EVENT_SLUG_LENGTH:
        return slug
    shortened = slug[:MAX_EVENT_SLUG_LENGTH].rstrip("-")
    return shortened.rsplit("-", 1)[0] if "-" in shortened else shortened


def shorten_event_slugs(apps, schema_editor) -> None:
    del schema_editor
    event_model = apps.get_model("events", "Event")
    alias_model = apps.get_model("events", "EventAlias")

    for event in event_model.objects.order_by("id"):
        previous_slug = event.slug
        shortened_slug = _event_title_slug(event.title)
        if previous_slug == shortened_slug:
            continue
        alias_model.objects.filter(
            event_id=event.id,
            kind="legacy_uuid",
            source_path=f"/events/{event.id}/{previous_slug}",
        ).update(source_path=f"/events/{event.id}/{shortened_slug}")
        event_model.objects.filter(pk=event.pk).update(slug=shortened_slug)


class Migration(migrations.Migration):
    dependencies = [("events", "0011_alter_eventqnaquestion_author_name")]

    operations = [
        migrations.RunPython(shorten_event_slugs, migrations.RunPython.noop),
    ]
