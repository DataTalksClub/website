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
# Percentage coordinates inside the square graph frame, clockwise from the top left.
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
class WikiGraphNode:
    topic: WikiTopic
    left: float
    top: float


@dataclass(frozen=True, slots=True)
class WikiGraph:
    hub: WikiTopic
    nodes: tuple[WikiGraphNode, ...]
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


def wiki_graph() -> WikiGraph:
    """Draw the hub and its real wiki relations, never an invented edge."""

    pages = _wiki_pages()
    hub_page = pages.get(WIKI_GRAPH_HUB)
    if hub_page is None:
        raise ImproperlyConfigured(f"Public wiki projection has no {WIKI_GRAPH_HUB} page.")
    related = {
        str(relation["href"]) for relation in hub_page["relations"] if relation["type"] == "wiki"
    }
    nodes: list[WikiGraphNode] = []
    for slug, (left, top) in zip(WIKI_GRAPH_SPOKES, WIKI_GRAPH_POSITIONS, strict=True):
        topic = _wiki_topic(pages, slug)
        if topic.public_path not in related:
            raise ImproperlyConfigured(f"{WIKI_GRAPH_HUB} does not link to {slug}.")
        nodes.append(WikiGraphNode(topic=topic, left=left, top=top))
    return WikiGraph(
        hub=_wiki_topic(pages, WIKI_GRAPH_HUB),
        nodes=tuple(nodes),
        connections=len(related),
    )
