"""Template access to the shared person chip.

The credits a public page holds are small: an event speaker is a key, a name and
a profile path, and a composed podcast guest is a name and a profile path.  The
portrait belongs to the person rather than to the mention, so this filter joins
the two just before the page draws them:

.. code-block:: html+django

   {% include "public/_person_chip.html" with person=speaker|person_chip %}

It lives in ``content`` because the public read models it resolves against do;
``core`` must not import a domain application.
"""

from __future__ import annotations

from typing import Any

from django import template

from content.person_chip import PersonChip
from content.person_chip import person_chip as resolve_person_chip

register = template.Library()


@register.filter(name="person_chip")
def person_chip(credit: Any) -> PersonChip:
    """Return one credited person with the portrait the people records hold."""

    return resolve_person_chip(credit)


@register.filter(name="story_chip")
def story_chip(story: Any) -> PersonChip:
    """Return a member story's teller as a chip, so a quote card names its person
    the way every other card on the page does.

    A member is not in the people records: the name links to the public post the
    quote was taken from (``source_url``, an external page, so the include passes
    ``chip_external=True``) and the portrait is the story's own.  A story with no
    portrait draws the chip's initials disc, and one whose portrait fails to load
    is swapped for the same disc by ``core/media_fallback.js``.
    """

    portrait = str(getattr(story, "portrait_url", "") or "")
    return PersonChip(
        name=str(story.name),
        public_path=str(getattr(story, "source_url", "") or ""),
        image_path=portrait,
        media_available=bool(portrait),
    )
