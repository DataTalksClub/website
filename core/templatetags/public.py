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
