"""Call-to-action buttons written in a Markdown body.

A body asks for a button by writing a link alone on its own line and marking
it::

    [Subscribe to the Substack](https://aishippingblog.com/){.button}
    [Join the course](https://example.com/join){.button-primary}

That line renders as the site's shared action control -- the same
``cta cta-<variant> interactive-lift`` markup ``templates/core/_button.html``
draws -- inside a ``<p class="prose-action">``.  ``{.button}`` is the secondary
variant, ``{.button-primary}`` the primary one; no other class can come from a
body.  The marker only works on a line that holds nothing but the link: written
after a link inside a sentence, or spelled as anything else (``{.btn}``,
``{.button-red}``), it is dropped and the link renders as an ordinary link, so
a body never shows the marker as text.

The plugin is installed in every mistune pipeline that renders synced bodies
(articles, Academy pages, documentation, course Markdown).  Each pipeline still
runs its own sanitizer afterwards; this module only produces markup.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

if TYPE_CHECKING:
    import mistune

#: Marker -> ``_button.html`` variant.  The only classes a body can ask for.
BUTTON_VARIANTS = {"button": "secondary", "button-primary": "primary"}

#: The paragraph a body button sits in.
BUTTON_PARAGRAPH_CLASS = "prose-action"

#: Every class string a rendered body button can carry, for sanitizers that
#: admit ``class`` on a link only for these exact values.
BUTTON_CLASS_VALUES = frozenset(
    f"cta cta-{variant} interactive-lift" for variant in BUTTON_VARIANTS.values()
)

_DESTINATION = r"(?:https?://|mailto:|/(?!/)|\#)[^\s()<>]*"
_BUTTON_LINE = (
    r"^ {0,3}\[(?P<cta_label>[^\[\]\n]+)\]\((?P<cta_href>" + _DESTINATION + r")\)"
    r"\{\.(?P<cta_marker>button(?:-primary)?)\}[ \t]*(?:\n|$)"
)
# A marker written straight after a link's closing parenthesis.  Matched in
# inline text wherever the block rule above did not take the whole line.
_LINK_MARKER = r"(?<=\))\{\.[A-Za-z][A-Za-z0-9_-]*\}"
_LINK_MARKER_RE = re.compile(r"(\]\([^)\n]*\))\{\.[A-Za-z][A-Za-z0-9_-]*\}")
_BUTTON_LINE_RE = re.compile(_BUTTON_LINE)


def is_button_line(line: str) -> bool:
    """Return whether one source line is a body button."""

    return _BUTTON_LINE_RE.match(line.strip("\n")) is not None


def strip_link_markers(text: str) -> str:
    """Drop every ``{.marker}`` written straight after a Markdown link.

    The plain-text projections of a body (search text, reading time, feeds)
    read the label and nothing else.
    """

    return _LINK_MARKER_RE.sub(r"\1", text)


def _is_external(href: str) -> bool:
    parts = urlsplit(href)
    return parts.scheme in {"http", "https"} and (parts.hostname or "") not in {
        "datatalks.club",
        "www.datatalks.club",
    }


def button_html(label_html: str, href: str, variant: str) -> str:
    """Return one body button: the shared control in its own paragraph.

    ``label_html`` is already-rendered inline markup; ``href`` is a raw
    destination (escaped by the template).
    """

    control = render_to_string(
        "core/_button.html",
        {
            "href": href,
            "label": mark_safe(label_html),  # noqa: S308 - rendered by mistune, sanitized later
            "variant": variant,
            "rel": "noopener noreferrer" if _is_external(href) else "",
        },
    )
    markup = " ".join(control.split()).replace(" >", ">")
    return f'<p class="{BUTTON_PARAGRAPH_CLASS}">{markup}</p>\n'


def _parse_button(block: Any, match: re.Match[str], state: Any) -> int:
    state.append_token(
        {
            "type": "cta_button",
            "text": match.group("cta_label").strip(),
            "attrs": {
                "href": match.group("cta_href"),
                "variant": BUTTON_VARIANTS[match.group("cta_marker")],
            },
        }
    )
    return match.end()


def _parse_marker(inline: Any, match: re.Match[str], state: Any) -> int:
    return match.end()


def _render_button(renderer: Any, text: str, href: str, variant: str) -> str:
    return button_html(text, href, variant)


def markdown_buttons(md: mistune.Markdown) -> None:
    """mistune plugin: body buttons, and stray markers dropped from links."""

    md.block.register("cta_button", _BUTTON_LINE, _parse_button, before="paragraph")
    md.inline.register("cta_marker", _LINK_MARKER, _parse_marker)
    if md.renderer and md.renderer.NAME == "html":
        md.renderer.register("cta_button", _render_button)
