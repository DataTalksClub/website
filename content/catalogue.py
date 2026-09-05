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

import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.db.models import Count, F, Max

from .models import ContentDocument, ContentRelease, ContentSource
from .public_text import strip_leaked_target_attributes, target_attribute_count

#: One published record, exactly as the import stored it. The pages read these
#: as mappings because that is what the catalogue's own records are: a book has
#: authors and a cover, a wiki page has relations, and no two kinds share a
#: shape worth naming a dataclass over.
Record = dict[str, Any]

#: The registered source whose active release publishes the editorial
#: catalogue: articles, podcasts, books, people, wiki pages and the derived
#: graph, search and route records that go with them.
PUBLIC_CONTENT_STABLE_ID = "dtc-public-content"

#: The collections the catalogue publishes.
COLLECTION_NAMES = (
    "articles",
    "podcasts",
    "books",
    "people",
    "wiki",
    "courses",
    "media",
)
#: The document kind each collection's records are stored under. The import
#: names a kind by dropping the collection's plural ``s``, which leaves
#: "people", "wiki" and "media" spelled as they are.
COLLECTION_KINDS = {name: name.rstrip("s") or name for name in COLLECTION_NAMES}


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


#: The audit of the accepted catalogue found exactly these supported metadata
#: markers left in published bodies. Cleaning them is a narrow, provenance-bound
#: repair, so the count is a canary: if it moves, the allowlist has broadened or
#: a body has drifted, and that is a refusal rather than a wider silent cleanup.
#: Articles are zero because their bodies are built by the article block builder,
#: which removes the legacy ``{:target="_blank"}`` directive before publication
#: instead of leaving 270 of them for a reader's request to clean up. Person bios
#: still take the older plain-text path, so their ten markers are removed here.
_EXPECTED_LEAKED_TARGET_MARKERS = {"article": 0, "people": 10}


@lru_cache(maxsize=8)
def _cleaned_bodies(release_id: str, kind: str) -> tuple[Record, ...]:
    """Published records whose body blocks have had leaked link metadata removed.

    The stored records are left untouched: each cleaned record is a copy, so the
    cache above still holds exactly what the database published.

    A database publishing no records of this kind has nothing to canary. That is
    an un-ingested database, not a drifted one, so it reads as an empty
    collection rather than a refusal.
    """

    held = _records(release_id, kind)
    markers = sum(
        target_attribute_count(block["text"])
        for record in held
        for block in record.get("blocks", ())
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )
    if held and markers != _EXPECTED_LEAKED_TARGET_MARKERS[kind]:
        raise ImproperlyConfigured("Public catalogue leaked target marker count mismatch.")
    return tuple(_cleaned_body(record) for record in held)


def _cleaned_body(record: Record) -> Record:
    copied = dict(record)
    raw_blocks = record.get("blocks")
    if not isinstance(raw_blocks, (list, tuple)):
        return copied
    blocks: list[Any] = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, dict):
            blocks.append(raw_block)
            continue
        block = dict(raw_block)
        text = block.get("text")
        if isinstance(text, str):
            block["text"] = strip_leaked_target_attributes(text, validated_projection=True)
        blocks.append(block)
    copied["blocks"] = blocks
    return copied


def articles() -> tuple[Record, ...]:
    """Every published article, newest first."""

    return _cleaned_bodies(active_release_id(), "article")


def article(slug: str) -> Record | None:
    """One article, or ``None`` when the catalogue does not publish it."""

    return _by_slug(articles(), slug)


_EVENT_UUID_PATH = re.compile(
    r"^/events/(?P<identity>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)"
)


def _event_public_path_stamp() -> tuple[int, str]:
    """A cheap stamp that moves whenever an event public path could have.

    One aggregate, not a row per event. Reading all 421 events on the way to
    every request, just to decide whether the people records are still valid,
    costs far more than rebuilding them on the rare occasion this changes.
    """

    from events.models import Event

    try:
        stamp = Event.objects.aggregate(total=Count("id"), latest=Max("updated_at"))
    except DatabaseError:
        return (0, "")
    return (int(stamp["total"] or 0), str(stamp["latest"] or ""))


def _event_public_paths() -> dict[str, str]:
    """The canonical public path of every event that has one."""

    from events.models import Event

    try:
        return {
            str(event_id): f"/events/{public_id}/{slug}"
            for event_id, public_id, slug in Event.objects.exclude(public_id=None).values_list(
                "id", "public_id", "slug"
            )
        }
    except DatabaseError:
        return {}


def _rewritten_event_path(path: object, replacements: Mapping[str, str]) -> object:
    if not isinstance(path, str):
        return path
    match = _EVENT_UUID_PATH.match(path)
    if match is None:
        return path
    return replacements.get(f"/events/{match.group('identity')}", path)


def people() -> tuple[Record, ...]:
    """Every published profile, with its links pointed at the live event routes."""

    return _people(active_release_id(), _event_public_path_stamp())


@lru_cache(maxsize=2)
def _people(release_id: str, event_stamp: tuple[int, str]) -> tuple[Record, ...]:
    """Profiles, cached on the two things that can change what they say.

    A profile still carries the uuid event paths its source was written with,
    while the running site addresses an event by its stable numeric
    ``Event.public_id``. One query resolves every referenced identity rather
    than turning a profile page into a round trip per relationship.

    A relationship naming an event the database does not have keeps its own
    path: the profile page is not the place to discover that an event is
    missing, and a dead link there is better than a 500.
    """

    replacements = {f"/events/{identity}": path for identity, path in _event_public_paths().items()}
    return tuple(
        {
            **person,
            "relationships": tuple(
                {
                    **relationship,
                    "public_path": _rewritten_event_path(
                        relationship.get("public_path", ""), replacements
                    ),
                }
                for relationship in person.get("relationships", ())
            ),
        }
        for person in _cleaned_bodies(release_id, "people")
    )


def person(slug: str) -> Record | None:
    """One profile, or ``None`` when the catalogue does not publish it."""

    return _by_slug(people(), slug)


def people_by_slug() -> dict[str, Record]:
    """Profiles indexed by the source key a credit names them with.

    A credit is resolved once per name drawn on a page, so both indexes are
    built with the profiles rather than scanned for each one.
    """

    return {person["slug"]: person for person in people() if "slug" in person}


def people_by_path() -> dict[str, Record]:
    """Profiles indexed by their own canonical address.

    A composed credit -- a podcast guest, an event speaker -- may carry no
    source key, so its profile link is the second way home.
    """

    return {person["public_path"]: person for person in people() if "public_path" in person}


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
