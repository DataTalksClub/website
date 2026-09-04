"""Display-level normalisation for curriculum titles.

Upstream course repositories number their lessons in the title itself, and they
do it raggedly: ML Zoomcamp 2026 ships "1.1 Introduction to Machine Learning",
"1.2 ML vs Rule-Based Systems" and then a bare "Setting up the Environment",
while LLM Zoomcamp 2026 numbers nothing at all.  Wherever the position is
already communicated by the surrounding chrome -- the rail's ordinal disc, the
module page's numbered lesson list, a prev/next button that says which way it
goes -- printing the prefix again numbers the row twice and makes the one
unprefixed row look like the odd one out.

This normalises for display only.  The stored title is untouched, so the
heading, the admin and every export still carry the course's own wording.
"""

import re

from django import template

register = template.Library()

# A section number ("1.1", "2.10.3") or an explicitly punctuated ordinal
# ("1.", "3)").  Deliberately narrower than a bare leading integer: a title such
# as "10 Minutes to Pandas" opens with a number that is part of its name, and
# silently deleting it would be a wrong title rather than a quieter one.
_ORDINAL_PREFIX_RE = re.compile(r"\A\d+(?:\.\d+)+[.)]?[ \t]+|\A\d+[.)][ \t]+")


@register.filter
def unit_display_title(value: object) -> str:
    """Return a curriculum title without the ordinal its position already states."""

    title = str(value or "")
    normalised = _ORDINAL_PREFIX_RE.sub("", title, count=1).strip()
    # A title that is nothing but its ordinal keeps it; an empty row says less
    # than a numbered one.
    return normalised or title
