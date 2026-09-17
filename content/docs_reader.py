"""The public documentation read model, over the shared knowledge base app.

Pages are ``community_base.knowledge_base.KnowledgeBasePage`` rows in the
``docs`` section, written by the ``dtc-docs`` parser. The package app owns the
storage and the hierarchy: the tree comes from the stored ``parent`` links
through ``community_base.knowledge_base.hierarchy``, and each page's public URL
is the ``public_path`` the parser stored, so every documentation path is the one
the source file has always served at.

What stays here is the site's own: the record shape the templates read, the
documentation assets under ``content/docs_assets/``, and the presentation and
routes in ``content.docs_presentation`` and ``content.review_views``. Rendering
is the site's too, but it runs at sync time -- see ``content.docs_rendering`` --
and the HTML it produced is stored on the page.

A database with no synced documentation publishes nothing: the hub renders
empty and every documentation route 404s. That is the normal state before a
sync has run, not a failure. A database failure raises rather than resolving to
an empty read model that a retry would then serve from cache (ARC-01).
"""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from community_base.content_sync.models import ContentSource as EngineContentSource
from community_base.knowledge_base import hierarchy
from community_base.knowledge_base.models import SECTION_DOCS, KnowledgeBasePage
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Count, Max

from .docs_rendering import DOCS_ROOT_PATH

DOCS_ASSET_ROOT = Path(__file__).with_name("docs_assets")
#: The community_base sync source whose pages publish the documentation.
#: One source, so the read path never has to choose between two of them.
DOCS_ENGINE_SOURCE_SLUG = "dtc-docs"
DOCS_CONTENT_KIND = "docs"
#: The staged release that still publishes the docs until the pipeline retires;
#: ``scripts/prod/import_docs.py`` shares this name with the synced source.
DOCS_SOURCE_STABLE_ID = DOCS_ENGINE_SOURCE_SLUG
DOCS_SOURCE_REVISION = "3f23e006ffdaa498bbc69697408853b6f5eb37dc"
DOCS_SEARCH_URL = "https://github.com/DataTalksClub/docs/search"
DOCS_ASSET_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/svg+xml"})


@dataclass(frozen=True, slots=True)
class DocsNavigationItem:
    """One immutable page position in the documentation hierarchy."""

    page: Mapping[str, Any]
    children: tuple[DocsNavigationItem, ...]

    @property
    def public_path(self) -> str:
        return str(self.page["public_path"])

    @property
    def title(self) -> str:
        return str(self.page["title"])

    @property
    def description(self) -> str:
        return str(self.page.get("description") or "")


@dataclass(frozen=True, slots=True)
class DocsNavigationTree:
    """The stored hierarchy and its deterministic depth-first reading order."""

    root: DocsNavigationItem
    preorder: tuple[DocsNavigationItem, ...]
    documents: tuple[DocsNavigationItem, ...]
    by_path: Mapping[str, DocsNavigationItem]


def _navigation_error(code: str, source_path: str) -> ImproperlyConfigured:
    return ImproperlyConfigured(f"Docs navigation {code}: {source_path or '<unknown>'}")


def _page_record(page: KnowledgeBasePage, by_pk: Mapping[int, KnowledgeBasePage]) -> dict[str, Any]:
    """One documentation page, as the navigation and the templates read it.

    The columns the package app owns -- title, summary, the stored public path,
    the stored rendered HTML -- come from the row; the source's own front
    matter, which is documentation-shaped and has no column, comes from the
    record the parser stored beside it.
    """

    record = page.record if isinstance(page.record, dict) else {}
    parent = by_pk.get(page.parent_id) if page.parent_id is not None else None
    parent_path = parent.get_absolute_url() if parent is not None else None
    headings = record.get("headings")
    return {
        "public_path": page.get_absolute_url(),
        "source_path": page.source_path or "",
        "title": page.title,
        "description": page.summary,
        "body": page.body,
        "body_html": page.body_html,
        "headings": tuple(heading for heading in headings if isinstance(heading, dict))
        if isinstance(headings, list)
        else (),
        "edit_url": str(record.get("edit_url") or ""),
        "body_sha256": str(record.get("body_sha256") or ""),
        "parent": record.get("parent"),
        # The stored parent link is the one hierarchy authority; the record's
        # ``parent`` is only the front-matter title it was resolved from. The
        # documentation root page itself has no parent, which is the one null.
        "parent_path": parent_path,
        "grand_parent": record.get("grand_parent"),
        "grand_parent_path": record.get("grand_parent_path"),
        "nav_order": record.get("nav_order"),
        "has_children": bool(record.get("has_children")),
        "has_toc": bool(record.get("has_toc", True)),
        "permalink": record.get("permalink"),
    }


def docs_sync_stamp() -> tuple[int, str]:
    """A cheap stamp that moves whenever the published documentation could have.

    One aggregate, with the same contract as ``content.catalogue.active_release_id``:
    it changes exactly when a sync writes the documentation pages, so a cached read
    follows a sync instead of outliving it. A database failure raises, so an outage
    can never enter a cache below disguised as an empty corpus.
    """

    stamp = _published_pages().aggregate(total=Count("id"), latest=Max("updated_at"))
    return (int(stamp["total"] or 0), str(stamp["latest"] or ""))


def _published_pages():
    """The published documentation pages of the enabled source.

    A disabled source publishes nothing, the way a disabled source empties every
    other synced collection.
    """

    source_ids = EngineContentSource.objects.filter(
        slug=DOCS_ENGINE_SOURCE_SLUG, is_enabled=True
    ).values_list("id", flat=True)
    return hierarchy.published_pages(SECTION_DOCS).filter(source_content_id__in=source_ids)


def _published_records() -> tuple[list[KnowledgeBasePage], dict[int, dict[str, Any]]]:
    rows = list(_published_pages())
    by_pk = {page.pk: page for page in rows}
    return rows, {page.pk: _page_record(page, by_pk) for page in rows}


@lru_cache(maxsize=2)
def _docs_state(stamp: tuple[int, str]) -> dict[str, Any]:
    """The published pages and their declared assets of exactly one state.

    The query is bound to the stamp the cache key names, so a warmed answer
    cannot outlive the rows it was read from: a sync changes the stamp, and the
    next read rebuilds the state instead of serving stale pages. A zero count is
    an absent source or an empty one -- an empty documentation corpus, not a
    failure -- so it answers without reading rows.

    Pages come back in the stored ``(section, slug)`` order, which is the source
    file path; the reading order the hub and Previous/Next use is the tree's,
    built separately so a listing never depends on a complete hierarchy.
    """

    if not stamp[0]:
        return {"pages": (), "assets": ()}
    rows, records = _published_records()
    pages = tuple(MappingProxyType(records[row.pk]) for row in rows)
    return {"pages": pages, "assets": _declared_assets(rows)}


@lru_cache(maxsize=2)
def _docs_tree(stamp: tuple[int, str]) -> DocsNavigationTree | None:
    """Turn the package's validated hierarchy into the tree the templates read.

    ``community_base.knowledge_base.hierarchy`` resolves and validates the
    parent links; this only re-expresses the result in the record shape the
    documentation templates and ``content.docs_presentation`` read, keyed by
    public path. A corpus with no root page has no tree, which is what an
    un-synced database is.
    """

    if not stamp[0]:
        return None
    _rows, records = _published_records()
    package_tree = hierarchy.navigation_tree(SECTION_DOCS)
    # A page the enabled source does not publish is not in the read model, so it
    # is not in the tree either.
    roots = [item for item in package_tree.roots if item.page.pk in records]
    root_item = next(
        (item for item in roots if records[item.page.pk]["public_path"] == DOCS_ROOT_PATH),
        None,
    )
    if root_item is None:
        return None
    if len(roots) != 1:
        stray = next(item for item in roots if item is not root_item)
        raise _navigation_error("orphan", str(records[stray.page.pk]["source_path"]))

    by_path: dict[str, DocsNavigationItem] = {}

    def build(item) -> DocsNavigationItem:
        record = records[item.page.pk]
        built = DocsNavigationItem(
            page=MappingProxyType(dict(record)),
            children=tuple(build(child) for child in item.children if child.page.pk in records),
        )
        by_path[str(record["public_path"])] = built
        return built

    root = build(root_item)
    preorder: list[DocsNavigationItem] = []

    def visit(item: DocsNavigationItem) -> None:
        preorder.append(item)
        for child in item.children:
            visit(child)

    visit(root)
    ordered = tuple(preorder)
    return DocsNavigationTree(
        root=root,
        preorder=ordered,
        documents=ordered[1:],
        by_path=MappingProxyType(dict(by_path)),
    )


def _asset_file(source_path: str) -> Path | None:
    """Resolve a declared asset path without allowing traversal or symlinks."""

    if not source_path.startswith("assets/") or "\\" in source_path:
        return None
    relative = source_path.removeprefix("assets/")
    raw_parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        return None
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    path = DOCS_ASSET_ROOT / Path(*parts)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        path.resolve().relative_to(DOCS_ASSET_ROOT.resolve())
    except ValueError:
        return None
    return path


def _declared_assets(rows: list[KnowledgeBasePage]) -> tuple[dict[str, Any], ...]:
    """The asset records the published pages declare, sorted by public path.

    A page's record names the images its body references; the asset bytes
    themselves stay the design files checked in under ``content/docs_assets/``,
    so a record's size and digest are read from the file it names. An asset
    whose file is missing publishes nothing: the route would refuse it anyway.
    """

    declared: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = row.record if isinstance(row.record, dict) else {}
        images = record.get("images")
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, str) or not image.startswith("assets/"):
                continue
            public_path = f"/docs/assets/{image.removeprefix('assets/')}"
            if public_path in declared:
                continue
            path = _asset_file(image)
            if path is None:
                continue
            payload = path.read_bytes()
            declared[public_path] = {
                "public_path": public_path,
                "source_path": image,
                "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
    return tuple(declared[public_path] for public_path in sorted(declared))


def docs_pages() -> tuple[dict[str, Any], ...]:
    """Every published documentation page, in the tree's own reading order."""

    return tuple(dict(page) for page in _docs_state(docs_sync_stamp())["pages"])


def docs_assets() -> tuple[dict[str, Any], ...]:
    """Every documentation asset the published pages declare."""

    return tuple(dict(asset) for asset in _docs_state(docs_sync_stamp())["assets"])


def docs_asset_path(asset: str) -> tuple[Path, str] | None:
    """Return a checked-in docs image and its declared media type for one URL segment."""

    if not asset or asset.startswith("/") or "\\" in asset:
        return None
    raw_parts = asset.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        return None
    parts = PurePosixPath(asset).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    public_path = f"/docs/assets/{PurePosixPath(asset).as_posix()}"
    for record in docs_assets():
        if record["public_path"] != public_path:
            continue
        path = _asset_file(str(record["source_path"]))
        if path is None:
            return None
        return path, str(record["content_type"])
    return None


def docs_page(public_path: str) -> dict[str, Any] | None:
    """One documentation page by its public path, or ``None``."""

    normalized = public_path or DOCS_ROOT_PATH
    if normalized == "/docs":
        normalized = DOCS_ROOT_PATH
    elif normalized.startswith("/docs/") and not normalized.endswith("/"):
        normalized += "/"
    tree = _docs_tree(docs_sync_stamp())
    if tree is None:
        return None
    item = tree.by_path.get(normalized)
    return dict(item.page) if item is not None else None


def docs_navigation_tree() -> DocsNavigationTree:
    """Return the stored hierarchy for the published documentation."""

    tree = _docs_tree(docs_sync_stamp())
    if tree is None:
        raise ImproperlyConfigured("Docs navigation root_missing: <unknown>")
    return tree


def docs_children(parent_path: str | None) -> tuple[dict[str, Any], ...]:
    tree = _docs_tree(docs_sync_stamp())
    if tree is None:
        return ()
    effective_parent = DOCS_ROOT_PATH if parent_path is None else parent_path
    parent = tree.by_path.get(effective_parent)
    if parent is None:
        return ()
    return tuple(dict(child.page) for child in parent.children)


def docs_breadcrumbs(page: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = [{"title": "Documentation", "public_path": DOCS_ROOT_PATH}]
    chain: list[dict[str, Any]] = []
    current = page
    tree = _docs_tree(docs_sync_stamp())
    by_path = tree.by_path if tree is not None else {}
    while current.get("parent_path"):
        parent_path = str(current["parent_path"])
        if parent_path == DOCS_ROOT_PATH:
            break
        parent_item = by_path.get(parent_path)
        if parent_item is None:
            break
        parent = dict(parent_item.page)
        chain.append(parent)
        current = parent
    for parent in reversed(chain):
        result.append({"title": str(parent["title"]), "public_path": str(parent["public_path"])})
    return tuple(result)


def docs_parent(page: Mapping[str, Any]) -> dict[str, Any]:
    """Return the explicit parent, using the Docs landing for top-level pages."""

    parent_path = page.get("parent_path")
    if parent_path is None or parent_path == DOCS_ROOT_PATH:
        return {"title": "Documentation", "public_path": DOCS_ROOT_PATH}
    parent = docs_navigation_tree().by_path.get(str(parent_path))
    if parent is None:  # The stored hierarchy makes this defensive branch unreachable.
        raise _navigation_error("orphan", str(page.get("source_path") or ""))
    return dict(parent.page)


def docs_sequential_navigation(
    page: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return adjacent detail documents in deterministic depth-first pre-order."""

    tree = _docs_tree(docs_sync_stamp())
    if tree is None:
        return None, None
    documents = tree.documents
    for index, item in enumerate(documents):
        if item.public_path == page.get("public_path"):
            previous = dict(documents[index - 1].page) if index else None
            following = dict(documents[index + 1].page) if index + 1 < len(documents) else None
            return previous, following
    return None, None


def docs_sibling_navigation(
    page: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    siblings = list(docs_children(page.get("parent_path")))
    for index, sibling in enumerate(siblings):
        if sibling["public_path"] == page.get("public_path"):
            previous = siblings[index - 1] if index else None
            following = siblings[index + 1] if index + 1 < len(siblings) else None
            return previous, following
    return None, None


def docs_navigation() -> tuple[dict[str, Any], ...]:
    """Return top-level pages in deterministic source navigation order."""

    return docs_children(None)


__all__ = [
    "DOCS_ASSET_CONTENT_TYPES",
    "DOCS_ASSET_ROOT",
    "DOCS_CONTENT_KIND",
    "DOCS_ENGINE_SOURCE_SLUG",
    "DOCS_ROOT_PATH",
    "DOCS_SEARCH_URL",
    "DOCS_SOURCE_REVISION",
    "DOCS_SOURCE_STABLE_ID",
    "DocsNavigationItem",
    "DocsNavigationTree",
    "docs_asset_path",
    "docs_assets",
    "docs_breadcrumbs",
    "docs_children",
    "docs_navigation",
    "docs_navigation_tree",
    "docs_page",
    "docs_pages",
    "docs_parent",
    "docs_sequential_navigation",
    "docs_sibling_navigation",
    "docs_sync_stamp",
]
