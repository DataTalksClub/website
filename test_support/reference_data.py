"""Reviewed reference data the whole test database starts from.

Two sets of rows used to arrive in a test database because a migration inserted
them: the 421 public event identities with their 1,684 aliases, and the six
reviewed homepage testimonials.  Neither is schema, so neither belongs in a
migration -- but both are *reviewed content the product is built around*, and
hundreds of tests read them without creating them.

Loading them here keeps that arrangement while moving the rows to where data
belongs: this runs once, after ``migrate`` has built the test database, and
writes exactly what the retired seeding migrations wrote.  The production path
for the same two sets is ``scripts/prod/import_events.py`` and
``scripts/prod/import_testimonials.py``.
"""

from __future__ import annotations

from pathlib import Path

from django.db import transaction

#: Where the reviewed manifest sits is a fact about the one-time ingest, so the
#: event domain does not carry it. Test support and ``scripts/prod`` each name
#: the location they read, and neither imports it from the other.
EVENT_IDENTITY_MANIFEST = (
    Path(__file__).resolve().parents[1] / "temporary" / "content" / "event_identity_manifest.json"
)


def load_event_identities() -> tuple[int, int]:
    """Insert the reviewed event identity manifest exactly as it is checked in."""

    from events.identity import ensure_public_id_sequence, load_identity_manifest
    from events.models import Event, EventAlias

    manifest = load_identity_manifest(EVENT_IDENTITY_MANIFEST)
    events = [
        Event(
            id=item.id,
            public_id=item.public_id,
            title=item.title,
            slug=item.slug,
            source_repository=item.source.repository,
            source_revision=item.source.revision,
            source_key=item.source.source_key,
            source_path=item.source_path,
            source_checksum=item.source_checksum,
        )
        for item in manifest.events
    ]
    aliases = [
        EventAlias(
            event_id=item.id,
            source_path=alias.source_path,
            kind=alias.kind,
            reason=alias.reason,
            source_repository=alias.source.repository,
            source_revision=alias.source.revision,
            source_key=alias.source.source_key,
        )
        for item in manifest.events
        for alias in item.aliases
    ]
    Event.objects.bulk_create(events)
    EventAlias.objects.bulk_create(aliases)
    ensure_public_id_sequence()
    return len(events), len(aliases)


def load_homepage_testimonials() -> int:
    from courses.services.testimonials import import_homepage_testimonials

    return import_homepage_testimonials().total


@transaction.atomic
def load_reviewed_reference_data() -> dict[str, int]:
    """Populate a freshly migrated database with the reviewed reference rows."""

    from events.models import Event

    if Event.objects.exists():
        return {"events": 0, "aliases": 0, "testimonials": 0}
    events, aliases = load_event_identities()
    return {
        "events": events,
        "aliases": aliases,
        "testimonials": load_homepage_testimonials(),
    }
