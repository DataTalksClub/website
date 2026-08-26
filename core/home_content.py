"""Editorial composition for the redesigned public homepage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured

from content.public_data import public_projection
from core.graph_layout import GraphLayout, GraphPoint, ring_layouts

FEATURED_FAMILY = "ai-dev-tools"

# Family slug prefix, catalogue title, and short chip label.
COURSE_FAMILIES: tuple[tuple[str, str, str], ...] = (
    (
        "ai-dev-tools",
        "AI Dev Tools Zoomcamp",
        "AI Dev Tools",
    ),
    (
        "de-zoomcamp",
        "Data Engineering Zoomcamp",
        "Data Engineering",
    ),
    (
        "llm-zoomcamp",
        "LLM Zoomcamp",
        "LLMs",
    ),
    (
        "ml-zoomcamp",
        "Machine Learning Zoomcamp",
        "Machine Learning",
    ),
    (
        "mlops-zoomcamp",
        "MLOps Zoomcamp",
        "MLOps",
    ),
    (
        "sma-zoomcamp",
        "Stock Markets Analytics Zoomcamp",
        "Stock Markets",
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

# The homepage draws the hub and its spokes as one SVG per width; the ring
# geometry it draws them on is shared with /wiki/graph and lives in
# `core.graph_layout`.  What is decided here is only which topics are drawn.

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
    public_path: str
    cohort_label: str
    homework_count: int
    project_count: int


@dataclass(frozen=True, slots=True)
class MemberStory:
    """One "people who were exactly where you are" card.

    ``before``, ``after``, and ``elapsed`` are optional: not every real testimonial
    states a clean role transition or an elapsed time, and the template must not
    invent one. When ``before``/``after`` are absent the transition chip pair is
    skipped entirely (never a lone "after" chip); when ``elapsed`` is absent the
    elapsed pill is skipped.

    ``source_url`` links the person's name back to the original public post the
    quote was taken from, so the attribution is checkable rather than asserted.
    """

    quote: str
    name: str
    context: str
    before: str | None = None
    after: str | None = None
    elapsed: str | None = None
    source_url: str | None = None


# REAL, SOURCED TESTIMONIALS (issue #179).  These three quotes are taken verbatim from
# public posts by named members (e.g. LinkedIn) and independently verified via direct
# link, replacing the earlier mockup placeholder copy.  None of them states a
# before/after role transition or an elapsed time, so those fields are intentionally
# left unset rather than invented — see MemberStory above for how the template handles
# that.
#
# IMPORTANT: these individuals have NOT been contacted for homepage-specific consent.
# They posted this content publicly elsewhere, but that is not the same as agreeing to
# be quoted on datatalks.club's homepage. Do not ship this to production until that
# consent step happens.
MEMBER_STORIES: tuple[MemberStory, ...] = (
    MemberStory(
        quote="The DE zoom camp gave me skills that helped me land my first tech job.",
        name="Tim Claytor",
        context="DE Zoomcamp",
        source_url="https://www.linkedin.com/feed/update/urn:li:activity:7396882073308938240",
    ),
    MemberStory(
        quote=(
            "This course gave me hands-on experience in building LLM-powered applications, "
            "including prompt engineering, retrieval-augmented generation (RAG), pipeline "
            "orchestration, and vector search optimization."
        ),
        name="Alexander Daniel Rios",
        context="Argentina · LLM Zoomcamp",
        source_url=(
            "https://www.linkedin.com/posts/alexander-daniel-rios_llmzoomcamp-ai-llm-"
            "activity-7391098999820406784-ByF1"
        ),
    ),
    MemberStory(
        quote=(
            "No other course I've taken or explored took such a comprehensive approach to "
            "what it means to develop ML models, all while maintaining a level of "
            "accessibility that opens the doors to folks from all different backgrounds."
        ),
        name="Zachary Keller",
        context="ML Zoomcamp",
        source_url=(
            "https://www.linkedin.com/posts/zrkeller_course-report-datatalksclub-ml-zoomcamp-"
            "activity-7013629465083707392-RbZV"
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class WikiTopic:
    slug: str
    title: str
    summary: str
    public_path: str


@dataclass(frozen=True, slots=True)
class WikiGraph:
    hub: WikiTopic
    spokes: tuple[WikiTopic, ...]
    layouts: tuple[GraphLayout, ...]
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
    for family, title, label in COURSE_FAMILIES:
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
        layouts=ring_layouts(
            GraphPoint(title=hub.title, url=hub.public_path),
            tuple(GraphPoint(title=topic.title, url=topic.public_path) for topic in spokes),
        ),
        connections=len(related),
    )
