"""The rich elements an article's Markdown body can place: named fenced blocks.

The mechanism is community-base's ```` ```embed ```` fence
(``community_base/content_sync/rendering.py``, FORMAT.md section 4.1), the one
AI Shipping Labs also uses for its ```` ```eventwidget ````: a fence whose info
string names the element, with the element's fields as YAML inside it.  An
author places it exactly where it belongs in the text::

    ```facts
    label: Key facts
    items:
      - label: Start
        value: April 2027
    ```

The elements (documented for authors in ``_docs/content-authoring.md``):

``facts``    a label and label/value items -- the design system's spec strip.
``cards``    two or three cards, each a title, a Markdown body and an optional
             link drawn as the card's secondary button.
``steps``    a numbered sequence -- the shared steps component
             (``core/_steps.html``); each item is a line of text or a
             title/body pair.
``callout``  a Markdown note in the system's callout (``info``, ``quiet`` or
             ``attention``).

A fence with any other info string stays a code sample.  The fields are
checked when the article is synced (``component_block``), so a malformed block
fails the sync instead of reaching a page, and they are rendered for the page
(``component_view``) through the same Markdown-and-sanitizer path as the body.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import yaml

COMPONENT_FENCES = frozenset({"facts", "cards", "steps", "callout"})

CALLOUT_VARIANTS = frozenset({"info", "quiet", "attention"})

#: An element is a handful of short fields; anything longer is a body, not a block.
MAX_ITEMS = 12
MAX_TEXT = 2_000


class ComponentError(ValueError):
    """A fenced element whose fields do not match its shape."""


def _text(value: Any, field: str, *, optional: bool = False) -> str:
    if optional and (value is None or value == ""):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise ComponentError(f"{field} must be non-empty text")
    return value.strip()


def _items(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list) or not value or len(value) > MAX_ITEMS:
        raise ComponentError(f"{field} must be a list of 1 to {MAX_ITEMS} items")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ComponentError(f"{field} must be a mapping")
    return value


def _link(value: Any, field: str) -> dict[str, str] | None:
    if value is None:
        return None
    link = _mapping(value, field)
    href = _text(link.get("href"), f"{field}.href")
    if not href.startswith(("/", "https://")):
        raise ComponentError(f"{field}.href must be a site path or an https address")
    return {"label": _text(link.get("label"), f"{field}.label"), "href": href}


def _facts(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "label": _text(data.get("label"), "facts.label"),
        "items": [
            {
                "label": _text(_mapping(item, "facts item").get("label"), "facts item label"),
                "value": _text(item.get("value"), "facts item value"),
            }
            for item in _items(data.get("items"), "facts.items")
        ],
    }


def _cards(data: Mapping[str, Any]) -> dict[str, Any]:
    items = _items(data.get("items"), "cards.items")
    if len(items) > 3:
        raise ComponentError("cards hold at most three cards")
    cards = []
    for item in items:
        card = _mapping(item, "card")
        cards.append(
            {
                "title": _text(card.get("title"), "card title"),
                "body": _text(card.get("body"), "card body"),
                "link": _link(card.get("link"), "card link"),
            }
        )
    return {"items": cards}


def _steps(data: Mapping[str, Any]) -> dict[str, Any]:
    steps = []
    for item in _items(data.get("items"), "steps.items"):
        if isinstance(item, str):
            steps.append({"title": "", "body": _text(item, "step")})
            continue
        step = _mapping(item, "step")
        steps.append(
            {
                "title": _text(step.get("title"), "step title", optional=True),
                "body": _text(step.get("body"), "step body", optional=True),
            }
        )
    return {"items": steps}


def _callout(data: Mapping[str, Any]) -> dict[str, Any]:
    variant = str(data.get("variant") or "info")
    if variant not in CALLOUT_VARIANTS:
        raise ComponentError(f"callout variant must be one of {sorted(CALLOUT_VARIANTS)}")
    return {"variant": variant, "body": _text(data.get("body"), "callout body")}


_SHAPES: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "facts": _facts,
    "cards": _cards,
    "steps": _steps,
    "callout": _callout,
}


def component_block(name: str, source: str) -> dict[str, Any]:
    """One fenced element as the body block the sync stores, its fields checked."""

    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError as error:
        raise ComponentError(f"{name} block is not valid YAML") from error
    return {"kind": "component", "component": name, **_SHAPES[name](_mapping(data, name))}


def component_view(block: Mapping[str, Any], render: Callable[[str], str]) -> dict[str, Any]:
    """A stored element as the page draws it, its Markdown fields rendered by ``render``."""

    name = str(block.get("component") or "")
    if name not in _SHAPES:
        raise ComponentError(f"unknown article element {name!r}")
    fields = _SHAPES[name]({key: value for key, value in block.items() if key != "kind"})
    if name == "cards":
        for card in fields["items"]:
            card["body_html"] = render(card["body"])
    if name == "callout":
        fields["body_html"] = render(fields["body"])
    return {"name": name, **fields}
