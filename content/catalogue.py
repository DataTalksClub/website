"""Read the published editorial catalogue from the database.

The public pages -- the blog, the podcast, the book archive, the profiles, the
wiki -- read their records from database rows, and this module is the one place
that turns those rows into the records the views and templates read, with a
function per kind rather than one dictionary holding every kind at once.

They share a module because they share everything that makes the read work:
the same publishing authority per kind, the same stored editorial order, and
the same cache key. Split per kind, that resolution would be written seven
times and could drift seven ways.

During the #384 cutover the wiki kinds, the editorial collections, the
people profiles, the media records and the course catalogue copies have a
second authority ahead of the staged pipeline's retirement: the
community_base sync engine's :class:`~content.models.SyncedDocument` rows,
written by the ``dtc-podwiki``, ``dtc-content``, ``dtc-main-site`` and
course-repository parsers. The staged ``dtc-public-content`` release still
publishes the remaining kinds -- the manifest and its derived records --
until the pipeline is retired. Each kind reads exactly one authority --
never a blend and never a fallback.

A database with no published rows publishes nothing. That is a normal state,
not a failure: hubs render empty and detail lookups miss, which is what an
un-ingested database should do.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from uuid import UUID

from django.db.models import Count, Max

from .models import ContentDocument, ContentSource, SyncedDocument
from .public_graph import validate_wiki_graph
from .public_text import strip_leaked_target_attributes
from .sync_parsers.course import COURSE_SOURCE_SLUGS

#: One published record, exactly as the import stored it. The pages read these
#: as mappings because that is what the catalogue's own records are: a book has
#: authors and a cover, a wiki page has relations, and no two kinds share a
#: shape worth naming a dataclass over.
Record = dict[str, Any]

#: The registered source whose active release publishes the editorial
#: catalogue: articles, podcasts, books, people, wiki pages and the derived
#: graph, search and route records that go with them.
PUBLIC_CONTENT_STABLE_ID = "dtc-public-content"

#: The collections the catalogue publishes.
COLLECTION_NAMES = (
    "articles",
    "podcasts",
    "books",
    "people",
    "wiki",
    "courses",
    "media",
)
#: The document kind each collection's records are stored under. The import
#: names a kind by dropping the collection's plural ``s``, which leaves
#: "people", "wiki" and "media" spelled as they are.
COLLECTION_KINDS = {name: name.rstrip("s") or name for name in COLLECTION_NAMES}
#: The counts the homepage states, per collection, and the transcript count
#: beside them.
COUNT_KEYS = (*COLLECTION_NAMES, "transcripts")

#: The community_base sync source whose synced rows publish the wiki, and the
#: kinds it owns: the pages the hub lists and the three singletons -- the
#: knowledge graph, the search index and the declared asset paths -- that a
#: database publishes one document apiece. These kinds read
#: :class:`~content.models.SyncedDocument` rows (issue #384); the staged
#: release remains the authority for every other kind until it is retired.
WIKI_SOURCE_SLUG = "dtc-podwiki"
WIKI_PAGE_KIND = "wiki"
WIKI_SINGLETON_KINDS = ("wiki_graph", "wiki_search", "wiki_assets")
WIKI_SYNCED_KINDS = (WIKI_PAGE_KIND, *WIKI_SINGLETON_KINDS)

#: The sync source whose synced rows publish the editorial collections that
#: have cut over, and the kinds it owns: articles, podcasts and books.
EDITORIAL_SOURCE_SLUG = "dtc-content"
EDITORIAL_SYNCED_KINDS = ("article", "podcast", "book")

#: The sync source whose synced rows publish the people profiles. The parser
#: reads the legacy main-site repository, which is where the profiles are
#: authored, so its source is its own rather than the editorial collection's.
PEOPLE_SOURCE_SLUG = "dtc-main-site"
PEOPLE_KIND = "people"

#: The kind the site's published images are stored under. Their records ride
#: on two sources -- the editorial tree files are ``dtc-content``'s, the
#: profile pictures are ``dtc-main-site``'s -- so, unlike the single-source
#: kinds, the media reader composes across both stamps.
MEDIA_KIND = "media"

#: The site page kinds the editorial source publishes as one row apiece: the
#: podcast platform catalog and the ``/slack`` page. Both are authored in the
#: structured content repository and read like every other synced kind; a
#: database that publishes none offers no platforms and no Slack page.
SLACK_PAGE_KIND = "slack_page"
SITE_PAGE_SYNCED_KINDS = ("podcast_platforms", SLACK_PAGE_KIND)

#: The kind the course catalogue copies are stored under. Each course
#: repository is its own sync source (the parser rejects every other), so --
#: like media, only more so -- the collection composes across all of the
#: sources' stamps. The copy is authored in each repository's ``course.yaml``
#: ``catalog:`` block (issue #384); a database whose course repositories have
#: not declared any publishes no catalogue copies.
COURSE_KIND = "course"

#: Every kind that reads the synced rows, mapped to the source that publishes
#: it: the dispatch ``records`` makes before falling back to the staged
#: release. The multi-source kinds (media, courses) dispatch on their own
#: branches instead. Each kind reads exactly one authority -- never a blend,
#: never a fallback.
SYNCED_KIND_SOURCES = {
    **{kind: EDITORIAL_SOURCE_SLUG for kind in EDITORIAL_SYNCED_KINDS},
    PEOPLE_KIND: PEOPLE_SOURCE_SLUG,
    **{kind: WIKI_SOURCE_SLUG for kind in WIKI_SYNCED_KINDS},
    **{kind: EDITORIAL_SOURCE_SLUG for kind in SITE_PAGE_SYNCED_KINDS},
}


def active_release_id() -> str:
    """The id of the release currently publishing the catalogue, or ``""``.

    One cheap indexed lookup, used as the cache key below. It changes exactly
    when an import activates a new release, which is the only thing that can
    change what the catalogue holds, so a cached read follows an import instead
    of outliving it.

    A missing source or pointer is a genuinely empty catalogue and reads as
    ``""``; a database failure raises, so an outage can never enter the cache
    below disguised as content (ARC-01).
    """

    active = (
        ContentSource.objects.filter(stable_id=PUBLIC_CONTENT_STABLE_ID, enabled=True)
        .values_list("active_release_id", flat=True)
        .first()
    )
    return str(active or "")


def records(kind: str) -> tuple[Record, ...]:
    """Every published record of one kind, in the catalogue's own order.

    A database failure raises here -- the ordinary request-failure path handles
    it -- rather than resolving to an empty catalogue that a retry would then
    serve from cache.
    """

    source_slug = SYNCED_KIND_SOURCES.get(kind)
    if source_slug is None:
        if kind == MEDIA_KIND:
            # The media records are one collection drawn from two sources, so
            # the cache key carries a stamp for each.
            return _synced_media(
                synced_stamp(EDITORIAL_SOURCE_SLUG), synced_stamp(PEOPLE_SOURCE_SLUG)
            )
        if kind == COURSE_KIND:
            # The course catalogue copies are one collection drawn from the
            # course repositories' own sources, so the cache key carries a
            # stamp per repository.
            return _synced_courses(*(synced_stamp(slug) for slug in COURSE_SOURCE_SLUGS))
        return _records(active_release_id(), kind)
    if kind in WIKI_SYNCED_KINDS or kind in SITE_PAGE_SYNCED_KINDS:
        return _synced_records(synced_stamp(source_slug), source_slug, kind)
    if kind == PEOPLE_KIND:
        # An absent people source is an empty profiles collection, answered
        # without reading the authorities its derivation would otherwise draw
        # from. A profile's credits follow three of them -- the editorial
        # collections, and the live events the person spoke at -- so the cache
        # key carries a stamp for each.
        people_stamp = synced_stamp(PEOPLE_SOURCE_SLUG)
        if not people_stamp[0]:
            return ()
        return _people(
            people_stamp,
            synced_stamp(EDITORIAL_SOURCE_SLUG),
            _event_credit_stamp(),
        )
    return _synced_editorial(kind, synced_stamp(source_slug), synced_stamp(PEOPLE_SOURCE_SLUG))


def synced_stamp(source_slug: str) -> tuple[int, str]:
    """A cheap stamp that moves whenever one source's synced rows could have.

    One aggregate, not a row per record, with the same contract as
    :func:`active_release_id`: it changes exactly when a sync writes the
    source's kinds, so a cached read follows a sync instead of outliving it. A
    database failure raises -- the synced readers refuse to let an outage enter
    the cache disguised as an empty catalogue (ARC-01).
    """

    stamp = SyncedDocument.objects.filter(
        source__slug=source_slug, source__is_enabled=True
    ).aggregate(total=Count("id"), latest=Max("updated_at"))
    return (int(stamp["total"] or 0), str(stamp["latest"] or ""))


@lru_cache(maxsize=16)
def _synced_records(stamp: tuple[int, str], source_slug: str, kind: str) -> tuple[Record, ...]:
    """The synced records of ``kind`` the stamp was built from.

    The query is bound to the stamp the cache key names, so a warmed answer
    cannot outlive the rows it was read from: a sync changes the stamp, and the
    next read rebuilds the entry instead of serving stale pages.

    A zero count is an absent source or an empty one -- an empty collection,
    not a failure -- so it answers without reading rows.

    The row table carries no order column: the order is a catalogue fact, so
    the reader derives it from the records themselves. The editorial kinds come
    back newest first, the rule the reviewed build ordered them by; the wiki
    pages come back in the A-Z order the hub pages through; the profiles come
    back in the A-Z-by-title order the reviewed build listed them in.
    """

    if not stamp[0]:
        return ()
    rows = SyncedDocument.objects.filter(
        source__slug=source_slug,
        content_kind=kind,
        is_published=True,
    ).values_list("record", flat=True)
    held = [record for record in rows if isinstance(record, dict)]
    if kind in EDITORIAL_SYNCED_KINDS:
        held.sort(
            key=lambda record: (
                str(record.get("published", "")),
                str(record.get("slug", "")),
            ),
            reverse=True,
        )
        # The bodies carry one known legacy token that pages must never render;
        # the cleanup is a narrow, idempotent per-block repair, applied to the
        # copy the read model holds (ARC-03).
        if kind == "article":
            held = [_cleaned_body(record) for record in held]
    elif kind == PEOPLE_KIND:
        held = [_cleaned_body(record) for record in held]
        held.sort(
            key=lambda record: (
                str(record.get("title", "")).casefold(),
                str(record.get("slug", "")),
            )
        )
    elif kind == WIKI_PAGE_KIND:
        held.sort(
            key=lambda record: (
                str(record.get("title", "")).casefold(),
                str(record.get("slug", "")),
            )
        )
    return tuple(held)


@lru_cache(maxsize=8)
def _synced_editorial(
    kind: str, content_stamp: tuple[int, str], people_stamp: tuple[int, str]
) -> tuple[Record, ...]:
    """The published editorial records of ``kind``, derived from the synced rows.

    The parser records carry what their source authors; the fields that are
    derivations of *other* collections -- the public image address, the byline
    credits resolved against the profiles -- are derived here, at read time, so
    they follow the people records instead of a snapshot of them. The credits
    resolve against the same synced people rows the profile pages read.
    """

    if not content_stamp[0]:
        return ()
    from . import public_records

    people_by_slug = {
        person["slug"]: person
        for person in _synced_records(people_stamp, PEOPLE_SOURCE_SLUG, PEOPLE_KIND)
    }
    derive = {
        "article": public_records.article_record,
        "book": public_records.book_record,
        "podcast": public_records.podcast_record,
    }[kind]
    return tuple(
        derive(record, people_by_slug)
        for record in _synced_records(content_stamp, EDITORIAL_SOURCE_SLUG, kind)
    )


@lru_cache(maxsize=64)
def _records(release_id: str, kind: str) -> tuple[Record, ...]:
    """Every published record of ``kind`` in the release the key names.

    The query is bound to the exact release the cache key names. Releases are
    immutable published snapshots, so a key's answer cannot drift: an
    activation racing the row read leaves the request finishing on the release
    it resolved, and a rollback to a release seen before answers from an entry
    that is still true.  Re-resolving the *current* pointer inside the query
    instead was the defect this binding removed -- content from release B
    could be built, and cached, under a key that was read as A.

    An empty key is an absent active pointer -- an empty catalogue, not a
    failure -- so it answers without touching the database.

    The order is the one stored beside each record. It is editorial -- newest
    first, season order, the sequence a hub lists in -- and no key the rows
    happen to sort by carries it.
    """

    if not release_id:
        return ()
    rows = list(
        ContentDocument.objects.filter(
            content_kind=kind,
            is_published=True,
            # The cache key is the release id as text; the lookup wants it as
            # the UUID it names.
            release_id=UUID(release_id),
        ).values_list("adapter_metadata", flat=True)
    )
    held = [
        (int((row or {}).get("position") or 0), (row or {}).get("record") or {}) for row in rows
    ]
    held.sort(key=lambda item: item[0])
    return tuple(record for _position, record in held)


def singleton(kind: str) -> Record:
    """The one record of a kind the catalogue publishes exactly one of.

    The wiki graph, the search index, the platform links and the route manifest
    are one document apiece rather than a collection of one. A database that
    publishes none gives an empty mapping, which every reader treats as absent.
    """

    held = records(kind)
    return held[0] if held else {}


def _by_slug(collection: tuple[Record, ...], slug: str) -> Record | None:
    return next((record for record in collection if record.get("slug") == slug), None)


def _cleaned_body(record: Record) -> Record:
    copied = dict(record)
    raw_blocks = record.get("blocks")
    if not isinstance(raw_blocks, (list, tuple)):
        return copied
    blocks: list[Any] = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, dict):
            blocks.append(raw_block)
            continue
        block = dict(raw_block)
        text = block.get("text")
        if isinstance(text, str):
            block["text"] = strip_leaked_target_attributes(text, published_body=True)
        blocks.append(block)
    copied["blocks"] = blocks
    return copied


def articles() -> tuple[Record, ...]:
    """Every published article, newest first."""

    return records("article")


def article(slug: str) -> Record | None:
    """One article, or ``None`` when the catalogue does not publish it."""

    return _by_slug(articles(), slug)


def _event_credit_stamp() -> tuple[int, str, int, str]:
    """A cheap stamp that moves whenever a speaker credit could have.

    One aggregate over the event identities and one over their content rows,
    not a row per event. A speaker's credit is read from the content row's
    speakers and links, and the import replaces both as a set whenever a
    re-ingest changes any of them, which saves the content row and moves this
    stamp; the identity aggregate covers the lifecycle and public-path changes
    that decide whether the credit reaches a page at all. Reading all 421
    events on the way to every request, just to decide whether the profile
    records are still valid, costs far more than rebuilding them on the rare
    occasion this changes.

    A database failure raises: the events are one of the authorities the
    profiles are derived from, and an outage must never enter the cache
    disguised as people with no talks (ARC-01).
    """

    from events.models import Event, EventContent

    events = Event.objects.aggregate(total=Count("id"), latest=Max("updated_at"))
    content = EventContent.objects.aggregate(total=Count("id"), latest=Max("updated_at"))
    return (
        int(events["total"] or 0),
        str(events["latest"] or ""),
        int(content["total"] or 0),
        str(content["latest"] or ""),
    )


def people() -> tuple[Record, ...]:
    """Every published profile, with its work credits derived at read time."""

    return records(PEOPLE_KIND)


@lru_cache(maxsize=2)
def _people(
    people_stamp: tuple[int, str],
    editorial_stamp: tuple[int, str],
    event_stamp: tuple[int, str],
) -> tuple[Record, ...]:
    """Profiles, cached on the three things that can change what they say.

    The parser records carry who a person is; the work they are credited with
    is a derivation of the article, book, podcast and event records, so it is
    composed here in one pass over the sources (see
    :func:`~content.public_records.person_relationships`) rather than stored as
    a snapshot that a new episode or a withdrawn event would silently stale.

    A credit an authority does not publish -- an author key with no profile, a
    guest who never had one -- credits no one, rather than exposing a source
    key as if it were a person's name.
    """

    if not people_stamp[0]:
        return ()
    from . import public_records

    people_records = _synced_records(people_stamp, PEOPLE_SOURCE_SLUG, PEOPLE_KIND)
    relationships = public_records.person_relationships(
        _synced_records(editorial_stamp, EDITORIAL_SOURCE_SLUG, "article"),
        _synced_records(editorial_stamp, EDITORIAL_SOURCE_SLUG, "book"),
        _synced_records(editorial_stamp, EDITORIAL_SOURCE_SLUG, "podcast"),
        _published_event_records(),
        people_slugs={record["slug"] for record in people_records},
    )
    return tuple(
        public_records.person_record(record, relationships.get(record["slug"], ()))
        for record in people_records
    )


def _published_event_records() -> tuple[Record, ...]:
    """The published event records the speaker credits are derived from."""

    from events.queries import published_event_records

    return published_event_records()


def person(slug: str) -> Record | None:
    """One profile, or ``None`` when the catalogue does not publish it."""

    return _by_slug(people(), slug)


def people_by_slug() -> dict[str, Record]:
    """Profiles indexed by the source key a credit names them with.

    A credit is resolved once per name drawn on a page, so both indexes are
    built with the profiles rather than scanned for each one.
    """

    return {person["slug"]: person for person in people() if "slug" in person}


def people_by_path() -> dict[str, Record]:
    """Profiles indexed by their own canonical address.

    A composed credit -- a podcast guest, an event speaker -- may carry no
    source key, so its profile link is the second way home.
    """

    return {person["public_path"]: person for person in people() if "public_path" in person}


def podcasts() -> tuple[Record, ...]:
    """Every published episode, in the catalogue's own order.

    This is not the order the pages list episodes in. Season and episode
    numbering is a podcast fact rather than a catalogue one, so
    :func:`content.podcast_content.ordered_podcasts` decides that.
    """

    return records("podcast")


def podcast(slug: str) -> Record | None:
    """One episode, or ``None`` when the catalogue does not publish it."""

    return _by_slug(podcasts(), slug)


def podcast_platforms() -> tuple[Record, ...]:
    """The listening platforms the show publishes, in the offered order."""

    return tuple(singleton("podcast_platforms").get("platforms", ()))


def slack_page() -> Record | None:
    """The ``/slack`` page's record, or ``None`` when no row publishes it."""

    held = records(SLACK_PAGE_KIND)
    return held[0] if held else None


def books() -> tuple[Record, ...]:
    """The Book of the Week archive, newest first."""

    return records("book")


def book(slug: str) -> Record | None:
    """One book, or ``None`` when the catalogue does not publish it."""

    return _by_slug(books(), slug)


def wiki_pages() -> tuple[Record, ...]:
    """The wiki catalogue, in the A-Z order the hub pages through."""

    return records(WIKI_PAGE_KIND)


def wiki_page(slug: str) -> Record | None:
    """One wiki page, or ``None`` when the catalogue does not publish it."""

    return _by_slug(wiki_pages(), slug)


def wiki_graph() -> Record:
    """The wiki knowledge graph, checked before it can reach a page.

    The graph is drawn as links a reader can follow, so its destinations are
    validated where they are read: a stored graph that breaks the contract is a
    refusal rather than something the page renders and hopes about. An
    un-ingested database publishes no graph, and an empty mapping passes
    validation as the absence it is.
    """

    graph = singleton("wiki_graph")
    if graph:
        validate_wiki_graph(graph)
    return graph


def wiki_search() -> Record:
    """The wiki search index the hub and the JSON route read."""

    return singleton("wiki_search")


def wiki_asset_paths() -> frozenset[str]:
    """The wiki asset paths the synced ``wiki_assets`` row declares.

    The asset bytes are a design file that ships with the app; what the database
    owns is whether the wiki publishes it at all, so the route asks here before
    handing anything over.
    """

    declared = singleton("wiki_assets").get("wiki_assets", {})
    return frozenset(declared) if isinstance(declared, dict) else frozenset()


def manifest() -> Record:
    """What the active release records about itself.

    Its provenance, its per-collection counts and the artifacts it was built
    from. Nothing a reader sees comes from here; it is what the release says it
    is, kept beside the records it published.
    """

    return singleton("manifest")


def courses() -> tuple[Record, ...]:
    """The course records the catalogue publishes.

    The catalogue copy -- one record per edition a course repository declares
    in its ``course.yaml`` ``catalog:`` block -- is the synced rows the course
    parsers write, merged across the repositories and read in the reviewed
    order: the editions still running first, then A-Z by title. The course
    pages themselves are database rows of their own; these are the catalogue's
    own copies, kept for the route inventory that checks the two agree.
    """

    return records(COURSE_KIND)


def collection_counts() -> dict[str, int]:
    """How many records each collection actually publishes.

    The homepage states these totals. They are counted from the rows the
    catalogue itself serves -- the same records a hub page lists -- rather
    than read from a release's own claim about itself, so a total cannot
    disagree with the collection it states. A database publishing nothing
    reports a zero for every collection rather than a missing key.
    """

    held = {
        "articles": len(articles()),
        "podcasts": len(podcasts()),
        "books": len(books()),
        "people": len(people()),
        "wiki": len(wiki_pages()),
        "courses": len(courses()),
        "media": len(media()),
        "transcripts": sum(1 for record in podcasts() if record.get("transcript")),
    }
    return {key: held[key] for key in COUNT_KEYS}


def media() -> tuple[Record, ...]:
    """Every published site image, in path order.

    The records are the synced rows the media parser writes -- the editorial
    tree files and the profile pictures -- read from their two sources and
    merged by key. The staged release's stored order carried no reader-visible
    meaning (the route resolves by lookup, the counts by length), so the
    derived collection reads in the order the records name themselves by.
    """

    return records(MEDIA_KIND)


def media_at(public_path: str) -> Record | None:
    """The media record a request addresses, or ``None`` when none is published."""

    return _media_index(synced_stamp(EDITORIAL_SOURCE_SLUG), synced_stamp(PEOPLE_SOURCE_SLUG)).get(
        public_path
    )


@lru_cache(maxsize=2)
def _synced_media(
    editorial_stamp: tuple[int, str], people_stamp: tuple[int, str]
) -> tuple[Record, ...]:
    """The published media records of the two sources the stamps name.

    An absent source publishes no media of its own; both absent is an empty
    collection, not a failure. Each source's rows follow its own stamp, so a
    sync to either side rebuilds the merged answer.
    """

    held = [
        *_synced_records(editorial_stamp, EDITORIAL_SOURCE_SLUG, MEDIA_KIND),
        *_synced_records(people_stamp, PEOPLE_SOURCE_SLUG, MEDIA_KIND),
    ]
    held.sort(key=lambda record: str(record.get("record_key", "")))
    return tuple(held)


@lru_cache(maxsize=2)
def _synced_courses(*stamps: tuple[int, str]) -> tuple[Record, ...]:
    """The published course records of the sources the stamps name.

    An absent repository publishes no catalogue copy of its own; all absent is
    an empty collection, not a failure. Each repository's rows follow its own
    stamp, so a sync to any one side rebuilds the merged answer. The order is
    the reviewed build's: unfinished editions first, then A-Z by title, then
    slug. An edition slug two repositories both declare is an authoring
    defect, not a quiet double count -- the read fails closed on it, as the
    reviewed build did.
    """

    held = [
        record
        for stamp, source_slug in zip(stamps, COURSE_SOURCE_SLUGS, strict=True)
        for record in _synced_records(stamp, source_slug, COURSE_KIND)
    ]
    slugs = [str(record.get("slug", "")) for record in held]
    if len(slugs) != len(set(slugs)):
        raise ValueError("course catalogue: duplicate edition slug across repositories")
    held.sort(
        key=lambda record: (
            bool(record.get("finished", False)),
            str(record.get("title", "")).casefold(),
            str(record.get("slug", "")),
        )
    )
    return tuple(held)


@lru_cache(maxsize=2)
def _media_index(
    editorial_stamp: tuple[int, str], people_stamp: tuple[int, str]
) -> dict[str, Record]:
    """Media records by their public path.

    Every image on the site is one lookup here, so the index is built with the
    records rather than scanned for each request.
    """

    return {
        record["public_path"]: record
        for record in _synced_media(editorial_stamp, people_stamp)
        if "public_path" in record
    }
