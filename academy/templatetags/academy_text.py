"""Academy copy fields that may carry Markdown.

Most Academy fields are plain text and are autoescaped like any other value.  A
few carry inline Markdown -- a link in an FAQ answer, the contact address in a
form's error -- and the legal pages and articles are a Markdown body (with
tables).  Both render through
mistune with raw HTML escaped, then through the shared content sanitizer, so no
source text can reach a page as markup it did not ask for.
"""

from __future__ import annotations

import re
from functools import lru_cache

import mistune
from django import template
from django.utils.safestring import SafeString, mark_safe

from content.services import sanitize_rendered_html
from core.markdown_actions import markdown_buttons

register = template.Library()

_MARKDOWN = mistune.create_markdown(escape=True, plugins=["table", markdown_buttons])
_TABLE = re.compile(r"<table>.*?</table>", re.S)
_SINGLE_PARAGRAPH = re.compile(r"\A<p>(.*)</p>\s*\Z", re.S)


@lru_cache(maxsize=1024)
def _render(text: str) -> str:
    return sanitize_rendered_html("academy", str(_MARKDOWN(text)))


def _framed_tables(html: str) -> str:
    """Each table in the system's scroll frame: a named region with a focus stop.

    A table wider than the reading column scrolls inside its frame instead of
    widening the page, and the frame is reachable from the keyboard.  Two
    frames on one page may not share a name, so each is numbered.
    """

    counter = 0

    def frame(match: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        return (
            f'<div class="prose-scroll" role="region" tabindex="0" aria-label="Table {counter}">'
            f"{match.group(0)}</div>"
        )

    return _TABLE.sub(frame, html)


@register.filter
def academy_markdown(value: object) -> SafeString:
    """A Markdown body as sanitized block HTML, its tables framed to scroll."""

    return mark_safe(_framed_tables(_render(str(value or ""))))  # noqa: S308 - sanitized above


@register.filter
def academy_inline(value: object) -> SafeString:
    """One line of Markdown as sanitized inline HTML (no wrapping paragraph)."""

    rendered = _render(str(value or ""))
    match = _SINGLE_PARAGRAPH.match(rendered)
    return mark_safe(match.group(1) if match else rendered)  # noqa: S308 - sanitized above


@register.filter
def academy_fill(value: object, date: object) -> str:
    """A label with a ``{date}`` slot (the RSVP button) filled in."""

    return str(value or "").replace("{date}", str(date or ""))
