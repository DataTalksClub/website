"""Composition for the public wiki surfaces (design 5a rebuild, issue #179).

The wiki pages read the same checked wiki data the rest of the site reads, and this
module is where that data becomes the shapes the templates draw: the graph's node
groups, its headline totals, and the one neighbourhood the graph page plots.

Nothing here invents a fact.  A node type, a count or an edge the data does not
carry raises :class:`~django.core.exceptions.ImproperlyConfigured` instead of
rendering a plausible-looking placeholder, the same rule ``core.home_content``
follows for the homepage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ImproperlyConfigured

# The ring the homepage plots its wiki hub on.  The graph page plots a hub of its
# own, so the geometry is imported rather than copied: one ring, one definition.
from core.home_content import WIKI_GRAPH_POSITIONS

from .public_data import public_projection

# Every node type the graph carries, in the order /wiki/graph presents them, with
# the heading and the one-line description of what that group is.  A type outside
# this table is a data change the page must not silently swallow.
GRAPH_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("wiki", "Wiki topics", "Topic pages written by the community."),
    (
        "article",
        "Guides, comparisons and roadmaps",
        "The typed wiki pages: guides, comparisons, roadmaps, transitions and how-tos.",
    ),
    ("topic", "Keywords", "Keywords the wiki indexes across its pages."),
    ("podcast", "Podcast episodes", "Episodes the wiki pages cite."),
    ("person", "People", "Guests, hosts and authors the wiki pages link to."),
    ("book", "Books", "Books the wiki pages cite."),
)

# The two totals the graph page leads with, and the key each one reads.
GRAPH_TOTALS: tuple[tuple[str, str], ...] = (
    ("nodes", "pages, people and keywords"),
    ("links", "connections between them"),
)

# The graph page plots the wiki page with the most connections to other wiki
# pages, and draws this many of them.  Both come from the ring above.
NEIGHBOURHOOD_SPOKES = len(WIKI_GRAPH_POSITIONS)


@dataclass(frozen=True, slots=True)
class GraphGroup:
    """One node type of the graph, with every node the data carries for it."""

    key: str
    title: str
    description: str
    nodes: tuple[dict[str, Any], ...]

    @property
    def count(self) -> int:
        return len(self.nodes)

    @property
    def heading_id(self) -> str:
        return f"graph-{self.key}-heading"


@dataclass(frozen=True, slots=True)
class GraphTotal:
    """One headline number of the graph, formatted the way the design writes it."""

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class GraphSpoke:
    """One neighbour of the plotted hub, at its position on the ring."""

    title: str
    url: str
    left: float
    top: float


@dataclass(frozen=True, slots=True)
class GraphNeighbourhood:
    """The plotted hub, its drawn neighbours, and how many it really has."""

    title: str
    url: str
    spokes: tuple[GraphSpoke, ...]
    connections: int


def _graph() -> dict[str, Any]:
    return public_projection()["wiki_graph"]


def graph_nodes() -> tuple[dict[str, Any], ...]:
    return tuple(_graph().get("nodes", ()))


def graph_groups() -> tuple[GraphGroup, ...]:
    """Group every graph node by its type, in the order the page presents them."""

    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key, _title, _text in GRAPH_GROUPS}
    for node in graph_nodes():
        node_type = str(node.get("type", ""))
        if node_type not in grouped:
            raise ImproperlyConfigured(
                f"The wiki graph carries an unknown node type: {node_type!r}"
            )
        grouped[node_type].append(node)
    return tuple(
        GraphGroup(key=key, title=title, description=description, nodes=tuple(grouped[key]))
        for key, title, description in GRAPH_GROUPS
    )


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
    for (node_id, _weight), (left, top) in zip(
        ranked[:NEIGHBOURHOOD_SPOKES], WIKI_GRAPH_POSITIONS, strict=True
    ):
        neighbour = nodes_by_id.get(node_id)
        if neighbour is None:
            raise ImproperlyConfigured(
                f"The wiki graph links to a node it does not carry: {node_id}"
            )
        spokes.append(
            GraphSpoke(
                title=str(neighbour["title"]),
                url=str(neighbour["url"]),
                left=left,
                top=top,
            )
        )
    return GraphNeighbourhood(
        title=str(hub["title"]),
        url=str(hub["url"]),
        spokes=tuple(spokes),
        connections=len(ranked),
    )
