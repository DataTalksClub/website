"""Derive the published editorial records from the synced parser records.

The reviewed projection build attached a handful of derived fields to every
editorial record before the staged import stored it: the public image address,
and the profile credits that let a byline link to the person behind it. The
synced parsers store their own records instead -- the source-authored fields,
not derivations of other collections -- so the derivation travels with the read
model now, and a profile edit or a person's departure is reflected without a
content rebuild.

Every field derived here was derived by the projection build under the same
rules; nothing new is invented. A credit the people records cannot place keeps
its written name and no link, rather than exposing a source key as if it were
a person's name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scripts.build_public_projection import _podcast_event_lineage

#: One byline credit, as templates draw it: the source key, the display name
#: and, when the people records place it, the profile link.
Profile = dict[str, Any]

#: One linked piece of work on a profile: the role the person had, what the
#: work is called, and where the reader reaches it.
Relationship = dict[str, str]


def image_public_path(record: Mapping[str, Any]) -> str:
    """The public media address of a record's declared primary image."""

    source = record.get("image_source")
    if not isinstance(source, str) or not source:
        return ""
    return "/" + source.lstrip("/")


def _profile_credit(key: str, people_by_slug: Mapping[str, Mapping[str, Any]]) -> Profile:
    person = people_by_slug.get(key)
    if person is None:
        return {"key": "", "name": key, "public_path": ""}
    return {"key": key, "name": person["title"], "public_path": person["public_path"]}


def article_record(
    record: Mapping[str, Any], people_by_slug: Mapping[str, Mapping[str, Any]]
) -> dict:
    """The published article record: public image address and author byline."""

    derived = dict(record)
    image_path = image_public_path(record)
    derived["image_path"] = image_path
    derived["media_available"] = bool(image_path)
    derived["author_profiles"] = [
        _profile_credit(author, people_by_slug) for author in record.get("authors", ())
    ]
    return derived


def book_record(record: Mapping[str, Any], people_by_slug: Mapping[str, Mapping[str, Any]]) -> dict:
    """The published book record: public image address and author byline."""

    derived = dict(record)
    image_path = image_public_path(record)
    derived["image_path"] = image_path
    derived["media_available"] = bool(image_path)
    derived["author_profiles"] = [
        _profile_credit(author, people_by_slug) for author in record.get("authors", ())
    ]
    return derived


def podcast_record(
    record: Mapping[str, Any], people_by_slug: Mapping[str, Mapping[str, Any]]
) -> dict:
    """The published episode record: public image address and guest credits."""

    derived = dict(record)
    image_path = image_public_path(record)
    derived["image_path"] = image_path
    derived["media_available"] = bool(image_path)
    derived["guest_profiles"] = [
        _profile_credit(guest, people_by_slug) for guest in record.get("guests", ())
    ]
    return derived


def person_record(record: Mapping[str, Any], relationships: tuple[Relationship, ...]) -> dict:
    """The published profile record: public image address and derived credits.

    A profile is the one record whose derived fields all point *outward* -- at
    the work the person contributed, rather than at the people who contributed
    it -- so instead of resolving credits it receives them, already composed by
    :func:`person_relationships` for the whole collection in one pass.
    """

    derived = dict(record)
    image_path = image_public_path(record)
    derived["image_path"] = image_path
    derived["media_available"] = bool(image_path)
    derived["relationships"] = relationships
    derived["roles"] = sorted({item["role"] for item in relationships})
    return derived


def _credit(
    relationships: dict[str, list[Relationship]],
    record: Mapping[str, Any],
    keys: object,
    role: str,
) -> None:
    """Credit every catalogue person among ``keys`` with one role on ``record``.

    A key the people records do not publish -- a book's unresolved byline name,
    a guest who never had a profile -- credits no one: the record itself already
    shows the written name where it is displayed.
    """

    if not isinstance(keys, (list, tuple)):
        return
    for key in keys:
        held = relationships.get(key)
        if held is not None:
            held.append(
                {
                    "role": role,
                    "label": str(record["title"]),
                    "public_path": str(record["public_path"]),
                }
            )


def person_relationships(
    articles: tuple[dict[str, Any], ...],
    books: tuple[dict[str, Any], ...],
    podcasts: tuple[dict[str, Any], ...],
    event_records: tuple[dict[str, Any], ...],
    people_slugs: set[str],
) -> dict[str, tuple[Relationship, ...]]:
    """The linked work of every person, composed in one pass over the sources.

    The rules are the reviewed build's: authors credit their articles and
    books, guests their episodes, and speakers their events -- except that an
    event which is the recording of a published episode does not credit, as a
    speaker, a person the episode already credits as a guest, or the same
    appearance would show twice on the profile. The lineage match is the
    reviewed build's own recording-identity rule, applied here to the live
    event records; one recording matching more than one episode is the same
    refusal it was at build time.

    Only people the people collection publishes come back; a key with no
    catalogue profile has no page for a credit to live on.
    """

    relationships: dict[str, list[Relationship]] = {slug: [] for slug in people_slugs}
    for article in articles:
        _credit(relationships, article, article.get("authors", ()), "author")
    for book in books:
        _credit(relationships, book, book.get("authors", ()), "author")
    for podcast in podcasts:
        _credit(relationships, podcast, podcast.get("guests", ()), "guest")

    podcasts_by_slug = {podcast["slug"]: podcast for podcast in podcasts}
    lineage = _podcast_event_lineage(list(podcasts), list(event_records))
    for event in event_records:
        provenance = event.get("provenance") or {}
        source_key = provenance.get("source_key") if isinstance(provenance, Mapping) else None
        canonical_podcast = podcasts_by_slug.get(lineage.get(str(source_key or ""), ""))
        for speaker in event.get("speakers", ()):
            if not isinstance(speaker, Mapping) or speaker.get("key") not in relationships:
                continue
            if canonical_podcast is not None and speaker["key"] in canonical_podcast.get(
                "guests", ()
            ):
                continue
            relationships[speaker["key"]].append(
                {
                    "role": "speaker",
                    "label": str(event["title"]),
                    "public_path": str(event["public_path"]),
                }
            )
    return {slug: tuple(items) for slug, items in relationships.items()}
