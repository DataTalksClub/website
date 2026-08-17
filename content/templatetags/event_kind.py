"""The status-pill variant an event's kind is drawn in.

The events index, the event page and the homepage all mark an event with the same
pill, and the choice of surface is a three-way branch: sand for anything already
past, mint for a live podcast recording, lavender for every other upcoming
session.

It lives here rather than in the template because a four-tag ``{% if %}`` chain
inside a ``class`` attribute is exactly what
``core.accessibility_registry.template_readability_issues`` refuses, and because
splitting that chain across lines writes the newlines into the rendered
attribute.  A filter keeps the template one readable line and the emitted class
exactly two words.
"""

from __future__ import annotations

from django import template

register = template.Library()

PAST = "status-pill-wait"
PODCAST = "status-pill-mint"
UPCOMING = "status-pill-open"


@register.filter
def kind_variant(kind: object, is_past: object = False) -> str:
    """Return the pill variant for ``kind``, past or upcoming."""

    if is_past:
        return PAST
    if str(kind) == "podcast":
        return PODCAST
    return UPCOMING
