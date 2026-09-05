"""The safety contract for the destinations a public graph draws.

The wiki graph and the podcast episode graph are drawn as links on a public
page, from records the site did not author. Every destination goes through
:func:`safe_public_graph_url` before a template can hand it to ``href``, and the
wiki graph document goes through :func:`validate_wiki_graph` before a view
returns it, so a stored graph that breaks the contract is a refusal rather than
something the page renders and hopes about.

These live apart from the catalogue that stores the graph on purpose: the
contract is the same whether the graph reaches a reader from the database at
request time or is being checked by the offline builder.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured


def safe_public_graph_url(value: Any) -> str:
    """Return a safe root-relative graph destination, or an empty destination.

    Empty URLs are valid for nodes that have no public page.  Every non-empty
    value must remain a path on this site. The graph's search nodes may carry one
    bounded ``q`` parameter and page links may carry a safe fragment;
    protocol-relative, absolute, credential-bearing, control-character and
    traversal values are rejected before a template can expose them as ``href``.
    """

    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        return ""
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return ""
    if "\\" in value or not value.startswith("/") or value.startswith("//"):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return ""
    if parsed.query:
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2)
        except ValueError:
            return ""
        if parsed.path != "/wiki/search" or set(query) != {"q"} or len(query["q"]) != 1:
            return ""
        if len(query["q"][0]) > 200 or any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in query["q"][0]
        ):
            return ""
    if parsed.fragment and re.fullmatch(r"[A-Za-z0-9._~%-]+", parsed.fragment) is None:
        return ""
    for index, character in enumerate(value):
        if character != "%":
            continue
        if (
            index + 2 >= len(value)
            or value[index + 1] not in "0123456789abcdefABCDEF"
            or value[index + 2] not in "0123456789abcdefABCDEF"
        ):
            return ""
    try:
        decoded = unquote(parsed.path, errors="strict")
        decoded_fragment = unquote(parsed.fragment, errors="strict")
    except UnicodeDecodeError:
        return ""
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in decoded) or any(
        segment == ".." for segment in decoded.split("/")
    ):
        return ""
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in decoded_fragment):
        return ""
    return value


def validate_wiki_graph(graph: Any) -> None:
    """Validate graph references before a stored graph reaches a view."""

    if not isinstance(graph, dict):
        raise ImproperlyConfigured("Public wiki graph is invalid.")
    raw_nodes = graph.get("nodes")
    raw_links = graph.get("links")
    if not isinstance(raw_nodes, list) or not isinstance(raw_links, list):
        raise ImproperlyConfigured("Public wiki graph collections are invalid.")
    node_ids: set[str] = set()
    for node in raw_nodes:
        if not isinstance(node, dict):
            raise ImproperlyConfigured("Public wiki graph contains a malformed node.")
        node_id = node.get("id")
        if (
            not isinstance(node_id, str)
            or not node_id
            or node_id in node_ids
            or not isinstance(node.get("label"), str)
            or not node["label"]
            or not isinstance(node.get("title"), str)
            or not node["title"]
            or not isinstance(node.get("type"), str)
            or not node["type"]
            or not isinstance(node.get("url", ""), str)
            or safe_public_graph_url(node.get("url", "")) != node.get("url", "")
        ):
            raise ImproperlyConfigured("Public wiki graph node contract is invalid.")
        node_ids.add(node_id)
    for link in raw_links:
        if not isinstance(link, dict):
            raise ImproperlyConfigured("Public wiki graph contains a malformed link.")
        source = link.get("source")
        target = link.get("target")
        if (
            not isinstance(source, str)
            or source not in node_ids
            or not isinstance(target, str)
            or target not in node_ids
            or not isinstance(link.get("kind"), str)
            or not link["kind"]
            or isinstance(link.get("weight"), bool)
            or not isinstance(link.get("weight"), int)
            or link["weight"] < 1
        ):
            raise ImproperlyConfigured("Public wiki graph link contract is invalid.")
