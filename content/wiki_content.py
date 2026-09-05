"""Composition for the public wiki surfaces (design system rebuild, issue #179).

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

import re
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

from . import catalogue
from .podcast_routes import podcast_canonical_path, podcast_public_id
from .public_graph import safe_public_graph_url

_PODCAST_GRAPH_PATH = re.compile(r"/podcast/s[0-9]+e[0-9]+/[a-z0-9_][a-z0-9_.-]*")

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


@dataclass(frozen=True, slots=True)
class EpisodeGraphNeighbour:
    """One deduplicated, directly incident node for a podcast episode.

    The graph can carry more than one record for the same pair of nodes.  The
    episode surface keeps that graph fact in the contract: ``weight`` is the
    deterministic aggregate, ``raw_links`` records how many source records
    contributed, and ``relation_kinds`` retains every relation kind rather than
    selecting one at render time.
    """

    id: str
    label: str
    title: str
    type: str
    url: str
    relation_kinds: tuple[str, ...]
    weight: int
    raw_links: int

    @property
    def relation_kind(self) -> str:
        """The stable human-readable form of one or more graph relations."""

        return ", ".join(self.relation_kinds)

    @property
    def kind(self) -> str:
        """Compatibility name for callers that use the graph link field name."""

        return self.relation_kind


@dataclass(frozen=True, slots=True)
class EpisodeGraph:
    """The projection-backed graph contract exposed to a podcast template.

    ``neighbors`` is complete and is the semantic fallback.  ``visual_neighbors``
    is only the bounded ring shown in the compact drawing; it is never the source
    of truth for the accessible list.
    """

    state: str
    title: str
    url: str
    hub_id: str
    neighbors: tuple[EpisodeGraphNeighbour, ...]
    visual_neighbors: tuple[EpisodeGraphNeighbour, ...]
    raw_links: int
    unique_neighbors: int
    layouts: tuple[GraphLayout, ...]

    @property
    def available(self) -> bool:
        return self.state == "available"

    @property
    def visual_count(self) -> int:
        return len(self.visual_neighbors)

    @property
    def neighbours(self) -> tuple[EpisodeGraphNeighbour, ...]:
        """British spelling for Python callers; templates use ``neighbors``."""

        return self.neighbors


def _hierarchical_podcast_graph_path(record: dict[str, Any]) -> str:
    """Build the graph-only destination for one validated podcast record.

    The route owner supplies the stable episode identifier format.  The graph
    still has to translate older projection node URLs at its boundary so a
    graph link never reintroduces the retired flat ``.html`` spelling.
    """

    slug = record.get("slug")
    season = record.get("season")
    episode = record.get("episode")
    if (
        not isinstance(slug, str)
        or re.fullmatch(r"[a-z0-9_][a-z0-9_.-]*", slug) is None
        or isinstance(season, bool)
        or not isinstance(season, int)
        or season < 1
        or isinstance(episode, bool)
        or not isinstance(episode, int)
        or episode < 1
    ):
        return ""
    reviewed_path = podcast_canonical_path(slug)
    if reviewed_path != f"/podcast/{slug}.html":
        return safe_public_graph_url(reviewed_path)
    return safe_public_graph_url(
        f"/podcast/{podcast_public_id(season=season, episode=episode)}/{slug}"
    )


def _podcast_graph_url(node: dict[str, str], *, podcast_records: dict[str, Any]) -> str:
    """Return only a hierarchical, known podcast destination for a graph node."""

    slug = node["id"].removeprefix("podcast:")
    record = podcast_records.get(slug)
    if not isinstance(record, dict):
        # A few checked graph IDs normalize source slugs (for example by
        # dropping a leading underscore or a filename suffix).  The checked
        # source URL remains the unambiguous identity in that case; use it only
        # to find the catalogue record, then emit the rebuilt route below.
        source_url = node["source_url"]
        record = next(
            (
                candidate
                for candidate in podcast_records.values()
                if isinstance(candidate, dict) and candidate.get("public_path") == source_url
            ),
            None,
        )
    if isinstance(record, dict):
        return _hierarchical_podcast_graph_path(record)
    # A pure graph fixture may not carry the catalogue index.  Preserve an
    # already hierarchical source URL, but never pass an old flat URL through.
    return node["url"] if _PODCAST_GRAPH_PATH.fullmatch(node["url"]) else ""


def _episode_graph_nodes(
    graph: dict[str, Any], *, podcast_records: dict[str, Any]
) -> dict[str, dict[str, str]]:
    raw_nodes = graph.get("nodes")
    if not isinstance(raw_nodes, (list, tuple)):
        raise ImproperlyConfigured("The wiki graph node collection is invalid.")
    nodes: dict[str, dict[str, str]] = {}
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise ImproperlyConfigured("The wiki graph contains a malformed node.")
        node_id = raw_node.get("id")
        node_title = raw_node.get("title")
        node_label = raw_node.get("label", node_title)
        node_type = raw_node.get("type")
        if (
            not isinstance(node_id, str)
            or not node_id
            or node_id in nodes
            or not isinstance(node_title, str)
            or not node_title
            or not isinstance(node_label, str)
            or not node_label
            or not isinstance(node_type, str)
            or not node_type
        ):
            raise ImproperlyConfigured("The wiki graph contains an invalid node contract.")
        source_url = safe_public_graph_url(raw_node.get("url", ""))
        node = {
            "id": node_id,
            "label": node_label,
            "title": node_title,
            "type": node_type,
            "source_url": source_url,
            "url": source_url,
        }
        if node_type == "podcast":
            node["url"] = _podcast_graph_url(node, podcast_records=podcast_records)
        nodes[node_id] = node
    return nodes


def _episode_graph_links(
    graph: dict[str, Any], nodes: dict[str, dict[str, str]]
) -> tuple[dict, ...]:
    raw_links = graph.get("links")
    if not isinstance(raw_links, (list, tuple)):
        raise ImproperlyConfigured("The wiki graph link collection is invalid.")
    links: list[dict] = []
    for link in raw_links:
        if not isinstance(link, dict):
            raise ImproperlyConfigured("The wiki graph contains a malformed link.")
        source = link.get("source")
        target = link.get("target")
        kind = link.get("kind")
        weight = link.get("weight")
        if (
            not isinstance(source, str)
            or not source
            or not isinstance(target, str)
            or not target
            or source not in nodes
            or target not in nodes
            or not isinstance(kind, str)
            or not kind
            or isinstance(weight, bool)
            or not isinstance(weight, int)
            or weight < 1
        ):
            raise ImproperlyConfigured("The wiki graph contains an invalid link contract.")
        links.append({"source": source, "target": target, "kind": kind, "weight": weight})
    return tuple(links)


def _episode_graph_shell(episode: dict[str, Any], *, state: str) -> EpisodeGraph:
    title = episode.get("title")
    public_path = episode.get("public_path")
    if (
        not isinstance(title, str)
        or not title
        or not isinstance(public_path, str)
        or not public_path
        or safe_public_graph_url(public_path) != public_path
    ):
        raise ImproperlyConfigured("The podcast episode identity is invalid.")
    # ``public_path`` is the request/canonical identity owned by the route
    # layer.  The graph has its own output boundary: if the catalogue record
    # has enough stable identity to build the hierarchical route, use it;
    # otherwise preserve only an already-hierarchical fixture path.  A flat
    # podcast path is never exposed through this graph contract.
    url = _hierarchical_podcast_graph_path(episode)
    if not url and _PODCAST_GRAPH_PATH.fullmatch(public_path):
        url = public_path
    return EpisodeGraph(
        state=state,
        title=title,
        url=url,
        hub_id="",
        neighbors=(),
        visual_neighbors=(),
        raw_links=0,
        unique_neighbors=0,
        layouts=(),
    )


def _episode_graph_narrow_hub_title(episode: dict[str, Any]) -> str:
    """Use the exact compact episode identity when the full title cannot fit."""

    season = episode.get("season")
    number = episode.get("episode")
    if (
        isinstance(season, int)
        and not isinstance(season, bool)
        and season > 0
        and isinstance(number, int)
        and not isinstance(number, bool)
        and number > 0
    ):
        return f"S{season}E{number:02d}"
    return str(episode["title"])


def unavailable_episode_graph(episode: dict[str, Any]) -> EpisodeGraph:
    """Return the safe request-time state for a known graph contract failure."""

    try:
        return _episode_graph_shell(episode, state="unavailable")
    except ImproperlyConfigured:
        return EpisodeGraph(
            state="unavailable",
            title="This episode",
            url="",
            hub_id="",
            neighbors=(),
            visual_neighbors=(),
            raw_links=0,
            unique_neighbors=0,
            layouts=(),
        )


def episode_graph(
    episode: dict[str, Any],
    *,
    projection: dict[str, Any] | None = None,
) -> EpisodeGraph:
    """Resolve one episode against its exact typed public graph node.

    Resolution is deliberately path-and-type based.  Titles, season numbers,
    transcripts and source identities are not graph identity.  The graph is
    read from the already checked public projection; no request-time source or
    external graph service is consulted.
    """

    graph = catalogue.wiki_graph() if projection is None else projection.get("wiki_graph")
    if not isinstance(graph, dict):
        raise ImproperlyConfigured("The published catalogue has no wiki graph.")
    shell = _episode_graph_shell(episode, state="no_data")
    episode_public_path = episode["public_path"]
    podcast_records = (
        {item["slug"]: item for item in catalogue.podcasts() if "slug" in item}
        if projection is None
        else projection.get("podcasts_by_slug", {})
    )
    if not isinstance(podcast_records, dict):
        podcast_records = {}
    nodes = _episode_graph_nodes(graph, podcast_records=podcast_records)
    links = _episode_graph_links(graph, nodes)
    episode_slug = episode.get("slug")
    episode_node_id = (
        f"podcast:{episode_slug}" if isinstance(episode_slug, str) and episode_slug else ""
    )
    matches = tuple(
        node
        for node in nodes.values()
        if node["type"] == "podcast"
        and (node["id"] == episode_node_id or node["source_url"] == episode_public_path)
    )
    if not matches:
        return shell
    if len(matches) != 1:
        raise ImproperlyConfigured("The public graph has duplicate episode identities.")
    hub = matches[0]
    aggregate: dict[str, dict[str, Any]] = {}
    raw_incident_links = 0
    for link in links:
        if link["source"] != hub["id"] and link["target"] != hub["id"]:
            continue
        if link["source"] == link["target"]:
            continue
        neighbour_id = link["target"] if link["source"] == hub["id"] else link["source"]
        raw_incident_links += 1
        entry = aggregate.setdefault(
            neighbour_id,
            {"weight": 0, "raw_links": 0, "relation_kinds": set()},
        )
        entry["weight"] += link["weight"]
        entry["raw_links"] += 1
        entry["relation_kinds"].add(link["kind"])
    if not aggregate:
        return shell

    neighbours = tuple(
        EpisodeGraphNeighbour(
            id=node_id,
            label=nodes[node_id]["label"],
            title=nodes[node_id]["title"],
            type=nodes[node_id]["type"],
            url=nodes[node_id]["url"],
            relation_kinds=tuple(sorted(entry["relation_kinds"])),
            weight=entry["weight"],
            raw_links=entry["raw_links"],
        )
        for node_id, entry in sorted(
            aggregate.items(),
            key=lambda item: (
                -item[1]["weight"],
                nodes[item[0]]["label"],
                item[0],
            ),
        )
    )
    visual_neighbours = neighbours[:RING_SPOKES]
    layouts: tuple[GraphLayout, ...] = ()
    if len(visual_neighbours) == RING_SPOKES:
        layouts = ring_layouts(
            GraphPoint(title=hub["label"], url=hub["url"]),
            tuple(
                GraphPoint(title=neighbour.label, url=neighbour.url)
                for neighbour in visual_neighbours
            ),
            wide_wrap_over=WIDE_WRAP_OVER,
            narrow_hub=GraphPoint(
                title=_episode_graph_narrow_hub_title(episode),
                url=hub["url"],
            ),
        )
    return EpisodeGraph(
        state="available",
        title=hub["label"],
        url=hub["url"],
        hub_id=hub["id"],
        neighbors=neighbours,
        visual_neighbors=visual_neighbours,
        raw_links=raw_incident_links,
        unique_neighbors=len(neighbours),
        layouts=layouts,
    )


def podcast_episode_graph(
    episode: dict[str, Any],
    *,
    projection: dict[str, Any] | None = None,
) -> EpisodeGraph:
    """Descriptive alias for the episode graph resolver used by extensions."""

    return episode_graph(episode, projection=projection)


def _graph() -> dict[str, Any]:
    return catalogue.wiki_graph()


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
