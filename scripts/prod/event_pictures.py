"""Event pictures for the import scripts, read from a directory outside the repo.

Every event is created with its picture (:mod:`events.pictures`), so an import
that creates an event needs the picture bytes at that moment.  They live in
``~/prod/dtc-data/event-pictures/`` (raw ingestion input, never in the
repository), one file per event named ``<key>.png|.jpg|.jpeg|.webp``.  The
key is whatever the import identifies the event by: the event's content UUID
or public id for the reviewed manifest, the provider's event id (for Luma the
``evt-...`` id) for provider discovery.

Render a missing one with banner-generator's ``dtc-event-speaker`` template
(``_docs/runbooks/event-cli.md``, "Render the standard event picture").
"""

from __future__ import annotations

from pathlib import Path

from events.pictures import EventPicture

EVENT_PICTURES_ROOT = Path.home() / "prod" / "dtc-data" / "event-pictures"
_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


class EventPictureDirectory:
    """Look up an event's picture by any of its keys."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else EVENT_PICTURES_ROOT

    def lookup(self, *keys: object) -> EventPicture | None:
        """The first ``<key><suffix>`` file that exists, validated; else ``None``."""

        if not self.root.is_dir():
            return None
        for key in keys:
            if key is None or str(key).strip() == "":
                continue
            for suffix in _SUFFIXES:
                candidate = self.root / f"{key}{suffix}"
                if candidate.is_file():
                    return EventPicture.from_bytes(candidate.read_bytes())
        return None
