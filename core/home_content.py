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

# Page-owned editorial copy for the featured cohort panel.
#
# ``Cohort`` has no format, price or notice field, so this has never been a database fact
# and is not invented here either.  Neither ``Cohort.description`` (generated boilerplate)
# nor ``Course.description`` (raw README markup carrying external image tags and
# courses.datatalks.club links) may be rendered in its place.
#
# THE SOURCE IS THE COHORT THIS PANEL ADVERTISES, AND ONLY THAT COHORT.  The featured
# cohort is AI Dev Tools Zoomcamp 2026, whose curriculum is the four module lessons in
# ``cohorts/2026/`` of DataTalksClub/ai-dev-tools-zoomcamp.  Those are the same lessons the
# site imports into ``courses.Module``/``courses.Unit`` and renders on the cohort's course
# page, so the panel and the page it links to describe one curriculum.  A verbatim copy is
# checked in at ``core/tests/data/ai_dev_tools_zoomcamp_2026/`` with its revision and
# per-file checksums, and ``core.tests.test_homepage`` pins every clause below to a phrase
# that copy actually contains.
#
# This replaces copy that described the 2025 edition: "six modules", a coding agent you
# build yourself, and n8n automation are ``cohorts/2025/`` (modules 01-overview through
# 06-automation-lowcode).  It was taken in good faith from
# ``courses/ai-dev-tools-zoomcamp/curriculum.md`` in DataTalksClub/docs (projected into
# ``content/docs_projection.json`` at revision 3f23e006 and served at
# /docs/courses/ai-dev-tools-zoomcamp/curriculum/), which still enumerates the 2025 modules
# and is therefore not a source for the 2026 cohort.  Do not anchor this panel to a
# course-wide docs page again; anchor it to the cohort's own modules.
#
# The module count is deliberately absent from this sentence.  It is a database fact
# (``CatalogCourse.module_count``) rendered next to the homework and project counts, so it
# cannot drift from the curriculum the site actually holds -- which is how the "six
# modules" claim survived here in the first place.
FEATURED_COHORT_FORMAT = "Online"
FEATURED_COHORT_SUMMARY = (
    "AI-native development: take a vague product idea through specification, build a "
    "working end-to-end application with AI assistance, then deploy and operate it with "
    "observability."
)

# What the featured cohort's mint panel promises you will build: one artefact per 2026
# module, in module order, each traceable to that module's own lesson (see the source note
# above).  The framing is "What you'll build", so each item is something a learner ends up
# holding, not a topic the module covers.
#
# There is no final-project item.  The 2026 cohort definition (``cohorts/2026/cohort.yaml``)
# and cohort README list these four modules and their four homeworks and nothing else; the
# repository's ``project/README.md`` marks the 2026 project requirements as a draft that may
# still change.  The panel does not promise coursework the cohort has not committed to, and
# the cohort's real project rows are counted from the database beside it.
#
# Do not add an item the 2026 lessons do not state.  Two generations of invented copy have
# already shipped here: a multi-agent/RAG curriculum belonging to no DataTalks.Club course
# at all (with a "small groups of 6-8 people" note the course does not offer), and then the
# 2025 curriculum described as if it were the 2026 one.
FEATURED_BUILD_ITEMS: tuple[str, ...] = (
    "a Django app built from a specification, with the AI tool of your choice",
    "a full-stack app with a frontend, a backend, an OpenAPI contract, "
    "and data persisted in SQLite",
    "the same app containerized, integration-tested, and deployed at a public URL",
    "an observability stack, an alert on real user impact, and an agent as first line of support",
)

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

    @property
    def start_display(self) -> str:
        """The start date the way the design writes it, or nothing when unknown."""

        if self.start_date is None:
            return ""
        return f"{self.start_date:%B} {self.start_date.day}, {self.start_date:%Y}"


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

    ``photo_static_path`` is a `static` template-tag path to the person's own
    portrait (e.g. ``"core/testimonials/tim-claytor.jpg"``), supplied by the site
    owner rather than pulled from the checked editorial people catalogue -- these
    three are not `people` catalogue records. When absent the card falls back to
    the plain decorative avatar mark.
    """

    quote: str
    name: str
    context: str
    before: str | None = None
    after: str | None = None
    elapsed: str | None = None
    source_url: str | None = None
    photo_static_path: str | None = None


# REAL, SOURCED TESTIMONIALS (issue #179).  These six quotes are taken verbatim from
# public posts by named members (e.g. LinkedIn) and independently verified via direct
# link, replacing the earlier mockup placeholder copy.  None of them states a
# before/after role transition or an elapsed time, so those fields are intentionally
# left unset rather than invented — see MemberStory above for how the template handles
# that.  The order alternates man/woman/man/woman/man/woman.
#
# IMPORTANT: these individuals have NOT been contacted for homepage-specific consent.
# They posted this content publicly elsewhere, but that is not the same as agreeing to
# be quoted on datatalks.club's homepage. Do not ship this to production until that
# consent step happens.
MEMBER_STORIES: tuple[MemberStory, ...] = (
    MemberStory(
        quote=(
            "The final project was the real challenge, where we applied everything we learned "
            "to build an end-to-end data pipeline. This was really hard. But the feeling of "
            "accomplishment made it all worthwhile. I would do it again!"
        ),
        name="Nevenka Lukic",
        photo_static_path="core/testimonials/nevenka-lukic.jpg",
        context="Data Engineer · Spain",
        source_url=(
            "https://www.linkedin.com/posts/nevenka-lukic_data-engineering-zoomcamp-final-"
            "project-activity-7181985646033461248-Lc1O"
        ),
    ),
    MemberStory(
        quote=(
            "This course gave me hands-on experience in building LLM-powered applications, "
            "including prompt engineering, retrieval-augmented generation (RAG), pipeline "
            "orchestration, and vector search optimization."
        ),
        name="Alexander Daniel Rios",
        photo_static_path="core/testimonials/alexander-daniel-rios.jpg",
        context="DS & ML Engineer · Argentina",
        source_url=(
            "https://www.linkedin.com/posts/alexander-daniel-rios_llmzoomcamp-ai-llm-"
            "activity-7391098999820406784-ByF1"
        ),
    ),
    MemberStory(
        quote=(
            "This course project strengthened my understanding of modern LLM applications, "
            "vector databases, prompt engineering, and production-ready AI workflows. A big "
            "thanks to the DataTalksClub community for providing such an excellent learning "
            "experience."
        ),
        name="Jocelyn Dumlao",
        photo_static_path="core/testimonials/jocelyn-dumlao.jpg",
        context="Data Scientist · Philippines",
        source_url="https://www.linkedin.com/feed/update/urn:li:activity:7486622652921430016/",
    ),
    MemberStory(
        quote=(
            "No other course I've taken or explored took such a comprehensive approach to "
            "what it means to develop ML models, all while maintaining a level of "
            "accessibility that opens the doors to folks from all different backgrounds."
        ),
        name="Zachary Keller",
        photo_static_path="core/testimonials/zachary-keller.jpg",
        context="Data & Analytics · United States",
        source_url=(
            "https://www.linkedin.com/posts/zrkeller_course-report-datatalksclub-ml-zoomcamp-"
            "activity-7013629465083707392-RbZV"
        ),
    ),
    MemberStory(
        quote=(
            "The LLM Zoomcamp 2026 by DataTalksClub is an excellent course. If you're interested "
            "in GenAI, RAG, and LLM engineering, I highly recommend checking it out."
        ),
        name="Hanaa Hammad",
        photo_static_path="core/testimonials/hanaa-hammad.jpg",
        context="Senior Data Engineer · Egypt",
        source_url="https://www.linkedin.com/feed/update/urn:li:activity:7489957731135651840/",
    ),
    MemberStory(
        quote="The Data Engineering Zoomcamp gave me skills that helped me land my first tech job.",
        name="Tim Claytor",
        photo_static_path="core/testimonials/tim-claytor.jpg",
        context="Data Science · United States",
        source_url=(
            "https://www.linkedin.com/feed/update/urn:li:activity:7396882073308938240/"
            "?dashCommentUrn=urn%3Ali%3Afsd_comment%3A%287396889959711793152%2Curn%3Ali%3A"
            "activity%3A7396882073308938240%29"
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
