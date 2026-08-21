"""Ring geometry for the drawn knowledge graph.

Two surfaces draw the wiki as a hub with a ring of neighbours around it: the
homepage's "The wiki, as a graph" band and ``/wiki/graph``.  Both draw it the
same way — the whole drawing, nodes, labels and edges, is **one SVG with a
viewBox**, so it scales as a unit and cannot overflow a narrow screen — and both
compute that drawing here rather than each inventing its own.

Only geometry lives in this module.  Which node sits where is decided by the
caller from the graph's own data; a title, a destination or an edge this module
receives is drawn exactly as given and never invented.

Two arrangements of the same nodes are produced for every drawing: a landscape
ring for wide screens and a portrait ring for phones, where a long label wraps
to two lines so every pill stays inside a 320-unit frame.  The page shows one
and hides the other with a media query.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Advance widths (em, at weight 600) measured from the shipped Quicksand
# variable font, so a pill computed here is as wide as the label the browser
# draws.  Characters outside the table fall back to a generous width.
QUICKSAND_ADVANCES_EM = {
    "a": 0.621,
    "b": 0.621,
    "c": 0.519,
    "d": 0.621,
    "e": 0.582,
    "f": 0.411,
    "g": 0.633,
    "h": 0.58,
    "i": 0.229,
    "j": 0.273,
    "k": 0.558,
    "l": 0.257,
    "m": 0.919,
    "n": 0.59,
    "o": 0.617,
    "p": 0.621,
    "q": 0.621,
    "r": 0.418,
    "s": 0.478,
    "t": 0.386,
    "u": 0.581,
    "v": 0.551,
    "w": 0.763,
    "x": 0.486,
    "y": 0.581,
    "z": 0.482,
    "A": 0.642,
    "B": 0.654,
    "C": 0.636,
    "D": 0.716,
    "E": 0.571,
    "F": 0.563,
    "G": 0.696,
    "H": 0.719,
    "I": 0.267,
    "J": 0.559,
    "K": 0.662,
    "L": 0.552,
    "M": 0.822,
    "N": 0.733,
    "O": 0.761,
    "P": 0.601,
    "Q": 0.762,
    "R": 0.681,
    "S": 0.585,
    "T": 0.619,
    "U": 0.708,
    "V": 0.674,
    "W": 0.951,
    "X": 0.639,
    "Y": 0.584,
    "Z": 0.647,
    "0": 0.608,
    "1": 0.383,
    "2": 0.564,
    "3": 0.533,
    "4": 0.568,
    "5": 0.566,
    "6": 0.548,
    "7": 0.537,
    "8": 0.545,
    "9": 0.565,
    " ": 0.278,
    "-": 0.394,
    "&": 0.699,
    ".": 0.226,
    "/": 0.553,
}
FALLBACK_ADVANCE_EM = 0.95

SPOKE_FONT = 14.0
HUB_FONT = 17.0
LABEL_PAD = 13.0
HUB_PAD = 16.0
LINE_HEIGHT = 16.5
SINGLE_LINE_HEIGHT = 32.0
# What each line after the first adds to a pill's box.
EXTRA_LINE_HEIGHT = 15.0
HUB_HEIGHT = 38.0
MARGIN = 8.0
# The optical vertical centre of a Quicksand line sits about a third of an em
# below the baseline anchor, so text y = centre + this shift.
BASELINE_SHIFT_EM = 0.35

# Spoke centres, clockwise.  The wide frame starts three-across on top (the
# desktop mockup's landscape spread); the narrow frame is the same ring turned
# portrait: one node on top, three down each side, one on the bottom.
WIDE_FRAME = (640, 400)
WIDE_HUB = (320.0, 200.0)
WIDE_POSITIONS = (
    (115.0, 76.0),
    (320.0, 36.0),
    (525.0, 76.0),
    (582.0, 200.0),
    (525.0, 324.0),
    (320.0, 364.0),
    (115.0, 324.0),
    (58.0, 200.0),
)
NARROW_FRAME = (320, 408)
NARROW_HUB = (160.0, 212.0)
NARROW_POSITIONS = (
    (160.0, 58.0),
    (232.0, 110.0),
    (261.0, 212.0),
    (232.0, 314.0),
    (160.0, 366.0),
    (88.0, 314.0),
    (59.0, 212.0),
    (88.0, 110.0),
)
# Pills wider than this (in frame units) wrap their label to two lines; it is
# the widest pill that still clears the hub from the ring's side positions.
NARROW_WRAP_OVER = 104.0
# The wide frame has room for a longer label, so it wraps later.  The homepage
# hand-picks eight short titles and passes ``None`` (never wrap); a page that
# draws whatever the data makes busiest asks for this bound instead.
WIDE_WRAP_OVER = 168.0

# How many neighbours a ring holds.  Both arrangements draw the same nodes.
RING_SPOKES = len(WIDE_POSITIONS)


@dataclass(frozen=True, slots=True)
class GraphPoint:
    """One thing the drawing names: what it is called and where it leads."""

    title: str
    url: str


@dataclass(frozen=True, slots=True)
class GraphLabelLine:
    """One rendered line of a node label: the text and its SVG baseline y."""

    text: str
    y: float


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One pill in the drawing: centre, box, and pre-broken label lines."""

    title: str
    url: str
    x: float
    y: float
    width: float
    height: float
    font_size: float
    lines: tuple[GraphLabelLine, ...]

    @property
    def left(self) -> float:
        return round(self.x - self.width / 2, 1)

    @property
    def top(self) -> float:
        return round(self.y - self.height / 2, 1)

    @property
    def radius(self) -> float:
        return round(self.height / 2, 1)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One drawn relation, from the hub centre to a spoke centre.

    Edges are painted before the pills, so each line visually terminates at
    the pill's border rather than crossing its label.
    """

    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class GraphLayout:
    """One complete arrangement of the drawing inside an SVG viewBox."""

    kind: str
    width: int
    height: int
    hub: GraphNode
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


def label_em(text: str) -> float:
    """The width of a label in ems, from the shipped font's own advances."""

    return sum(QUICKSAND_ADVANCES_EM.get(char, FALLBACK_ADVANCE_EM) for char in text)


def balanced_lines(title: str) -> tuple[str, ...]:
    """Break a multi-word title into the two most balanced lines."""

    words = title.split()
    if len(words) < 2:
        return (title,)
    breaks = ((" ".join(words[:index]), " ".join(words[index:])) for index in range(1, len(words)))
    return min(breaks, key=lambda lines: max(label_em(line) for line in lines))


def wrap_label(title: str, *, limit_em: float) -> tuple[str, ...]:
    """Break a title into the fewest lines that each stay inside ``limit_em``.

    A title too long for its pill is wrapped rather than allowed to widen the
    pill into its neighbours: the hub a page draws is whatever the data makes
    busiest, and "MLOps vs DevOps Practices" on one line reaches the middle of
    the ring.  Two lines are then rebalanced, because an even pair reads better
    than a full line above a short one, and a word wider than the limit keeps
    its own line rather than being cut.
    """

    words = title.split()
    if len(words) < 2:
        return (title,)
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if label_em(candidate) <= limit_em:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    if len(lines) == 2:
        return balanced_lines(title)
    return tuple(lines)


def graph_node(
    point: GraphPoint,
    x: float,
    y: float,
    frame: tuple[int, int],
    *,
    font: float,
    pad: float,
    height: float,
    wrap_over: float | None,
) -> GraphNode:
    lines: tuple[str, ...] = (point.title,)
    if wrap_over is not None and label_em(point.title) * font + 2 * pad > wrap_over:
        lines = wrap_label(point.title, limit_em=max(wrap_over - 2 * pad, 0.0) / font)
    width = max(label_em(line) for line in lines) * font + 2 * pad
    box_height = height + (len(lines) - 1) * EXTRA_LINE_HEIGHT
    # Clamp the centre so the whole pill stays inside the frame with a margin,
    # whatever the projection titles grow into.
    x = min(max(x, MARGIN + width / 2), frame[0] - MARGIN - width / 2)
    y = min(max(y, MARGIN + box_height / 2), frame[1] - MARGIN - box_height / 2)
    first_baseline = y - (len(lines) - 1) / 2 * LINE_HEIGHT + BASELINE_SHIFT_EM * font
    return GraphNode(
        title=point.title,
        url=point.url,
        x=round(x, 1),
        y=round(y, 1),
        width=round(width, 1),
        height=box_height,
        font_size=font,
        lines=tuple(
            GraphLabelLine(text=line, y=round(first_baseline + index * LINE_HEIGHT, 1))
            for index, line in enumerate(lines)
        ),
    )


def graph_layout(
    kind: str,
    frame: tuple[int, int],
    hub_center: tuple[float, float],
    positions: tuple[tuple[float, float], ...],
    hub: GraphPoint,
    spokes: Sequence[GraphPoint],
    *,
    wrap_over: float | None,
) -> GraphLayout:
    hub_node = graph_node(
        hub,
        hub_center[0],
        hub_center[1],
        frame,
        font=HUB_FONT,
        pad=HUB_PAD,
        height=HUB_HEIGHT,
        wrap_over=wrap_over,
    )
    nodes = tuple(
        graph_node(
            point,
            x,
            y,
            frame,
            font=SPOKE_FONT,
            pad=LABEL_PAD,
            height=SINGLE_LINE_HEIGHT,
            wrap_over=wrap_over,
        )
        for point, (x, y) in zip(spokes, positions, strict=True)
    )
    edges = tuple(GraphEdge(x1=hub_node.x, y1=hub_node.y, x2=node.x, y2=node.y) for node in nodes)
    return GraphLayout(
        kind=kind,
        width=frame[0],
        height=frame[1],
        hub=hub_node,
        nodes=nodes,
        edges=edges,
    )


def ring_layouts(
    hub: GraphPoint,
    spokes: Sequence[GraphPoint],
    *,
    wide_wrap_over: float | None = None,
    narrow_hub: GraphPoint | None = None,
) -> tuple[GraphLayout, ...]:
    """Arrange one hub and its ring of spokes for a wide and a narrow screen.

    A caller may provide a compact narrow hub when the full hub label is a
    page-length title.  The default keeps the existing wiki/homepage geometry
    byte-for-byte equivalent; episode pages use the episode identity in this
    optional slot so the established narrow ring remains legible.
    """

    return (
        graph_layout(
            "wide",
            WIDE_FRAME,
            WIDE_HUB,
            WIDE_POSITIONS,
            hub,
            spokes,
            wrap_over=wide_wrap_over,
        ),
        graph_layout(
            "narrow",
            NARROW_FRAME,
            NARROW_HUB,
            NARROW_POSITIONS,
            narrow_hub or hub,
            spokes,
            wrap_over=NARROW_WRAP_OVER,
        ),
    )
