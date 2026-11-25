"""The wiki hub's read model: topic facts, ranked search and the hub's sections.

Everything here is derived at read time from the synced wiki rows
(``content.wiki_reader``) and the two synced singletons the wiki publishes
beside them, the knowledge graph and the search corpus (``content.catalogue``).
Nothing is stored: an episode count is the graph's own podcast edges, a heading
is a section document of the search corpus, an area is the rule in
``content.wiki_areas``.

The derived index is built once per synced state.  The singletons are the same
objects for as long as their rows are unchanged, so the cache holds the objects
it was built from and rebuilds when any of them is a different object.
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import catalogue, wiki_reader
from .wiki_areas import AREAS, area_for

#: The kinds the podwiki parser tags a page with, as the hub names them.  A page
#: without a kind tag is a plain topic.
KIND_LABELS = {
    "topic": "Topic",
    "guide": "Guide",
    "comparison": "Comparison",
    "roadmap": "Roadmap",
    "transition": "Career transition",
    "how-to": "How-to",
}

#: The "Guides and roadmaps" card: each kind, its plural, and its special-pages
#: listing.
KIND_LISTINGS = (
    ("guide", "Guides", "guides"),
    ("comparison", "Comparisons", "comparisons"),
    ("transition", "Career transitions", "transitions"),
    ("roadmap", "Roadmaps", "roadmaps"),
    ("how-to", "How-tos", "how-tos"),
)

#: The hub's "Featured" band, chosen by the editors rather than computed: the
#: two roadmaps to start a path from, then the four topics the most episodes
#: cite (umbrella pages such as "Machine Learning" left out).  A slug the wiki
#: does not publish drops its card.  Edit these tuples to change the band.
FEATURED_ROADMAPS = ("ai-engineering-roadmap", "llm-rag-production-roadmap")
FEATURED_TOPICS = (
    "mlops",
    "career-transitions-in-data",
    "machine-learning-for-business",
    "portfolio-projects",
)

#: The hub's "Start here" band: a reader's goal and the roadmap or transition
#: page that answers it.  Only goals with a real target page are listed; a
#: target the wiki stops publishing drops its card.
START_GOALS = (
    ("Switch into data engineering", "data-engineer-roadmap"),
    ("Become an AI engineer", "ai-engineering-roadmap"),
    ("Get your first data job", "data-analyst-careers"),
    ("Move from data science to ML engineering", "data-scientist-to-machine-learning-engineer"),
)

#: Broad umbrella pages nearly every episode touches ("Machine Learning",
#: "AI").  They count towards their area but are left out of an area card's top
#: topics, which would otherwise repeat the area's own name back to the reader.
UMBRELLA_TOPICS = frozenset(
    {
        "ai",
        "career-development",
        "career-growth",
        "data-engineering",
        "data-science",
        "leadership",
        "llms",
        "machine-learning",
        "open-source",
        "practices",
        "production",
        "software-engineering",
        "tools",
    }
)

#: How many topics each area card lists, and how many live results the search
#: endpoint returns.
AREA_TOPICS = 4
LIVE_RESULTS = 5

#: Search scores, per query word.  A word that matches nothing excludes the page.
_TITLE_WORD = 100
_TITLE_PART = 40
_SUMMARY_WORD = 20
_SUMMARY_PART = 10
_HEADING = 6
_BODY = 2
#: Whole-query bonuses: the exact title, the title's initials ("rag" for
#: Retrieval-Augmented Generation), and a title that starts with the query.
_EXACT_TITLE = 500
_INITIALS = 300
_TITLE_PREFIX = 60

_WORD = re.compile(r"[\w+#]+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


@dataclass(frozen=True)
class HubTopic:
    slug: str
    title: str
    summary: str
    url: str
    kind: str
    episodes: int
    links: int

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, KIND_LABELS["topic"])

    @property
    def is_typed(self) -> bool:
        return self.kind != "topic"

    @property
    def episodes_label(self) -> str:
        """``"13 episodes"`` (one unbreakable phrase), or nothing for a page no episode cites."""

        if not self.episodes:
            return ""
        return f"{self.episodes}\u00a0episode{'' if self.episodes == 1 else 's'}"

    @property
    def meta(self) -> str:
        """A card's one meta line: ``"Roadmap · 13 episodes"`` or ``"74 episodes"``."""

        parts = (self.kind_label if self.is_typed else "", self.episodes_label)
        return "\u00a0· ".join(part for part in parts if part)

    def as_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "episodes": self.episodes,
            "meta": self.meta,
        }


@dataclass(frozen=True)
class _SearchEntry:
    topic: HubTopic
    title: str
    title_words: frozenset[str]
    initials: str
    summary: str
    summary_words: frozenset[str]
    headings: str
    body: str


@dataclass(frozen=True)
class HubStats:
    topics: int
    episodes: int
    books: int
    events: int


@dataclass
class _Index:
    topics: tuple[HubTopic, ...]
    by_slug: dict[str, HubTopic]
    entries: tuple[_SearchEntry, ...]
    stats: HubStats
    busiest: HubTopic | None = None
    sources: tuple[Any, ...] = field(default=(), repr=False)


_lock = threading.Lock()
_cached: _Index | None = None


def _index() -> _Index:
    global _cached
    pages = wiki_reader.wiki_pages()
    graph = catalogue.wiki_graph()
    corpus = catalogue.wiki_search()
    sources = (pages, graph, corpus)
    held = _cached
    if held is not None and all(a is b for a, b in zip(held.sources, sources, strict=True)):
        return held
    built = _build(pages, graph, corpus)
    built.sources = sources
    with _lock:
        _cached = built
    return built


def _build(pages: tuple[dict, ...], graph: dict, corpus: dict) -> _Index:
    node_types = {str(node.get("id")): str(node.get("type", "")) for node in graph.get("nodes", ())}
    neighbours: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for link in graph.get("links", ()):
        source, target = str(link.get("source", "")), str(link.get("target", ""))
        for one, other in ((source, target), (target, source)):
            if one.startswith("wiki:") and one != other:
                neighbours[one][node_types.get(other, "")].add(other)

    headings: dict[str, list[str]] = defaultdict(list)
    bodies: dict[str, str] = {}
    for document in corpus.get("docs", ()):
        graph_id = str(document.get("graph_id", ""))
        if document.get("document_type") == "section":
            headings[graph_id].append(str(document.get("segment_title", "")))
        elif document.get("document_type") == "page":
            bodies[graph_id] = str(document.get("text", ""))

    topics: list[HubTopic] = []
    entries: list[_SearchEntry] = []
    events: set[str] = set()
    for page in pages:
        node_id = f"wiki:{page['slug']}"
        tags = page.get("tags") or ()
        kind = str(tags[0]) if tags and str(tags[0]) in KIND_LABELS else "topic"
        linked = neighbours.get(node_id, {})
        topic = HubTopic(
            slug=str(page["slug"]),
            title=str(page["title"]),
            summary=str(page["summary"]),
            url=str(page["public_path"]),
            kind=kind,
            episodes=len(linked.get("podcast", ())),
            links=len(linked.get("wiki", ())) + len(linked.get("article", ())),
        )
        topics.append(topic)
        for relation in page.get("relations") or ():
            if relation.get("type") == "event" and relation.get("label"):
                events.add(str(relation["label"]).casefold())
        title_words = _words(topic.title)
        entries.append(
            _SearchEntry(
                topic=topic,
                title=topic.title.casefold(),
                title_words=frozenset(title_words),
                initials="".join(word[0] for word in title_words),
                summary=topic.summary.casefold(),
                summary_words=frozenset(_words(topic.summary)),
                headings=" \n".join(headings.get(node_id, ())).casefold(),
                body=bodies.get(node_id, "").casefold(),
            )
        )

    def cited(node_type: str) -> int:
        found: set[str] = set()
        for linked in neighbours.values():
            found |= linked.get(node_type, set())
        return len(found)

    by_slug = {topic.slug: topic for topic in topics}
    return _Index(
        topics=tuple(topics),
        by_slug=by_slug,
        entries=tuple(entries),
        stats=HubStats(
            topics=len(topics),
            episodes=cited("podcast"),
            books=cited("book"),
            events=len(events),
        ),
        busiest=max(topics, key=lambda topic: (topic.links, topic.title), default=None),
    )


def _score(entry: _SearchEntry, query: str, words: list[str], starts: list[re.Pattern[str]]) -> int:
    """Score one page; a partial match must start a word ("airfl", never "rag" in "storage")."""

    score = 0
    for word, start in zip(words, starts, strict=True):
        if word in entry.title_words:
            score += _TITLE_WORD
        elif word in entry.title and start.search(entry.title):
            score += _TITLE_PART
        elif word in entry.summary_words:
            score += _SUMMARY_WORD
        elif word in entry.summary and start.search(entry.summary):
            score += _SUMMARY_PART
        elif word in entry.headings and start.search(entry.headings):
            score += _HEADING
        elif word in entry.body and start.search(entry.body):
            score += _BODY
        else:
            return 0
    if entry.title == query:
        score += _EXACT_TITLE
    compact = "".join(words)
    if len(compact) >= 2 and entry.initials == compact:
        score += _INITIALS
    if entry.title.startswith(query):
        score += _TITLE_PREFIX
    return score


def search(query: str) -> tuple[HubTopic, ...]:
    """Every wiki page matching all words of ``query``, best first.

    Whole-word title matches beat partial ones, which beat the summary, then a
    section heading, then the body; a query that spells a title's initials
    ("rag") ranks that page first.  Ties go to the page more episodes cite.
    """

    folded = " ".join(query.casefold().split())[:200]
    words = _words(folded)
    if not words:
        return ()
    starts = [re.compile(r"(?<![\w+#])" + re.escape(word)) for word in words]
    scored = [
        (score, entry.topic)
        for entry in _index().entries
        if (score := _score(entry, folded, words, starts)) > 0
    ]
    scored.sort(key=lambda item: (-item[0], -item[1].episodes, item[1].title.casefold()))
    return tuple(topic for _score_value, topic in scored)


# --- hub sections ------------------------------------------------------------


@dataclass(frozen=True)
class AreaCard:
    title: str
    description: str
    count: int
    topics: tuple[HubTopic, ...]


@dataclass(frozen=True)
class KindRow:
    label: str
    count: int
    category: str


@dataclass(frozen=True)
class StartGoal:
    goal: str
    topic: HubTopic

    @property
    def meta(self) -> str:
        """The page the goal opens, then how many episodes it draws on."""

        return "\u00a0· ".join(
            part for part in (self.topic.title, self.topic.episodes_label) if part
        )


@dataclass(frozen=True)
class LetterGroup:
    letter: str
    topics: tuple[HubTopic, ...]


def stats() -> HubStats:
    return _index().stats


def busiest() -> HubTopic | None:
    return _index().busiest


def _pick(slugs: tuple[str, ...]) -> tuple[HubTopic, ...]:
    by_slug = _index().by_slug
    return tuple(by_slug[slug] for slug in slugs if slug in by_slug)


def featured_roadmaps() -> tuple[HubTopic, ...]:
    return _pick(FEATURED_ROADMAPS)


def featured_topics() -> tuple[HubTopic, ...]:
    return _pick(FEATURED_TOPICS)


def start_goals() -> tuple[StartGoal, ...]:
    by_slug = _index().by_slug
    return tuple(
        StartGoal(goal=goal, topic=by_slug[slug]) for goal, slug in START_GOALS if slug in by_slug
    )


def _by_episodes(topics: list[HubTopic]) -> list[HubTopic]:
    return sorted(topics, key=lambda topic: (-topic.episodes, topic.title.casefold()))


def areas() -> tuple[AreaCard, ...]:
    filed: dict[str, list[HubTopic]] = defaultdict(list)
    for topic in _index().topics:
        filed[area_for(topic.title, topic.kind).slug].append(topic)
    return tuple(
        AreaCard(
            title=area.title,
            description=area.description,
            count=len(filed[area.slug]),
            topics=tuple(
                topic
                for topic in _by_episodes(filed[area.slug])
                if topic.slug not in UMBRELLA_TOPICS
            )[:AREA_TOPICS],
        )
        for area in AREAS
        if filed[area.slug]
    )


def kind_rows() -> tuple[KindRow, ...]:
    counts: dict[str, int] = defaultdict(int)
    for topic in _index().topics:
        counts[topic.kind] += 1
    return tuple(
        KindRow(label=label, count=counts[kind], category=category)
        for kind, label, category in KIND_LISTINGS
        if counts[kind]
    )


def letter_groups() -> tuple[LetterGroup, ...]:
    groups: dict[str, list[HubTopic]] = {}
    for topic in _index().topics:
        initial = topic.title[:1].upper()
        letter = initial if initial.isalpha() else "#"
        groups.setdefault(letter, []).append(topic)
    return tuple(
        LetterGroup(letter=letter, topics=tuple(items)) for letter, items in groups.items()
    )


__all__ = [
    "FEATURED_ROADMAPS",
    "FEATURED_TOPICS",
    "LIVE_RESULTS",
    "START_GOALS",
    "HubTopic",
    "areas",
    "busiest",
    "featured_roadmaps",
    "featured_topics",
    "kind_rows",
    "letter_groups",
    "search",
    "start_goals",
    "stats",
]
