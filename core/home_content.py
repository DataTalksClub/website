"""Editorial composition for the redesigned public homepage.

The homepage renders the "5a" mockup from DataTalksClub/website#179.  Every fact on the
page (titles, paths, cohort dates, counts, relations) is read from the checked public
projection; only the short section copy and the per-course promise lines are editorial
constants, and they live here so a copy change never needs a template edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured

from content.public_data import public_projection

FEATURED_FAMILY = "ai-dev-tools"

# Family slug prefix, catalogue title, short chip label, and the promise the course makes.
COURSE_FAMILIES: tuple[tuple[str, str, str, str], ...] = (
    (
        "ai-dev-tools",
        "AI Dev Tools Zoomcamp",
        "AI Dev Tools",
        "a deployed full-stack app built with AI assistance",
    ),
    (
        "de-zoomcamp",
        "Data Engineering Zoomcamp",
        "Data Engineering",
        "a batch and streaming pipeline that runs on a schedule",
    ),
    (
        "llm-zoomcamp",
        "LLM Zoomcamp",
        "LLMs",
        "a RAG assistant over your own documents",
    ),
    (
        "ml-zoomcamp",
        "Machine Learning Zoomcamp",
        "Machine Learning",
        "trained models your classmates review",
    ),
    (
        "mlops-zoomcamp",
        "MLOps Zoomcamp",
        "MLOps",
        "a deployed model with monitoring around it",
    ),
    (
        "sma-zoomcamp",
        "Stock Markets Analytics Zoomcamp",
        "Stock Markets",
        "a trading strategy you backtest yourself",
    ),
)

# What the featured cohort's mint panel promises you will build, and the note under it.
FEATURED_BUILD_ITEMS: tuple[str, ...] = (
    "multi-agent system that researches and writes",
    "RAG evaluation with dashboards",
    "agents with memory and tools",
    "deployment with monitoring",
)
FEATURED_GROUP_NOTE = "small groups of 6–8 people"

# The wiki hub the graph is drawn around, and the direct relations it is drawn to.  Every
# slug is validated against the projection so a source change fails loudly instead of
# rendering an edge that does not exist.
WIKI_GRAPH_HUB = "mlops"
WIKI_GRAPH_SPOKES = (
    "feature-stores",
    "model-monitoring",
    "mlops-tools",
    "model-registry",
    "experiment-tracking",
    "ml-platforms",
    "dataops",
    "mlops-roadmap",
)

# Percentage coordinates of the plotted-pill ring, clockwise from the top left.
# The homepage now draws its graph as a scalable SVG (geometry below), but
# /wiki/graph still plots its neighbourhood with positioned pills on this ring
# (content/wiki_content.py imports it: one ring, one definition).
WIKI_GRAPH_POSITIONS = (
    (18.0, 19.0),
    (50.0, 9.0),
    (82.0, 19.0),
    (91.0, 50.0),
    (82.0, 81.0),
    (50.0, 91.0),
    (18.0, 81.0),
    (9.0, 50.0),
)

# ---------------------------------------------------------------------------
# Wiki graph geometry.  The homepage draws the hub and its spokes as one SVG
# (nodes, labels and edges share a viewBox), so the drawing scales as a unit
# and cannot overflow a narrow screen.  Two arrangements of the same validated
# data are computed here: a landscape ring for wide screens and a portrait
# ring for phones, where multi-word labels wrap to two lines so every pill
# stays inside a 320-unit frame.  Only geometry lives here — every title,
# path and edge still comes from the checked projection.
# ---------------------------------------------------------------------------

# Advance widths (em, at weight 600) measured from the shipped Quicksand
# variable font, so a pill computed here is as wide as the label the browser
# draws.  Characters outside the table fall back to a generous width.
_QUICKSAND_ADVANCES_EM = {
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
_FALLBACK_ADVANCE_EM = 0.95

_GRAPH_SPOKE_FONT = 14.0
_GRAPH_HUB_FONT = 17.0
_GRAPH_LABEL_PAD = 13.0
_GRAPH_HUB_PAD = 16.0
_GRAPH_LINE_HEIGHT = 16.5
_GRAPH_SINGLE_LINE_HEIGHT = 32.0
_GRAPH_TWO_LINE_HEIGHT = 47.0
_GRAPH_HUB_HEIGHT = 38.0
_GRAPH_MARGIN = 8.0
# The optical vertical centre of a Quicksand line sits about a third of an em
# below the baseline anchor, so text y = centre + this shift.
_GRAPH_BASELINE_SHIFT_EM = 0.35

# Spoke centres, clockwise.  The wide frame starts three-across on top (the
# desktop mockup's landscape spread); the narrow frame is the same ring turned
# portrait: one node on top, three down each side, one on the bottom.
_WIDE_FRAME = (640, 400)
_WIDE_HUB = (320.0, 200.0)
_WIDE_POSITIONS = (
    (115.0, 76.0),
    (320.0, 36.0),
    (525.0, 76.0),
    (582.0, 200.0),
    (525.0, 324.0),
    (320.0, 364.0),
    (115.0, 324.0),
    (58.0, 200.0),
)
_NARROW_FRAME = (320, 408)
_NARROW_HUB = (160.0, 212.0)
_NARROW_POSITIONS = (
    (160.0, 58.0),
    (232.0, 110.0),
    (261.0, 212.0),
    (232.0, 314.0),
    (160.0, 366.0),
    (88.0, 314.0),
    (59.0, 212.0),
    (88.0, 110.0),
)
# Pills wider than this (in frame units) wrap their label to two lines in the
# narrow frame; it is the widest pill that still clears the hub from the ring's
# side positions.  The wide frame never wraps.
_NARROW_WRAP_OVER = 104.0
WIKI_TOPICS = (
    "retrieval-augmented-generation",
    "mlops",
    "data-engineering",
    "vector-databases",
    "feature-stores",
    "experimentation",
)


@dataclass(frozen=True, slots=True)
class CatalogCourse:
    """One zoomcamp family, represented by its most recent cohort."""

    family: str
    slug: str
    title: str
    label: str
    promise: str
    public_path: str
    cohort_label: str
    homework_count: int
    project_count: int


@dataclass(frozen=True, slots=True)
class MemberStory:
    """One "people who were exactly where you are" card."""

    before: str
    after: str
    quote: str
    name: str
    context: str
    elapsed: str


# PLACEHOLDER COPY.  These three quotes come from the #179 mockup, not from named members
# who have agreed to be quoted.  Replace them with real, attributable stories (or drop the
# entries, which hides the section) before this page is served from datatalks.club.
MEMBER_STORIES: tuple[MemberStory, ...] = (
    MemberStory(
        before="QA analyst",
        after="Data engineer",
        quote=(
            "I finished the pipeline homework, put it on GitHub, and used it as my whole "
            "portfolio. Three interviews later I had the job — and my current stack is the "
            "one from the course."
        ),
        name="Tolu A.",
        context="Lagos · DE Zoomcamp",
        elapsed="11 months",
    ),
    MemberStory(
        before="Excel reports",
        after="ML in prod",
        quote=(
            "Deploying a model terrified me. The MLOps homework made me do it eight times, "
            "and the Slack caught every mistake before my team ever saw it."
        ),
        name="Marta S.",
        context="Kraków · MLOps Zoomcamp",
        elapsed="7 months",
    ),
    MemberStory(
        before="Backend dev",
        after="Shipping agents",
        quote=(
            "I built an agent that survives restarts, and now I run the AI features at my "
            "company. Two years ago I was the person asking what RAG meant."
        ),
        name="Nikhil R.",
        context="Bengaluru · LLM Zoomcamp",
        elapsed="2 years",
    ),
)


@dataclass(frozen=True, slots=True)
class WikiTopic:
    slug: str
    title: str
    summary: str
    public_path: str


@dataclass(frozen=True, slots=True)
class WikiGraphLabelLine:
    """One rendered line of a node label: the text and its SVG baseline y."""

    text: str
    y: float


@dataclass(frozen=True, slots=True)
class WikiGraphNode:
    """One pill in the graph drawing: centre, box, and pre-broken label lines."""

    topic: WikiTopic
    x: float
    y: float
    width: float
    height: float
    font_size: float
    lines: tuple[WikiGraphLabelLine, ...]

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
class WikiGraphEdge:
    """One drawn relation, from the hub centre to a spoke centre.

    Edges are painted before the pills, so each line visually terminates at
    the pill's border rather than crossing its label.
    """

    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class WikiGraphLayout:
    """One complete arrangement of the graph inside an SVG viewBox."""

    kind: str
    width: int
    height: int
    hub: WikiGraphNode
    nodes: tuple[WikiGraphNode, ...]
    edges: tuple[WikiGraphEdge, ...]


@dataclass(frozen=True, slots=True)
class WikiGraph:
    hub: WikiTopic
    spokes: tuple[WikiTopic, ...]
    layouts: tuple[WikiGraphLayout, ...]
    connections: int


def _latest_cohort_records(
    records: tuple[dict[str, Any], ...],
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Return the newest cohort record for every ``<family>-<year>`` course slug."""

    latest: dict[str, tuple[str, dict[str, Any]]] = {}
    for record in records:
        family, _, year = str(record["slug"]).rpartition("-")
        if not family or not year.isdigit():
            continue
        current = latest.get(family)
        if current is None or year > current[0]:
            latest[family] = (year, record)
    return latest


def course_catalog() -> tuple[CatalogCourse, ...]:
    """Return one card per zoomcamp family, newest cohort first within each family."""

    latest = _latest_cohort_records(tuple(public_projection()["courses"]))
    catalog: list[CatalogCourse] = []
    for family, title, label, promise in COURSE_FAMILIES:
        found = latest.get(family)
        if found is None:
            raise ImproperlyConfigured(f"Public course projection has no {family} cohort.")
        year, record = found
        catalog.append(
            CatalogCourse(
                family=family,
                slug=str(record["slug"]),
                title=title,
                label=label,
                promise=promise,
                public_path=str(record["public_path"]),
                cohort_label=f"{year} cohort",
                homework_count=int(record["homework_count"]),
                project_count=int(record["project_count"]),
            )
        )
    return tuple(catalog)


_SPELLED = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
)


def spelled_count(value: int) -> str:
    """Spell a small count the way the design writes it, and fall back to digits."""

    if 0 <= value < len(_SPELLED):
        return _SPELLED[value]
    return str(value)


def event_time_display(starts_at: str) -> str:
    """Render an event start the way the design writes it: weekday, date, then time.

    The shared ``display_time`` on the projection is what the events hub and the event
    pages render; the homepage wants the weekday too, so it formats its own without
    changing those surfaces.
    """

    local = datetime.fromisoformat(starts_at).astimezone(ZoneInfo("Europe/Berlin"))
    return f"{local:%a}, {local:%b} {local.day}, {local:%Y} \u00b7 {local:%H:%M %Z}"


# A comfortable prose reading pace; the design shows the result as "N min read".
READING_WORDS_PER_MINUTE = 200


def reading_minutes(article: dict[str, Any]) -> int:
    """Estimate an article's reading time from its own projected text blocks."""

    words = 0
    for block in article.get("blocks", ()):
        for key in ("text", "caption", "quote"):
            value = block.get(key)
            if isinstance(value, str):
                words += len(value.split())
    return max(1, round(words / READING_WORDS_PER_MINUTE))


def published_display(value: str) -> str:
    """Render a projection ``YYYY-MM-DD`` publication date the way the design shows it."""

    published = date.fromisoformat(value)
    return f"{published:%b} {published.day}, {published:%Y}"


def _wiki_pages() -> dict[str, dict[str, Any]]:
    return {str(page["slug"]): page for page in public_projection()["wiki"]}


def _wiki_topic(pages: dict[str, dict[str, Any]], slug: str) -> WikiTopic:
    page = pages.get(slug)
    if page is None:
        raise ImproperlyConfigured(f"Public wiki projection has no {slug} page.")
    return WikiTopic(
        slug=slug,
        title=str(page["title"]),
        summary=str(page["summary"]),
        public_path=str(page["public_path"]),
    )


def wiki_topics() -> tuple[WikiTopic, ...]:
    pages = _wiki_pages()
    return tuple(_wiki_topic(pages, slug) for slug in WIKI_TOPICS)


def _label_em(text: str) -> float:
    """The width of a label in ems, from the shipped font's own advances."""

    return sum(_QUICKSAND_ADVANCES_EM.get(char, _FALLBACK_ADVANCE_EM) for char in text)


def _wrap_label(title: str) -> tuple[str, ...]:
    """Break a multi-word title into the two most balanced lines."""

    words = title.split()
    if len(words) < 2:
        return (title,)
    breaks = ((" ".join(words[:index]), " ".join(words[index:])) for index in range(1, len(words)))
    return min(breaks, key=lambda lines: max(_label_em(line) for line in lines))


def _graph_node(
    topic: WikiTopic,
    x: float,
    y: float,
    frame: tuple[int, int],
    *,
    font: float,
    pad: float,
    height: float,
    wrap_over: float | None,
) -> WikiGraphNode:
    lines: tuple[str, ...] = (topic.title,)
    if wrap_over is not None and _label_em(topic.title) * font + 2 * pad > wrap_over:
        lines = _wrap_label(topic.title)
    width = max(_label_em(line) for line in lines) * font + 2 * pad
    box_height = height if len(lines) == 1 else _GRAPH_TWO_LINE_HEIGHT
    # Clamp the centre so the whole pill stays inside the frame with a margin,
    # whatever the projection titles grow into.
    x = min(max(x, _GRAPH_MARGIN + width / 2), frame[0] - _GRAPH_MARGIN - width / 2)
    y = min(max(y, _GRAPH_MARGIN + box_height / 2), frame[1] - _GRAPH_MARGIN - box_height / 2)
    first_baseline = y - (len(lines) - 1) / 2 * _GRAPH_LINE_HEIGHT + _GRAPH_BASELINE_SHIFT_EM * font
    return WikiGraphNode(
        topic=topic,
        x=round(x, 1),
        y=round(y, 1),
        width=round(width, 1),
        height=box_height,
        font_size=font,
        lines=tuple(
            WikiGraphLabelLine(text=line, y=round(first_baseline + index * _GRAPH_LINE_HEIGHT, 1))
            for index, line in enumerate(lines)
        ),
    )


def _graph_layout(
    kind: str,
    frame: tuple[int, int],
    hub_center: tuple[float, float],
    positions: tuple[tuple[float, float], ...],
    hub: WikiTopic,
    spokes: tuple[WikiTopic, ...],
    *,
    wrap_over: float | None,
) -> WikiGraphLayout:
    hub_node = _graph_node(
        hub,
        hub_center[0],
        hub_center[1],
        frame,
        font=_GRAPH_HUB_FONT,
        pad=_GRAPH_HUB_PAD,
        height=_GRAPH_HUB_HEIGHT,
        wrap_over=None,
    )
    nodes = tuple(
        _graph_node(
            topic,
            x,
            y,
            frame,
            font=_GRAPH_SPOKE_FONT,
            pad=_GRAPH_LABEL_PAD,
            height=_GRAPH_SINGLE_LINE_HEIGHT,
            wrap_over=wrap_over,
        )
        for topic, (x, y) in zip(spokes, positions, strict=True)
    )
    edges = tuple(
        WikiGraphEdge(x1=hub_node.x, y1=hub_node.y, x2=node.x, y2=node.y) for node in nodes
    )
    return WikiGraphLayout(
        kind=kind,
        width=frame[0],
        height=frame[1],
        hub=hub_node,
        nodes=nodes,
        edges=edges,
    )


def wiki_graph() -> WikiGraph:
    """Draw the hub and its real wiki relations, never an invented edge."""

    pages = _wiki_pages()
    hub_page = pages.get(WIKI_GRAPH_HUB)
    if hub_page is None:
        raise ImproperlyConfigured(f"Public wiki projection has no {WIKI_GRAPH_HUB} page.")
    related = {
        str(relation["href"]) for relation in hub_page["relations"] if relation["type"] == "wiki"
    }
    spokes: list[WikiTopic] = []
    for slug in WIKI_GRAPH_SPOKES:
        topic = _wiki_topic(pages, slug)
        if topic.public_path not in related:
            raise ImproperlyConfigured(f"{WIKI_GRAPH_HUB} does not link to {slug}.")
        spokes.append(topic)
    hub = _wiki_topic(pages, WIKI_GRAPH_HUB)
    return WikiGraph(
        hub=hub,
        spokes=tuple(spokes),
        layouts=(
            _graph_layout(
                "wide",
                _WIDE_FRAME,
                _WIDE_HUB,
                _WIDE_POSITIONS,
                hub,
                tuple(spokes),
                wrap_over=None,
            ),
            _graph_layout(
                "narrow",
                _NARROW_FRAME,
                _NARROW_HUB,
                _NARROW_POSITIONS,
                hub,
                tuple(spokes),
                wrap_over=_NARROW_WRAP_OVER,
            ),
        ),
        connections=len(related),
    )
