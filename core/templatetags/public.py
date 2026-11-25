from __future__ import annotations

import html
import re

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

register = template.Library()

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+|/(?!/)[^\s)]*)\)")


@register.filter
def public_text(value: object) -> str:
    """Render source text with line breaks and conservative external Markdown links.

    Book discussions are imported as text, not trusted HTML.  Escaping happens before the small
    link conversion so historical markup, including tags and script-looking snippets, remains
    inert in the public response.
    """

    # Slack exports in the source projection contain a small number of encoded quote/angle
    # entities.  Decode those first so they display as the original text, then escape the result
    # below; this keeps ``&lt;script&gt;`` inert while avoiding a visible ``&amp;gt;`` in replies.
    raw = html.unescape(str(value))
    output: list[str] = []
    position = 0
    for match in _MARKDOWN_LINK.finditer(raw):
        output.append(conditional_escape(raw[position : match.start()]))
        label = conditional_escape(match.group(1))
        url = conditional_escape(match.group(2))
        if match.group(2).startswith("/"):
            output.append('<a class="app-link" href="' + url + '">' + label + "</a>")
        else:
            output.append(
                '<a class="app-link" href="'
                + url
                + '" target="_blank" rel="noopener noreferrer">'
                + label
                + '<span class="sr-only"> (opens in a new tab)</span></a>'
            )
        position = match.end()
    output.append(conditional_escape(raw[position:]))
    return mark_safe("<br>".join("".join(output).splitlines()))


@register.filter
def prose_runs(blocks: object) -> list[dict]:
    """Group a body's consecutive list items into one list run.

    The projection keeps a flat block list; a page draws each run of source list
    items as one real ``<ul>``/``<ol>`` so the items read (and are announced) as a
    list.  Every other block passes through unchanged.
    """

    runs: list[dict] = []
    for block in blocks or ():
        if block.get("kind") == "list_item":
            ordered = bool(block.get("ordered"))
            previous = runs[-1] if runs else None
            if previous and previous["kind"] == "list" and previous["ordered"] == ordered:
                previous["items"].append(block)
            else:
                runs.append({"kind": "list", "ordered": ordered, "items": [block]})
        else:
            runs.append(block)
    return runs
