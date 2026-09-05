"""Markers for tests that need content this checkout may not have.

*Media object bytes* live in the configured media store, not the database, so
they are the one kind of public content ``test_support.reference_data`` cannot
seed. A fresh checkout has an unhydrated local store, and every recorded object
answers with a fail-closed 502 -- the store behaving correctly, not the route
being wrong.

A test that needs them says so here. Before the catalogue moved into the
database these tests iterated an empty catalogue and passed without asserting
anything, which is worse than skipping: it looked like coverage.

Event content used to be marked the same way, because it had no importer. It has
one now (``scripts/prod/import_events.py``), the reference data runs it, and
those tests assert against real rows again.
"""

from __future__ import annotations

import unittest


def _media_bytes_are_available() -> bool:
    from django.conf import settings

    from content.media_store import local_media_root

    if str(getattr(settings, "PUBLIC_MEDIA_STORE_BACKEND", "local")) != "local":
        return False
    try:
        return any(local_media_root().glob("*"))
    except OSError:
        return False


requires_media_bytes = unittest.skipUnless(
    _media_bytes_are_available(),
    "the local media store holds no objects (run scripts/prod/sync_public_media_hydrate.py)",
)
