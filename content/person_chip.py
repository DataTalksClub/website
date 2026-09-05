"""One shared way to name a person on a public page.

A person turns up on nearly every public surface — an article byline, a book
byline, an event's speakers, a podcast episode's guests — and until now each
surface drew them differently: some linked the name, some printed a bare source
key, and every one of them drew the design system's striped stand-in disc even
when the people records held a real portrait.

This module resolves any of those credits to the same four facts, so
``templates/public/_person_chip.html`` can draw them identically:

* the display name;
* the profile path, empty when the credit has no profile to link to;
* the portrait, empty when the people records hold none;
* whether that portrait is actually available.

The credit a collection carries is deliberately small — ``{key, name,
public_path}`` — because it is the collection's own fact.  The portrait belongs
to the person, so it is joined here at render time rather than copied into every
record that mentions them.  A credit the people records cannot place keeps its
name and loses only its link and its face: the reader still sees who it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from . import catalogue


@dataclass(frozen=True, slots=True)
class PersonChip:
    """One credited person, ready to draw."""

    name: str
    public_path: str
    image_path: str
    media_available: bool


def _field(credit: Any, *names: str) -> str:
    for name in names:
        if isinstance(credit, dict):
            value = credit.get(name)
        else:
            value = getattr(credit, name, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def person_chip(
    credit: Any,
    people_by_slug: dict[str, dict[str, Any]] | None = None,
    people_by_path: dict[str, dict[str, Any]] | None = None,
) -> PersonChip:
    """Return one credit as the value every public surface draws it from.

    ``credit`` is anything that names a person and may link to them: a projected
    ``author_profiles``/``guest_profiles`` entry, an event speaker, one of the
    composed ``Author``/``Guest`` values, or a person record itself.
    """

    name = _field(credit, "name", "title")
    if not name:
        raise ImproperlyConfigured("A credited person must have a name.")
    public_path = _field(credit, "public_path")
    if public_path and not public_path.startswith("/"):
        raise ImproperlyConfigured("A credited person's link must be a site path.")

    # The composed values a page already holds (a podcast `Guest`, for instance) carry no source
    # key, so the profile path is the second way home: it is the person's own canonical address.
    if people_by_slug is None:
        people_by_slug = catalogue.people_by_slug()
    if people_by_path is None:
        people_by_path = catalogue.people_by_path()
    key = _field(credit, "key", "slug")
    person = people_by_slug.get(key) or people_by_path.get(public_path) or {}
    image_path = str(person.get("image_path") or "")
    return PersonChip(
        name=name,
        public_path=public_path,
        image_path=image_path,
        media_available=bool(person.get("media_available")) and bool(image_path),
    )


def person_chips(
    credits: Any,
    people_by_slug: dict[str, dict[str, Any]] | None = None,
    people_by_path: dict[str, dict[str, Any]] | None = None,
) -> tuple[PersonChip, ...]:
    """Return a whole byline, speaker list or guest list in one call."""

    if people_by_slug is None:
        people_by_slug = catalogue.people_by_slug()
    if people_by_path is None:
        people_by_path = catalogue.people_by_path()
    return tuple(person_chip(credit, people_by_slug, people_by_path) for credit in credits or ())
