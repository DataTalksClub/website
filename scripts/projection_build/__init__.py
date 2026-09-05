"""Projection-build helpers, kept out of the runtime app.

These modules parse and validate event descriptions, speaker credits and their
link policy while building the public projection file. Nothing a public request
touches reads them any more: events are database rows read through
``events.queries``.

They live here, next to ``scripts/build_public_projection.py`` and
``scripts/build_event_description_bridge.py``, because those are their only
callers and because a module under ``content/`` is a module the runtime can
import by accident. They stay because the parsing in them is what a database
ingest for event content will be built from -- see the source decision recorded
in ``scripts/prod/import_events.py``.
"""
