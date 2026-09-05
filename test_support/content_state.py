"""Markers for tests that need content this checkout may not have.

Two kinds of public content are database-owned but not seeded by
``test_support.reference_data``, for reasons that are facts about the project
rather than about any test:

*Event content* has no importer. ``scripts/prod/import_events.py`` records that
its only current source is the legacy repository being retired, and that the
replacement source is undecided, so a database carries event *identities* with
no ``EventContent`` rows behind them. Pages render empty, which is correct.

*Media object bytes* live in the configured media store, not the database. A
fresh checkout has an unhydrated local store, so every recorded object answers
with a fail-closed 502 -- the store behaving correctly, not the route being
wrong.

A test that needs either says so here. Before the catalogue moved into the
database these tests iterated an empty catalogue and passed without asserting
anything, which is worse than skipping: it looked like coverage.
"""

from __future__ import annotations

import unittest


def _events_are_published() -> bool:
    from django.db import DatabaseError

    from events.queries import published_event_records

    try:
        return bool(published_event_records())
    except DatabaseError:
        return False


def _media_bytes_are_available() -> bool:
    from django.conf import settings

    from content.media_store import local_media_root

    if str(getattr(settings, "PUBLIC_MEDIA_STORE_BACKEND", "local")) != "local":
        return False
    try:
        return any(local_media_root().glob("*"))
    except OSError:
        return False


requires_published_events = unittest.skipUnless(
    _events_are_published(),
    "no event content is published (its importer is blocked on a source decision)",
)

requires_media_bytes = unittest.skipUnless(
    _media_bytes_are_available(),
    "the local media store holds no objects (run scripts/prod/sync_public_media_hydrate.py)",
)
