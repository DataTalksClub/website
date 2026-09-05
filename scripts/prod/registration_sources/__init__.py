"""Provider export readers for the historical registration sources.

The ``events`` app owns the domain: an ``Event``, a source-run, an aggregate
revision, and the neutral registry in ``events.importers`` that says what a
source reader must provide.  It does not own any provider's file format.  A Luma
directory or an Eventbrite archive becomes a
:class:`events.importers.DerivedSource` here, in the ingestion layer, and stops
being a "Luma export" at that moment.

Registration is explicit: :func:`register_source_readers` is called by the
ingest entry point that actually has an export to read.  Nothing imports these
readers at import time, so a plain web process never loads a provider parser,
and ``events`` never imports one at all.  A web process that has a configured
source but no reader fails closed -- ``derive_registered_source`` raises
``ProtectedSourceError("source_reader_unregistered")``, which Studio and the
management API already render as a bounded refusal.
"""

from __future__ import annotations

from events.importers import register_source_reader

from . import eventbrite, luma

__all__ = ["register_source_readers"]


def register_source_readers() -> None:
    """Make both provider readers available to ``events.importers``.

    Idempotent: registering the same provider again replaces its reader, so an
    entry point may call this without tracking whether it already has.
    """

    register_source_reader(luma.source_reader())
    register_source_reader(eventbrite.source_reader())
