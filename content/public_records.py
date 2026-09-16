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

#: One byline credit, as templates draw it: the source key, the display name
#: and, when the people records place it, the profile link.
Profile = dict[str, Any]


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
