"""Podwiki parser: the community wiki from ``DataTalksClub/podwiki``.

The pages are stored in the shared ``community_base.knowledge_base`` app, in its
``wiki`` section (D7.1): a flat set of pages, each keeping the ``/wiki/<slug>``
path it has always served at as its own ``public_path``. A wiki page is not
markdown -- it is the parsed block structure the site's own templates render --
so the whole parsed record travels in the page's ``record``, which the package
stores and never interprets. The graph, search corpus and asset digests are not
pages and stay ``SyncedDocument`` singleton rows the catalogue reads.

The record rules are the projection builder's wiki rules (``_wiki``): one
page per top-level ``_wiki/*.md`` document, the knowledge graph
(``graph/graph.json``) and the search corpus (``search/search-corpus.json``)
as singleton records the catalogue publishes one apiece, and the declared
public assets as a third singleton carrying the digests the reviewed
release's manifest published under ``wiki_assets``.  The builder's frozen
corpus-wide count pins stay with the projection build contract; the
structural checks -- field allowlists, URL containment, heading fragment
resolution -- travel with the records here, so the parser follows the
repository across commits.

The graph and the search corpus reference entities published by the
*other* sources, so their URL canonicalization reads the podcast, book and
people public paths from the already-synced rows: sync those sources
first.  References the maps cannot place degrade exactly as the reviewed
build degraded them -- podcast nodes and their search documents drop with
the graph counts recomputed, and person links keep their label without a
link -- but a wholly missing collection is a sync-ordering mistake the
parser refuses rather than a gap it silently publishes.
"""

import json
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from community_base.content_sync.checkout import ImmutableCheckout
from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser
from community_base.knowledge_base import sync as knowledge_base_sync
from community_base.knowledge_base.models import SECTION_WIKI

from content.models import SyncedDocument
from scripts import build_public_projection as builder

from . import base

SOURCE_SLUG = "dtc-podwiki"
CONTENT_KIND = "wiki"
GRAPH_KIND = "wiki_graph"
SEARCH_KIND = "wiki_search"
ASSET_KIND = "wiki_assets"
WIKI_ROOT = "_wiki"
GRAPH_SOURCE_PATH = "graph/graph.json"
SEARCH_SOURCE_PATH = "search/search-corpus.json"
ASSET_PUBLIC_PREFIX = "/wiki/assets/"

#: Singleton rows are not pages; the public path only has to be unique and
#: never collide with a route, so it lives under a reserved non-served
#: prefix (the staged import namespaced its internal documents the same way).
SINGLETON_PUBLIC_PATHS = {
    GRAPH_KIND: f"/-/podwiki/{GRAPH_KIND}",
    SEARCH_KIND: f"/-/podwiki/{SEARCH_KIND}",
    ASSET_KIND: f"/-/podwiki/{ASSET_KIND}",
}

#: Engine-visible item keys.  ``$`` is outside the wiki slug alphabet, so a
#: singleton item can never collide with a page slug in the sync's duplicate
#: key check, and its presence in ``seen_keys`` says the singleton was seen.
SINGLETON_ITEM_KEYS = {kind: f"${kind}" for kind in SINGLETON_PUBLIC_PATHS}

_GRAPH_FIELDS = {"generated_at", "counts", "nodes", "links"}
_GRAPH_LINK_FIELDS = {"kind", "source", "target", "weight"}
_GRAPH_NODE_FIELDS = {
    "collection",
    "count",
    "id",
    "keyword",
    "label",
    "search",
    "title",
    "type",
    "url",
}
_SEARCH_FIELDS = {"docs"}
_SEARCH_DOCUMENT_FIELDS = {
    "document_type",
    "episode_slug",
    "graph_id",
    "id",
    "level",
    "page_title",
    "related_terms",
    "segment_title",
    "text",
    "title",
    "url",
}
_FRAGMENT_ROUTE = re.compile(r"/wiki/([A-Za-z0-9._-]+)")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: Collections the graph/search corpus carries that this site has no reader
#: for: ``course_wiki`` names the (unimported) ``_course_wiki/`` pages and
#: points at an unrouted ``/course-wiki/...``; ``event`` cites a DTC event by
#: its raw recording link (YouTube etc.) rather than this site's own event
#: page, which the wiki repository has no way to resolve to. Both degrade
#: the same way an unresolvable podcast reference does: present upstream,
#: absent here, never a hard failure.
_UNSUPPORTED_GRAPH_COLLECTIONS = frozenset({"course_wiki", "event"})
#: The search corpus's own field naming for the same two collections
#: (``level`` "course" for a ``course_wiki`` node, "event" for an ``event`` one).
_UNSUPPORTED_SEARCH_LEVELS = frozenset({"course", "event"})


def _derived_checksum(source_checksum: str, record: dict) -> str:
    # The record derives from its source file plus data the sync does not own
    # (the other pages' titles, the entity paths), so change detection covers
    # the whole derived record, not just the file it was parsed from.
    return builder._sha256_bytes(
        (source_checksum + "|" + json.dumps(record, sort_keys=True, ensure_ascii=False)).encode()
    )


class PodwikiParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        base.activate(checkout)
        podcast_paths, book_paths, people_paths = self._entity_paths()
        graph = self._graph(checkout, podcast_paths, book_paths, people_paths)
        search, expected_fragments = self._search(checkout, podcast_paths, book_paths, people_paths)
        # Pages parse in two phases, mirroring the builder: every title is
        # known before the first relation resolves, so a forward [[link]]
        # lands on the same slug the published corpus uses.
        title_to_slug: dict[str, str] = {}
        parsed: list[tuple[PurePosixPath, dict, str, str]] = []
        for relative in checkout.files():
            path = PurePosixPath(str(relative))
            if path.parts[:1] != (WIKI_ROOT,) or len(path.parts) != 2 or path.suffix != ".md":
                continue
            metadata, body = builder._frontmatter(base.snapshot_path(checkout, relative))
            slug = builder._safe_key(path.stem, field="wiki slug")
            title = builder._title_from_record(metadata, slug)
            if title.casefold() in title_to_slug:
                base.fail("duplicate wiki title", relative)
            title_to_slug[title.casefold()] = slug
            checksum = base.checksum_of(checkout, relative)
            parsed.append((path, metadata, body, checksum))
        known_slugs = {path.stem for path, _metadata, _body, _checksum in parsed}
        if set(expected_fragments) - known_slugs:
            base.fail("wiki search references an unknown projected page", SEARCH_SOURCE_PATH)
        records = [
            self._page_record(
                checkout,
                path,
                metadata,
                body,
                checksum,
                title_to_slug,
                podcast_paths,
                book_paths,
                people_paths,
                expected_fragments,
            )
            for path, metadata, body, checksum in parsed
        ]
        # The A-Z order is editorial and the catalogue pages through it, so it
        # travels in the item order the records are written in.
        records.sort(key=lambda record: (record["title"].casefold(), record["slug"]))
        person_hrefs = {
            relation["href"]
            for record in records
            for relation in record["relations"]
            if relation["type"] == "person"
        }
        if any(href and href not in set(people_paths.values()) for href in person_hrefs):
            base.fail("wiki person relation target is not a synced profile", WIKI_ROOT)
        items = [
            SourceItem(
                key=record["slug"],
                path=record["source_path"],
                data={
                    "content_kind": CONTENT_KIND,
                    "record": record,
                    "stable_key": record["slug"],
                    "public_path": record["public_path"],
                    "source_path": record["source_path"],
                    "commit_sha": checkout.commit_sha,
                    "checksum": _derived_checksum(record["provenance"]["checksum"], record),
                },
            )
            for record in records
        ]
        items.append(self._singleton_item(checkout, GRAPH_SOURCE_PATH, GRAPH_KIND, graph))
        items.append(self._singleton_item(checkout, SEARCH_SOURCE_PATH, SEARCH_KIND, search))
        items.append(self._asset_item(checkout))
        return items

    def upsert(self, item, source, media):
        data = item.data
        record = data["record"]
        if data["content_kind"] == CONTENT_KIND:
            # A wiki page is a knowledge base page: the shared app owns the row,
            # the title, the summary and the public path, and stores the rest of
            # the record -- blocks, tags, relations, heading fragments -- as the
            # site's own opaque metadata. Wiki pages are a flat set, so no page
            # carries a parent.
            page, action = knowledge_base_sync.upsert_page(
                source,
                section=SECTION_WIKI,
                slug=data["stable_key"],
                title=record.get("title") or data["stable_key"],
                summary=record.get("summary") or "",
                public_path=data["public_path"],
                record=record,
                commit_sha=data["commit_sha"],
                source_path=data["source_path"],
                checksum=data["checksum"],
            )
            return UpsertResult(page, action)
        document, action = base.upsert_document(
            source,
            content_kind=data["content_kind"],
            stable_key=data["stable_key"],
            slug="",
            title=record.get("title") or data["stable_key"],
            summary=record.get("summary") or "",
            public_path=data["public_path"],
            source_path=data["source_path"],
            checksum=data["checksum"],
            record=record,
        )
        return UpsertResult(document, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug != SOURCE_SLUG:
            return 0
        deleted = len(
            knowledge_base_sync.delete_missing(source, SECTION_WIKI, seen_slugs=seen_keys)
        )
        for kind, marker in SINGLETON_ITEM_KEYS.items():
            # The singletons are structural: discover fails when their source
            # artifacts are absent, so a missing marker only means an earlier
            # step failed and the stale rows should go with it.
            if marker not in seen_keys:
                deleted += base.delete_missing(source, kind, set())
        return deleted

    def _entity_paths(self):
        """The public paths of the podcast, book and people records already synced.

        The wiki graph and search corpus link to those entities by their
        source-era URLs; the synced rows decide where each link lands on this
        site.  Episode slugs keep their alias forms because wiki citations
        use both.
        """

        podcast_paths: dict[str, str] = {}
        book_paths: dict[str, str] = {}
        people_paths: dict[str, str] = {}
        rows = SyncedDocument.objects.filter(
            content_kind__in=("podcast", "book", "people"), is_published=True
        ).values_list("content_kind", "stable_key", "public_path")
        for kind, key, public_path in rows:
            if kind == "podcast":
                for alias in {key, key.removesuffix(".md").lstrip("_")}:
                    previous = podcast_paths.get(alias)
                    if previous is not None and previous != public_path:
                        base.fail("ambiguous podcast relation alias", alias)
                    podcast_paths[alias] = public_path
            elif kind == "book":
                book_paths[key] = public_path
            else:
                people_paths[key] = public_path
        if not (podcast_paths and book_paths and people_paths):
            base.fail(
                "wiki sync needs the podcast, book and people sources synced first",
                SOURCE_SLUG,
            )
        return podcast_paths, book_paths, people_paths

    def _graph(self, checkout, podcast_paths, book_paths, people_paths) -> dict:
        raw = builder._load_json_bounded(base.snapshot_path(checkout, GRAPH_SOURCE_PATH))
        if set(raw) != _GRAPH_FIELDS:
            base.fail("wiki graph fields are not public-allowlisted", GRAPH_SOURCE_PATH)
        if any(
            not isinstance(node, dict) or not set(node).issubset(_GRAPH_NODE_FIELDS)
            for node in raw.get("nodes", [])
        ) or any(
            not isinstance(link, dict) or set(link) != _GRAPH_LINK_FIELDS
            for link in raw.get("links", [])
        ):
            base.fail("wiki graph record fields are not public-allowlisted", GRAPH_SOURCE_PATH)
        graph = builder._canonicalize_wiki_document_urls(
            raw, podcast_paths, book_paths, people_paths
        )
        # A podcast node whose episode the catalogue does not publish has no
        # destination; the node and every link through it leave the graph, and
        # the declared counts describe what actually remains. See
        # _UNSUPPORTED_GRAPH_COLLECTIONS for the other kind that degrades here.
        stale_nodes = {
            node["id"]
            for node in graph.get("nodes", [])
            if (node.get("collection") == "podcast" and not node.get("url"))
            or node.get("collection") in _UNSUPPORTED_GRAPH_COLLECTIONS
        }
        if stale_nodes:
            graph["nodes"] = [node for node in graph["nodes"] if node.get("id") not in stale_nodes]
            graph["links"] = [
                link
                for link in graph["links"]
                if link.get("source") not in stale_nodes and link.get("target") not in stale_nodes
            ]
        graph["counts"] = {
            **graph["counts"],
            "nodes": len(graph["nodes"]),
            "links": len(graph["links"]),
            "podcasts": sum(node.get("collection") == "podcast" for node in graph["nodes"]),
        }
        self._check_urls(graph, podcast_paths, book_paths, people_paths)
        return graph

    def _search(self, checkout, podcast_paths, book_paths, people_paths):
        raw = builder._load_json_bounded(base.snapshot_path(checkout, SEARCH_SOURCE_PATH))
        if set(raw) != _SEARCH_FIELDS:
            base.fail("wiki search fields are not public-allowlisted", SEARCH_SOURCE_PATH)
        if any(
            not isinstance(document, dict) or not set(document).issubset(_SEARCH_DOCUMENT_FIELDS)
            for document in raw.get("docs", [])
        ):
            base.fail("wiki search record fields are not public-allowlisted", SEARCH_SOURCE_PATH)
        search = builder._canonicalize_wiki_document_urls(
            raw, podcast_paths, book_paths, people_paths
        )
        # Episodes the catalogue does not publish drop out of the corpus, and
        # their aliases drop out of the related-terms strings that name them.
        stale_episode_slugs = {
            episode_slug
            for document in search["docs"]
            if isinstance(document, dict)
            for episode_slug in (document.get("episode_slug"),)
            if isinstance(episode_slug, str) and episode_slug and episode_slug not in podcast_paths
        }
        stale_episode_aliases = {
            alias
            for episode_slug in stale_episode_slugs
            for alias in (episode_slug, episode_slug.removeprefix("_"))
        }
        active_documents: list[dict] = []
        for document in search["docs"]:
            if not isinstance(document, dict):
                continue
            # The search corpus's own two unsupported collections (see
            # _UNSUPPORTED_GRAPH_COLLECTIONS/_UNSUPPORTED_SEARCH_LEVELS)
            # degrade the same way their graph nodes do.
            if document.get("level") in _UNSUPPORTED_SEARCH_LEVELS:
                continue
            episode_slug = document.get("episode_slug")
            if episode_slug and episode_slug not in podcast_paths:
                continue
            related_terms = document.get("related_terms")
            if isinstance(related_terms, str) and stale_episode_aliases:
                document["related_terms"] = " ".join(
                    term
                    for term in related_terms.split()
                    if not any(alias in term for alias in stale_episode_aliases)
                )
            active_documents.append(document)
        search["docs"] = active_documents
        self._check_urls(search, podcast_paths, book_paths, people_paths)
        # Heading fragments: every #fragment the corpus advertises must name
        # exactly one heading on the page it points at, so a rendered anchor
        # never has two meanings.
        expected_fragments: dict[str, dict[str, str]] = {}
        for document in search["docs"]:
            if not isinstance(document.get("url"), str):
                base.fail("wiki search document is malformed", SEARCH_SOURCE_PATH)
            parsed_url = urlsplit(document["url"])
            if not parsed_url.fragment:
                continue
            route_match = _FRAGMENT_ROUTE.fullmatch(parsed_url.path)
            if route_match is None:
                base.fail("wiki search fragment route is outside the projection", document["url"])
            slug = route_match.group(1)
            segment_title = builder._string(
                document.get("segment_title"),
                field="wiki search segment title",
                maximum=1_000,
            )
            by_title = expected_fragments.setdefault(slug, {})
            previous = by_title.get(segment_title.casefold())
            if previous is not None and previous != parsed_url.fragment:
                base.fail("wiki search heading fragment is ambiguous", document["url"])
            by_title[segment_title.casefold()] = parsed_url.fragment
        return search, expected_fragments

    def _page_record(
        self,
        checkout: ImmutableCheckout,
        path: PurePosixPath,
        metadata: dict,
        body: str,
        checksum: str,
        title_to_slug: dict[str, str],
        podcast_paths: dict[str, str],
        book_paths: dict[str, str],
        people_paths: dict[str, str],
        expected_fragments: dict[str, dict[str, str]],
    ) -> dict:
        relative = path.as_posix()
        slug = path.stem
        tags = builder._safe_key_list(metadata.get("tags"), field="wiki tag")
        blocks = builder._body_blocks(body, preserve_links=True)
        fragments = expected_fragments.get(slug, {})
        rendered_fragments: set[str] = set()
        for block in blocks:
            if block["kind"] != "heading":
                continue
            expected_fragment = fragments.get(block["text"].casefold())
            if expected_fragment:
                block["id"] = expected_fragment
            if block["id"] in fragments.values():
                rendered_fragments.add(block["id"])
        all_fragments = sorted(set(fragments.values()))
        unresolved_fragments = sorted(set(all_fragments) - rendered_fragments)
        heading_ids = [block["id"] for block in blocks if block["kind"] == "heading"]
        if unresolved_fragments or len(heading_ids) != len(set(heading_ids)):
            base.fail("wiki heading fragment does not resolve uniquely", relative)
        return {
            "slug": slug,
            "public_path": f"/wiki/{slug}",
            "title": builder._title_from_record(metadata, slug),
            "summary": builder._string(
                metadata.get("summary"), field="wiki summary", maximum=4_000, optional=True
            ),
            "tags": tags,
            "blocks": blocks,
            "fragment_ids": all_fragments,
            "unresolved_fragment_ids": unresolved_fragments,
            "relations": builder._wiki_relations(
                body,
                title_to_slug,
                podcast_paths,
                book_paths,
                people_paths,
            ),
            "source_path": relative,
            # The reviewed build forms provenance from the repository URL and
            # stores the URL-normalized name; passing the reviewed constant
            # keeps source_url identical to the reviewed records.
            "provenance": builder._provenance(
                repository=builder.WIKI_REPOSITORY,
                revision=checkout.commit_sha,
                source_path=relative,
                source_key=slug,
                checksum=checksum,
            ),
        }

    def _singleton_item(self, checkout, source_path: str, kind: str, record: dict) -> SourceItem:
        return SourceItem(
            key=SINGLETON_ITEM_KEYS[kind],
            path=source_path,
            data={
                "content_kind": kind,
                "record": record,
                "stable_key": kind,
                "public_path": SINGLETON_PUBLIC_PATHS[kind],
                "source_path": source_path,
                "checksum": _derived_checksum(base.checksum_of(checkout, source_path), record),
            },
        )

    def _asset_item(self, checkout) -> SourceItem:
        declared: dict[str, str] = {}
        for source_path in builder.WIKI_PUBLIC_ASSETS:
            payload = builder._read_bytes(
                base.snapshot_path(checkout, source_path), maximum=8 * 1024 * 1024
            )
            if not payload.startswith(_PNG_SIGNATURE):
                base.fail("wiki PNG asset signature mismatch", source_path)
            relative = source_path.removeprefix("assets/")
            declared[f"{ASSET_PUBLIC_PREFIX}{relative}"] = builder._sha256_bytes(payload)
        record = {"wiki_assets": declared}
        source_checksum = builder._sha256_bytes("".join(declared.values()).encode())
        return SourceItem(
            key=SINGLETON_ITEM_KEYS[ASSET_KIND],
            path="wiki_assets",
            data={
                "content_kind": ASSET_KIND,
                "record": record,
                "stable_key": ASSET_KIND,
                "public_path": SINGLETON_PUBLIC_PATHS[ASSET_KIND],
                "source_path": "assets/",
                "checksum": _derived_checksum(source_checksum, record),
            },
        )

    def _check_urls(self, payload, podcast_paths, book_paths, people_paths) -> None:
        allowed = (
            set(podcast_paths.values()) | set(book_paths.values()) | set(people_paths.values())
        )
        for document in payload.get("nodes", []) + payload.get("docs", []):
            url = document.get("url", "")
            if url and not (url.startswith("/wiki/") or url == "/wiki/search" or url in allowed):
                base.fail("wiki document URL is outside the public projection", str(url))


register_parser(CONTENT_KIND, PodwikiParser())
