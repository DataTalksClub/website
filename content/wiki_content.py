"""Composition for the public wiki surfaces (design 5a rebuild, issue #179).

The wiki pages read the same checked wiki data the rest of the site reads, and this
module is where that data becomes the shapes the templates draw: the neighbourhood
the graph page draws, its headline totals, and the graph's node groups.

``/wiki/graph`` answers one question — *what is connected to what* — so each node
group leads with the nodes the graph itself makes busiest, and keeps its complete
list folded behind them for the reader who wants the whole inventory.  The A-Z
catalogue of wiki pages belongs to ``/wiki`` and the pages filed by kind belong to
``/wiki/special-pages``; this page links to them rather than restating them.

Nothing here invents a fact.  A node type, a count or an edge the data does not
carry raises :class:`~django.core.exceptions.ImproperlyConfigured` instead of
rendering a plausible-looking placeholder, the same rule ``core.home_content``
follows for the homepage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ImproperlyConfigured

# The ring the homepage draws its wiki hub on.  The graph page draws a hub of its
# own, as the same single scalable SVG, so the geometry is imported rather than
# copied: one ring, one definition.
from core.graph_layout import (
    RING_SPOKES,
    WIDE_WRAP_OVER,
    GraphLayout,
    GraphPoint,
    ring_layouts,
)

from .public_data import public_projection

# Every node type the graph carries, in the order /wiki/graph presents them: the
# heading, the one-line description of what that group is, and the page that
# already indexes that kind of thing in full (the graph page sends a reader
# there instead of being a second index).  A type outside this table is a data
# change the page must not silently swallow.
GRAPH_GROUPS: tuple[tuple[str, str, str, tuple[str, str] | None], ...] = (
    (
        "wiki",
        "Wiki topics",
        "Topic pages written by the community.",
        ("wiki-home", "browse every wiki page A–Z"),
    ),
    (
        "article",
        "Guides, comparisons and roadmaps",
        "The typed wiki pages: guides, comparisons, roadmaps, transitions and how-tos.",
        ("wiki-special", "browse the pages filed by kind"),
    ),
    ("topic", "Keywords", "Keywords the wiki indexes across its pages.", None),
    (
        "podcast",
        "Podcast episodes",
        "Episodes the wiki pages cite.",
        ("podcast", "browse every podcast episode"),
    ),
    ("person", "People", "Guests, hosts and authors the wiki pages link to.", None),
    ("book", "Books", "Books the wiki pages cite.", ("books", "browse every book")),
)

# The two totals the graph page leads with, and the key each one reads.
GRAPH_TOTALS: tuple[tuple[str, str], ...] = (
    ("nodes", "pages, people and keywords"),
    ("links", "connections between them"),
)

# How many nodes of a type the page shows before folding the rest away.  Twelve
# is a couple of rows of pills: enough to be a way in, few enough to read.
GROUP_ENTRY_POINTS = 12

# The graph page draws the wiki page with the most connections to other wiki
# pages, and draws this many of them: the ring the drawing is laid out on.
NEIGHBOURHOOD_SPOKES = RING_SPOKES


@dataclass(frozen=True, slots=True)
class GraphBrowse:
    """The page that indexes one node type in full, and how the graph names it."""

    url_name: str
    label: str


@dataclass(frozen=True, slots=True)
class GraphGroup:
    """One node type of the graph: its busiest nodes first, then all the rest.

    The group still carries every node the data has for the type — nothing is
    dropped and every node keeps the identifier the graph gives it — but the
    page shows the ways in and folds the remainder away, because a band of a
    thousand pills is an inventory rather than something a reader can explore.
    """

    key: str
    title: str
    description: str
    browse: GraphBrowse | None
    entries: tuple[dict[str, Any], ...]
    rest: tuple[dict[str, Any], ...]

    @property
    def nodes(self) -> tuple[dict[str, Any], ...]:
        return self.entries + self.rest

    @property
    def count(self) -> int:
        return len(self.entries) + len(self.rest)

    @property
    def rest_count(self) -> int:
        return len(self.rest)

    @property
    def heading_id(self) -> str:
        return f"graph-{self.key}-heading"


@dataclass(frozen=True, slots=True)
class GraphTotal:
    """One headline number of the graph, formatted the way the design writes it."""

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class GraphNeighbourhood:
    """The drawn hub, the neighbours drawn around it, and how many it really has.

    ``layouts`` is the same drawing arranged for a wide and for a narrow screen;
    the page renders both as one scalable SVG each and shows one at a time.
    """

    title: str
    url: str
    spokes: tuple[GraphPoint, ...]
    connections: int
    layouts: tuple[GraphLayout, ...]


def _graph() -> dict[str, Any]:
    return public_projection()["wiki_graph"]


def graph_nodes() -> tuple[dict[str, Any], ...]:
    return tuple(_graph().get("nodes", ()))


def node_connections() -> dict[str, int]:
    """Count the edges each node sits on, from the graph's own links."""

    connections: dict[str, int] = {}
    for link in _graph().get("links", ()):
        weight = int(link.get("weight", 1))
        for end in ("source", "target"):
            node_id = str(link.get(end, ""))
            if node_id:
                connections[node_id] = connections.get(node_id, 0) + weight
    return connections


def graph_groups() -> tuple[GraphGroup, ...]:
    """Group every graph node by its type, in the order the page presents them.

    Inside a group the busiest nodes come first — the ones the graph itself
    puts on the most edges — and the rest keep the order the data carries them
    in, which is by title.
    """

    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key, _title, _text, _to in GRAPH_GROUPS}
    for node in graph_nodes():
        node_type = str(node.get("type", ""))
        if node_type not in grouped:
            raise ImproperlyConfigured(
                f"The wiki graph carries an unknown node type: {node_type!r}"
            )
        grouped[node_type].append(node)
    connections = node_connections()
    groups: list[GraphGroup] = []
    for key, title, description, browse in GRAPH_GROUPS:
        nodes = grouped[key]
        busiest = sorted(
            nodes,
            key=lambda node: (-connections.get(str(node["id"]), 0), str(node["title"])),
        )[:GROUP_ENTRY_POINTS]
        entries = tuple(busiest)
        chosen = {str(node["id"]) for node in entries}
        groups.append(
            GraphGroup(
                key=key,
                title=title,
                description=description,
                browse=GraphBrowse(url_name=browse[0], label=browse[1]) if browse else None,
                entries=entries,
                rest=tuple(node for node in nodes if str(node["id"]) not in chosen),
            )
        )
    return tuple(groups)


def graph_totals() -> tuple[GraphTotal, ...]:
    """Return the graph's headline counts, and fail loudly when one is missing."""

    counts = _graph().get("counts", {})
    totals: list[GraphTotal] = []
    for key, label in GRAPH_TOTALS:
        value = counts.get(key)
        if not isinstance(value, int):
            raise ImproperlyConfigured(f"The wiki graph has no {key} count.")
        totals.append(GraphTotal(value=f"{value:,}", label=label))
    return tuple(totals)


def busiest_neighbourhood() -> GraphNeighbourhood:
    """Return the wiki page with the most wiki neighbours, and the ring around it.

    The hub, its neighbours and their order are all read from the graph's own
    edges; only the ring coordinates are a layout constant.
    """

    nodes_by_id = {str(node["id"]): node for node in graph_nodes()}
    neighbours: dict[str, dict[str, int]] = {}
    for link in _graph().get("links", ()):
        source = str(link.get("source", ""))
        target = str(link.get("target", ""))
        if not source.startswith("wiki:") or not target.startswith("wiki:") or source == target:
            continue
        weight = int(link.get("weight", 1))
        neighbours.setdefault(source, {})
        neighbours.setdefault(target, {})
        neighbours[source][target] = neighbours[source].get(target, 0) + weight
        neighbours[target][source] = neighbours[target].get(source, 0) + weight
    if not neighbours:
        raise ImproperlyConfigured("The wiki graph carries no edges between wiki pages.")

    hub_id = max(neighbours, key=lambda node_id: (len(neighbours[node_id]), node_id))
    hub = nodes_by_id.get(hub_id)
    if hub is None:
        raise ImproperlyConfigured(f"The wiki graph links to a node it does not carry: {hub_id}")
    ranked = sorted(
        neighbours[hub_id].items(),
        key=lambda item: (-item[1], str(nodes_by_id.get(item[0], {}).get("title", item[0]))),
    )
    if len(ranked) < NEIGHBOURHOOD_SPOKES:
        raise ImproperlyConfigured(
            f"{hub_id} has {len(ranked)} wiki neighbours, fewer than the {NEIGHBOURHOOD_SPOKES} "
            "the graph plots."
        )
    spokes = []
    for node_id, _weight in ranked[:NEIGHBOURHOOD_SPOKES]:
        neighbour = nodes_by_id.get(node_id)
        if neighbour is None:
            raise ImproperlyConfigured(
                f"The wiki graph links to a node it does not carry: {node_id}"
            )
        spokes.append(GraphPoint(title=str(neighbour["title"]), url=str(neighbour["url"])))
    return GraphNeighbourhood(
        title=str(hub["title"]),
        url=str(hub["url"]),
        spokes=tuple(spokes),
        connections=len(ranked),
        layouts=ring_layouts(
            GraphPoint(title=str(hub["title"]), url=str(hub["url"])),
            tuple(spokes),
            # Unlike the homepage, which draws eight hand-picked short titles,
            # this hub is whatever the data makes busiest, so a long label wraps
            # in the wide frame too instead of growing past its neighbours.
            wide_wrap_over=WIDE_WRAP_OVER,
        ),
    )
