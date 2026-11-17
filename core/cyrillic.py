"""Detect Cyrillic script in text that would become user-facing.

The site is English-only.  Every Unicode block assigned to the Cyrillic script
is matched, so a homoglyph (Cyrillic U+0441 standing in for Latin "c") is caught
as reliably as a whole Russian word.
"""

from __future__ import annotations

import re
from typing import Final

#: Cyrillic (U+0400-04FF), Cyrillic Supplement (U+0500-052F), Cyrillic
#: Extended-C (U+1C80-1C8F), Cyrillic Extended-A (U+2DE0-2DFF), Cyrillic
#: Extended-B (U+A640-A69F) and Cyrillic Extended-D (U+1E030-1E08F).
CYRILLIC_PATTERN: Final = re.compile(
    "[\u0400-\u052f\u1c80-\u1c8f\u2de0-\u2dff\ua640-\ua69f\U0001e030-\U0001e08f]"
)


def contains_cyrillic(text: str) -> bool:
    """Return whether ``text`` holds any character from a Cyrillic block."""

    return CYRILLIC_PATTERN.search(text) is not None
