"""The canonical FAQ block's helpers (`templates/core/_faq.html`).

Every list of questions on the site renders through one partial and takes one
data shape: an iterable of items carrying ``question`` and either
``answer_html`` (a rendered, sanitised fragment) or ``answer`` (plain text),
with an optional ``id`` for a deep link.  Items may be mappings or objects.

Two decisions are made here rather than by each page:

* ``faq_columns`` -- two columns only for four or more short items; anything
  else reads as one column at the reading measure.
* ``faq_structured_data`` -- the ``FAQPage`` JSON-LD, written from the same
  items the block draws, so the visible questions and the structured data can
  never disagree.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from typing import Any

from django import template
from django.utils.html import strip_tags
from django.utils.safestring import SafeString, mark_safe

from core.runtime_config import get_str_setting

register = template.Library()

#: Two columns need at least this many items; fewer leave a ragged half-row.
TWO_COLUMN_MIN_ITEMS = 4
#: An item is "short" when its question and answer stay within these lengths
#: (plain text characters), so two side-by-side columns stay scannable.
SHORT_QUESTION_CHARS = 90
SHORT_ANSWER_CHARS = 280

_WHITESPACE = re.compile(r"\s+")
_CLOSING_BRACES = re.compile(r"\}(?=\})")
#: Block boundaries become spaces before the tags go, so two paragraphs or list
#: items do not run together into one word.
_BLOCK_END = re.compile(r"</(?:p|li|h[1-6]|div|pre|tr|td|th|blockquote|dd|dt)>|<br\s*/?>", re.I)


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def answer_text(item: Any) -> str:
    """The plain text of one item's answer, as structured data reads it."""

    answer_html = _field(item, "answer_html")
    if answer_html:
        text = html.unescape(strip_tags(_BLOCK_END.sub(" ", str(answer_html))))
    else:
        text = str(_field(item, "answer") or "")
    return _WHITESPACE.sub(" ", text).strip()


@register.filter
def faq_columns(items: Iterable[Any] | None) -> int:
    """2 for four or more short items, otherwise 1."""

    rows = list(items or ())
    if len(rows) < TWO_COLUMN_MIN_ITEMS:
        return 1
    for item in rows:
        if len(str(_field(item, "question") or "")) > SHORT_QUESTION_CHARS:
            return 1
        if len(answer_text(item)) > SHORT_ANSWER_CHARS:
            return 1
    return 2


def faq_page_node(items: Iterable[Any], page_url: str = "") -> dict[str, Any]:
    """The ``FAQPage`` node for *items*; questions with an id get their own URL."""

    questions = []
    for item in items:
        question: dict[str, Any] = {"@type": "Question"}
        anchor = _field(item, "id")
        if page_url and anchor:
            question["@id"] = question["url"] = f"{page_url}#{anchor}"
        question["name"] = str(_field(item, "question") or "")
        question["acceptedAnswer"] = {"@type": "Answer", "text": answer_text(item)}
        questions.append(question)
    node: dict[str, Any] = {"@context": "https://schema.org", "@type": "FAQPage"}
    if page_url:
        node["url"] = page_url
    node["mainEntity"] = questions
    return node


@register.simple_tag(takes_context=True)
def faq_structured_data(context: template.Context, items: Iterable[Any] | None) -> SafeString:
    """One ``<script type="application/ld+json">`` FAQPage for *items*."""

    rows = list(items or ())
    if not rows:
        return SafeString("")
    request = context.get("request")
    page_url = ""
    if request is not None:
        page_url = f"{get_str_setting('site.origin.canonical')}{request.path}"
    text = json.dumps(faq_page_node(rows, page_url), ensure_ascii=False)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    # A page body must never carry a template delimiter ("}}" reads as a leak
    # to the site's template checks), and whitespace between braces is free.
    text = _CLOSING_BRACES.sub("} ", text)
    return mark_safe(f'<script type="application/ld+json">{text}</script>')  # noqa: S308 - escaped JSON
