"""Read the published editorial catalogue from the database.

The public pages -- the blog, the podcast, the book archive, the profiles, the
wiki -- are all published by one source, ``dtc-public-content``, whose active
release holds one :class:`~content.models.ContentDocument` per record. This
module is the one place that turns those rows into the records the views and
templates read, with a function per kind rather than one dictionary holding
every kind at once.

They share a module because they share everything that makes the read work:
the same source, the same active release, the same stored editorial order, and
the same cache key. Split per kind, that resolution would be written seven
times and could drift seven ways.

A database with no active release publishes nothing. That is a normal state,
not a failure: hubs render empty and detail lookups miss, which is what an
un-ingested database should do.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from django.db import DatabaseError
from django.db.models import F

from .models import ContentDocument, ContentRelease, ContentSource

#: One published record, exactly as the import stored it. The pages read these
#: as mappings because that is what the catalogue's own records are: a book has
#: authors and a cover, a wiki page has relations, and no two kinds share a
#: shape worth naming a dataclass over.
Record = dict[str, Any]

#: The registered source whose active release publishes the editorial
#: catalogue: articles, podcasts, books, people, wiki pages and the derived
#: graph, search and route records that go with them.
PUBLIC_CONTENT_STABLE_ID = "dtc-public-content"

#: The collections the catalogue publishes, and the document kind each record
#: is stored under.
COLLECTION_NAMES = (
    "articles",
    "podcasts",
    "books",
    "people",
    "wiki",
    "courses",
    "media",
)


def active_release_id() -> str:
    """The id of the release currently publishing the catalogue, or ``""``.

    One cheap indexed lookup, used as the cache key below. It changes exactly
    when an import activates a new release, which is the only thing that can
    change what the catalogue holds, so a cached read follows an import instead
    of outliving it.
    """

    try:
        active = (
            ContentSource.objects.filter(stable_id=PUBLIC_CONTENT_STABLE_ID, enabled=True)
            .values_list("active_release_id", flat=True)
            .first()
        )
    except DatabaseError:
        return ""
    return str(active or "")


def records(kind: str) -> tuple[Record, ...]:
    """Every published record of one kind, in the catalogue's own order."""

    return _records(active_release_id(), kind)


@lru_cache(maxsize=64)
def _records(release_id: str, kind: str) -> tuple[Record, ...]:
    """Every published record of ``kind``, rebuilt only when the release changes.

    ``release_id`` is the cache key, not an argument this reads: the query below
    resolves the active release itself, and naming it here is what makes the
    cached result follow an import.

    The order is the one stored beside each record. It is editorial -- newest
    first, season order, the sequence a hub lists in -- and no key the rows
    happen to sort by carries it.
    """

    try:
        rows = list(
            ContentDocument.objects.filter(
                content_kind=kind,
                is_published=True,
                release__status=ContentRelease.Status.ACTIVE,
                release__source__enabled=True,
                release__source__stable_id=PUBLIC_CONTENT_STABLE_ID,
                release_id=F("release__source__active_release_id"),
            ).values_list("adapter_metadata", flat=True)
        )
    except DatabaseError:
        return ()
    held = [
        (int((row or {}).get("position") or 0), (row or {}).get("record") or {}) for row in rows
    ]
    held.sort(key=lambda item: item[0])
    return tuple(record for _position, record in held)


def singleton(kind: str) -> Record:
    """The one record of a kind the catalogue publishes exactly one of.

    The wiki graph, the search index, the platform links and the route manifest
    are one document apiece rather than a collection of one. A database that
    publishes none gives an empty mapping, which every reader treats as absent.
    """

    held = records(kind)
    return held[0] if held else {}


def _by_slug(collection: tuple[Record, ...], slug: str) -> Record | None:
    return next((record for record in collection if record.get("slug") == slug), None)


def podcasts() -> tuple[Record, ...]:
    """Every published episode, in the catalogue's own order.

    This is not the order the pages list episodes in. Season and episode
    numbering is a podcast fact rather than a catalogue one, so
    :func:`content.podcast_content.ordered_podcasts` decides that.
    """

    return records("podcast")


def podcast(slug: str) -> Record | None:
    """One episode, or ``None`` when the catalogue does not publish it."""

    return _by_slug(podcasts(), slug)


def podcast_platforms() -> tuple[Record, ...]:
    """The listening platforms the show publishes, in the offered order."""

    return tuple(singleton("podcast_platforms").get("platforms", ()))


def books() -> tuple[Record, ...]:
    """The Book of the Week archive, newest first."""

    return records("book")


def book(slug: str) -> Record | None:
    """One book, or ``None`` when the catalogue does not publish it."""

    return _by_slug(books(), slug)
