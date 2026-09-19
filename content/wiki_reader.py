"""The wiki page read model, over the shared knowledge base app.

Wiki pages are ``community_base.knowledge_base.KnowledgeBasePage`` rows in the
``wiki`` section, written by the ``dtc-podwiki`` parser (D7.1). The package app
owns the row, the title, the summary and the stored ``public_path``, which is
the ``/wiki/<slug>`` path the page has always served at; everything else a wiki
page is -- its parsed blocks, its tags, its relations and its heading fragment
ids -- is the site's own record, which the package stores and never interprets.

The graph, the search corpus and the declared asset paths are not pages. They
stay ``content.catalogue`` singletons, and the knowledge graph composition over
them stays in ``content.wiki_content``.

A database with no synced wiki publishes nothing: the hub renders empty and
every wiki detail route 404s. A database failure raises rather than resolving to
an empty catalogue that a retry would then serve from cache (ARC-01).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from community_base.content_sync.models import ContentSource as EngineContentSource
from community_base.knowledge_base import hierarchy
from community_base.knowledge_base.models import SECTION_WIKI, KnowledgeBasePage
from django.db.models import Count, Max

#: The sync source whose pages publish the wiki. A disabled source publishes
#: nothing, the way a disabled source empties every other synced collection.
WIKI_SOURCE_SLUG = "dtc-podwiki"

#: One published wiki page, exactly as the parser stored it. The pages read
#: these as mappings because that is what a wiki record is: blocks, tags and
#: relations rather than a shape worth naming a dataclass over.
Record = dict[str, Any]


def wiki_sync_stamp() -> tuple[int, str]:
    """A cheap stamp that moves whenever the published wiki pages could have.

    The same contract as ``content.catalogue.synced_stamp``: it changes exactly
    when a sync writes the wiki pages, so a cached read follows a sync instead
    of outliving it. A database failure raises, so an outage can never enter the
    cache below disguised as an empty catalogue.
    """

    stamp = _published_pages().aggregate(total=Count("id"), latest=Max("updated_at"))
    return (int(stamp["total"] or 0), str(stamp["latest"] or ""))


def _published_pages():
    """The published wiki pages of the enabled source.

    Ownership is the page's ``source`` foreign key. C7.9c (community-base
    v0.5.0) gave ``source_content_id`` back its documented meaning -- the
    item's own ``content_id`` from the content format -- and moved the source
    it used to hold into the key that names it.
    """

    source_ids = EngineContentSource.objects.filter(
        slug=WIKI_SOURCE_SLUG, is_enabled=True
    ).values_list("id", flat=True)
    return hierarchy.published_pages(SECTION_WIKI).filter(source_id__in=source_ids)


def _record(page: KnowledgeBasePage) -> Record:
    """One page as the hub, the detail page and the feeds read it.

    The stored columns win over the record they are also written into: the
    public path a reader follows is the page's own ``public_path``, not a copy
    of it inside the site's metadata.
    """

    record = dict(page.record) if isinstance(page.record, dict) else {}
    record.update(
        {
            "slug": page.slug,
            "public_path": page.get_absolute_url(),
            "title": page.title,
            "summary": page.summary,
            "source_path": page.source_path or "",
        }
    )
    return record


@lru_cache(maxsize=2)
def _wiki_state(stamp: tuple[int, str]) -> tuple[Record, ...]:
    """The published wiki pages of exactly one synced state, A-Z.

    The query is bound to the stamp the cache key names, so a warmed answer
    cannot outlive the rows it was read from. A zero count is an absent source
    or an empty one -- an empty wiki, not a failure -- so it answers without
    reading rows.

    The row table carries no order column: the A-Z order the hub pages through
    is a catalogue fact, so the reader derives it from the records themselves.
    """

    if not stamp[0]:
        return ()
    pages = [_record(page) for page in _published_pages()]
    pages.sort(key=lambda record: (str(record["title"]).casefold(), str(record["slug"])))
    return tuple(pages)


def wiki_pages() -> tuple[Record, ...]:
    """The wiki catalogue, in the A-Z order the hub pages through."""

    return _wiki_state(wiki_sync_stamp())


def wiki_page(slug: str) -> Record | None:
    """One wiki page, or ``None`` when the wiki does not publish it."""

    return next((record for record in wiki_pages() if record.get("slug") == slug), None)


__all__ = ["Record", "wiki_page", "wiki_pages", "wiki_sync_stamp"]
