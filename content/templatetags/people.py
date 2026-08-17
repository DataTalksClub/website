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
