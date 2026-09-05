"""Editorial composition for the redesigned public homepage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from django.urls import NoReverseMatch, reverse

from content.public_data import public_projection
from core.graph_layout import GraphLayout, GraphPoint, ring_layouts
from courses.services.public_course_catalog import (
    cohort_recency_key,
    latest_visible_cohort_per_family,
)

FEATURED_FAMILY = "ai-dev-tools"

# Family slug prefix and catalogue title.  The cards used to carry a third value, a
# short uppercase category pill ("Data Engineering", "LLMs", ...); the owner removed
# that pill from the catalogue cards, and nothing else read the value.
#
# This table is presentation only.  The database decides which families exist and what
# they are called; all this decides is the order they are shown in.  A family the
# database holds and this table does not still renders, labelled with its own
# ``Course.title`` and placed after the listed ones.
COURSE_FAMILIES: tuple[tuple[str, str], ...] = (
    ("ai-dev-tools", "AI Dev Tools Zoomcamp"),
    ("de-zoomcamp", "Data Engineering Zoomcamp"),
    ("llm-zoomcamp", "LLM Zoomcamp"),
    ("ml-zoomcamp", "Machine Learning Zoomcamp"),
    ("mlops-zoomcamp", "MLOps Zoomcamp"),
    ("sma-zoomcamp", "Stock Markets Analytics Zoomcamp"),
)

# Two visible ``Course`` rows currently carry the title "AI Dev Tools Zoomcamp":
# ``ai-dev-tools`` (holding the 2025 cohort) and ``ai-dev-tools-zoomcamp`` (holding the
# 2026 one).  Reading families straight from the database would therefore show the
# course twice, and a family-keyed read would keep resolving AI Dev Tools to 2025.  This
# alias collapses them onto one catalogue key so the homepage shows one card resolved to
# the newest cohort across both.  It is presentation only: no row, no URL and no
# migration changes, and it is deleted once issue #308 merges the two rows.
FAMILY_ALIASES: dict[str, str] = {"ai-dev-tools-zoomcamp": "ai-dev-tools"}

# The designed landing page for the featured cohort.  It is a fixed route rather than a
# course-page link, so it is named here instead of derived from the resolved cohort.
FEATURED_COHORT_ROUTE_NAME = "course-cohort-ai-dev-tools-2026"

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
    """One zoomcamp family, represented by its most recent visible cohort.

    Every value here is a database fact: ``title`` is the family's own
    ``Course.title``, ``cohort_title`` and ``start_date`` come from the selected
    ``Cohort``, and ``public_path`` is that cohort's canonical course route.  Editorial
    copy the database has no field for stays in the page-owned constants above.
    """

    family: str
    slug: str
    title: str
    public_path: str
    cohort_label: str
    homework_count: int
    project_count: int
    module_count: int = 0
    cohort_title: str = ""
    start_date: date | None = None
    #: How the cohort is delivered, its one-sentence summary, and what it
    #: promises you will build -- the cohort's own rows, empty when it has not
    #: been given any. The panel omits what is empty rather than substituting
    #: copy from somewhere else.
    delivery_format: str = ""
    promo_summary: str = ""
    build_items: tuple[str, ...] = ()

    @property
    def start_display(self) -> str:
        """The start date the way the design writes it, or nothing when unknown."""

        if self.start_date is None:
            return ""
        return f"{self.start_date:%B} {self.start_date.day}, {self.start_date:%Y}"


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


def _catalog_order(family: str) -> tuple[int, str]:
    """Sort listed families in their reviewed order and unlisted ones after them."""

    for index, (listed, _title) in enumerate(COURSE_FAMILIES):
        if listed == family:
            return (index, family)
    return (len(COURSE_FAMILIES), family)


def course_catalog() -> tuple[CatalogCourse, ...]:
    """Return one card per visible course family, showing its newest visible cohort.

    The database owns which families exist, so a family it holds and ``COURSE_FAMILIES``
    does not still renders.  Nothing here raises: an empty, partial or unreachable
    database yields a shorter catalogue or none at all, and the homepage renders its
    empty state instead of a 500.
    """

    collapsed: dict[str, Any] = {}
    for family_slug, cohort in latest_visible_cohort_per_family().items():
        key = FAMILY_ALIASES.get(family_slug, family_slug)
        current = collapsed.get(key)
        if current is None or cohort_recency_key(cohort) > cohort_recency_key(current):
            collapsed[key] = cohort

    catalog: list[CatalogCourse] = []
    for family in sorted(collapsed, key=_catalog_order):
        cohort = collapsed[family]
        try:
            public_path = reverse("course", args=[cohort.course.slug, cohort.identifier])
        except NoReverseMatch:
            continue
        catalog.append(
            CatalogCourse(
                family=family,
                slug=str(cohort.slug),
                title=str(cohort.course.title),
                public_path=public_path,
                cohort_label=f"{cohort.year} cohort",
                homework_count=int(cohort.homework_count),
                project_count=int(cohort.project_count),
                module_count=int(getattr(cohort, "module_count", 0) or 0),
                cohort_title=str(cohort.title),
                start_date=cohort.start_date,
                delivery_format=str(cohort.delivery_format),
                promo_summary=str(cohort.promo_summary),
                build_items=tuple(item.text for item in cohort.build_items.order_by("position")),
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


def _wiki_topic(pages: dict[str, dict[str, Any]], slug: str) -> WikiTopic | None:
    """The topic for ``slug``, or ``None`` when the wiki does not publish it.

    The homepage names the topics it would like to show, but the wiki decides
    which pages exist. A page the wiki has not published (an un-ingested
    database publishes none at all) drops its card rather than taking the
    homepage down with it.
    """

    page = pages.get(slug)
    if page is None:
        return None
    return WikiTopic(
        slug=slug,
        title=str(page["title"]),
        summary=str(page["summary"]),
        public_path=str(page["public_path"]),
    )


def wiki_topics() -> tuple[WikiTopic, ...]:
    pages = _wiki_pages()
    topics = (_wiki_topic(pages, slug) for slug in WIKI_TOPICS)
    return tuple(topic for topic in topics if topic is not None)


def wiki_graph() -> WikiGraph | None:
    """Draw the hub and its real wiki relations, never an invented edge.

    ``None`` when the wiki does not publish the hub, so the homepage simply
    draws no graph. A published hub that does not link to a named spoke is a
    different matter -- the graph would be drawing an edge that does not exist
    -- and still fails loudly.
    """

    pages = _wiki_pages()
    hub_page = pages.get(WIKI_GRAPH_HUB)
    hub = _wiki_topic(pages, WIKI_GRAPH_HUB)
    if hub_page is None or hub is None:
        return None
    related = {
        str(relation["href"]) for relation in hub_page["relations"] if relation["type"] == "wiki"
    }
    spokes: list[WikiTopic] = []
    for slug in WIKI_GRAPH_SPOKES:
        topic = _wiki_topic(pages, slug)
        if topic is None:
            continue
        if topic.public_path not in related:
            raise ImproperlyConfigured(f"{WIKI_GRAPH_HUB} does not link to {slug}.")
        spokes.append(topic)
    return WikiGraph(
        hub=hub,
        spokes=tuple(spokes),
        layouts=ring_layouts(
            GraphPoint(title=hub.title, url=hub.public_path),
            tuple(GraphPoint(title=topic.title, url=topic.public_path) for topic in spokes),
        ),
        connections=len(related),
    )
