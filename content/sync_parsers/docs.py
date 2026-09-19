"""Docs parser: the documentation site from ``DataTalksClub/docs``.

The pages are stored in the shared ``community_base.knowledge_base`` app, in its
``docs`` section (D7.1). What the package owns is storage and hierarchy
resolution; everything documentation-shaped stays here:

- the page's stable key is its source path with the ``index`` stem dropped
  (``courses/foo/index.md`` keys as ``courses/foo``), which is also its slug.
  The corpus repeats fifteen leaf segments under different parents, so the whole
  path is the key and the tree carries the parent link separately;
- the public path is the pretty permalink the source has always served at
  (``courses/foo.md`` serves at ``/docs/courses/foo/``) and is stored on the page
  as its own ``public_path``, so no documentation URL is derived from the tree;
- the hierarchy front matter (``parent``/``nav_order``/``has_children``/``toc``/
  ``permalink``) resolves parents by page title within the same sync, the way the
  reviewed import did. A page whose front matter names no parent hangs off the
  documentation root page;
- the body is rendered here, by ``content.docs_rendering``, because this corpus
  is kramdown and Liquid rather than plain markdown. The package stores the
  result instead of re-rendering it;
- what has no column -- the edit URL, the table-of-contents flags, the declared
  images, the rendered headings -- travels in the page's ``record``.

The repository also carries the FAQ index pages under ``courses/``; those are
documentation pages here and stay docs pages, exactly as the staged import
treated them.
"""

import hashlib
import json
import posixpath
import re
from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser
from community_base.knowledge_base import sync as knowledge_base_sync
from community_base.knowledge_base.models import SECTION_DOCS

from content.docs_rendering import render_docs_markdown
from scripts import build_public_projection as builder

from . import base

SOURCE_SLUG = "dtc-docs"
CONTENT_KIND = "docs"
REPOSITORY = "DataTalksClub/docs"
EDIT_BASE_URL = f"https://github.com/{REPOSITORY}/edit/main"
PAGES_ROOTS = ("activities", "courses", "general", "touch")

_MD_SUFFIX = ".md"
_INDEX_STEM = "index"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
# The source repository writes every image through Jekyll's ``relative_url`` filter, for
# example ``{{ '/assets/images/foo.png' | relative_url }}``; unwrap it to the bare source-root
# path before scanning for image references, matching ``docs_rendering._LIQUID_RELATIVE_URL``.
_LIQUID_RELATIVE_URL = re.compile(
    r"{{\s*(['\"])(?P<path>.*?)\1\s*\|\s*relative_url\s*}}",
    re.DOTALL,
)
_IMAGE_REFERENCE = re.compile(r"(?:!\[[^\]]*\]\(|src=\")([^\s)\"]+)")


def _nav_order(value) -> int:
    """The front matter's sibling position, as the stored integer column."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _checksum_payload(item: SourceItem) -> dict:
    """Everything the stored page derives from, for change detection.

    The rendered HTML is left out on purpose: it is a pure function of the body
    and the renderer, both of which the payload already covers.
    """

    data = item.data
    return {
        key: data[key]
        for key in (
            "stable_key",
            "public_path",
            "source_path",
            "title",
            "description",
            "body",
            "nav_order",
            "parent_key",
            "record",
        )
    }


def _parents_first(items: list[SourceItem]) -> list[SourceItem]:
    """Order the items so a page's parent is always upserted before it.

    The front matter names parents by title, which is independent of the source
    file path, so sorting by key is not enough on its own.
    """

    by_key = {item.key: item for item in items}
    ordered: list[SourceItem] = []
    placed: set[str] = set()

    def place(item: SourceItem, seen: frozenset[str]) -> None:
        if item.key in placed:
            return
        if item.key in seen:
            base.fail("docs pages form a parent cycle", item.data["source_path"])
        parent_key = item.data["parent_key"]
        parent = by_key.get(parent_key) if parent_key else None
        if parent is not None:
            place(parent, seen | {item.key})
        placed.add(item.key)
        ordered.append(item)

    for item in items:
        place(item, frozenset())
    return ordered


class DocsParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        base.activate(checkout)
        items = [self._item(checkout, path) for path in self._page_paths(checkout)]
        items.sort(key=lambda item: item.key)
        seen_paths: set[str] = set()
        for item in items:
            public_path = item.data["public_path"]
            if public_path in seen_paths:
                base.fail("docs pages collide on one public path", public_path)
            seen_paths.add(public_path)
        # The hierarchy front matter names parents by title; resolve the parent
        # keys in the same pass so no item carries an unresolved name. A page
        # that names no parent, or one no page answers to, hangs off the
        # documentation root page, which is what the read model has always
        # shown it as.
        keys_by_title: dict[str, str] = {}
        for item in items:
            title = item.data["title"]
            if title and title not in keys_by_title:
                keys_by_title[title] = item.data["stable_key"]
        has_root = any(item.data["stable_key"] == _INDEX_STEM for item in items)
        for item in items:
            parent_title = item.data["parent_title"]
            parent_key = keys_by_title.get(parent_title) if parent_title else None
            if parent_key == item.data["stable_key"]:
                base.fail("docs page is its own parent", item.data["source_path"])
            if parent_key is None and has_root and item.data["stable_key"] != _INDEX_STEM:
                parent_key = _INDEX_STEM
            item.data["parent_key"] = parent_key
        # The record derives from the page's file plus every page whose title a
        # parent reference resolves to, so the change-detection checksum covers
        # the whole derived record, not just the page's own bytes.
        for item in items:
            item.data["checksum"] = builder._sha256_bytes(
                (
                    item.data["checksum"]
                    + "|"
                    + json.dumps(_checksum_payload(item), sort_keys=True, ensure_ascii=False)
                ).encode()
            )
        # The package refuses a parent it has not stored yet, so a parent's item
        # precedes every child's.
        return _parents_first(items)

    def upsert(self, item, source, media):
        data = item.data
        for image in data["record"]["images"]:
            media.upload(base.active_checkout(), image, source)
        page, action = knowledge_base_sync.upsert_page(
            source,
            section=SECTION_DOCS,
            slug=data["stable_key"],
            title=data["title"],
            summary=data["description"],
            body=data["body"],
            body_html=data["body_html"],
            parent_slug=data["parent_key"],
            nav_order=data["nav_order"],
            public_path=data["public_path"],
            record=data["record"],
            commit_sha=data["commit_sha"],
            source_path=data["source_path"],
            checksum=data["checksum"],
        )
        return UpsertResult(page, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug != SOURCE_SLUG:
            return 0
        return len(knowledge_base_sync.delete_missing(source, SECTION_DOCS, seen_slugs=seen_keys))

    def _page_paths(self, checkout) -> list[PurePosixPath]:
        pages = []
        for relative in checkout.files():
            path = PurePosixPath(str(relative))
            if path.suffix != _MD_SUFFIX:
                continue
            if path.parts[:1] not in [(root,) for root in PAGES_ROOTS]:
                continue
            if path.name.startswith("_"):
                continue
            pages.append(path)
        root_index = PurePosixPath("index.md")
        if any(path == root_index for path in checkout.files()):
            pages.append(root_index)
        return sorted(pages)

    def _item(self, checkout, path: PurePosixPath) -> SourceItem:
        relative = path.as_posix()
        checksum = base.checksum_of(checkout, relative)
        metadata, body = builder._frontmatter(base.snapshot_path(checkout, relative))
        if not metadata.get("title"):
            base.fail("docs page without a title", relative)
        stable_key = self._stable_key(path)
        body_html, headings = render_docs_markdown(body)
        record = {
            # Everything the documentation templates read that the shared page
            # has no column for. The package stores this object and never reads
            # a key of it.
            "images": self._images(body, path.parent),
            "headings": [dict(heading) for heading in headings],
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "parent": builder._string(
                metadata.get("parent"), field="docs parent", maximum=500, optional=True
            ),
            "grand_parent": "",
            "grand_parent_path": "",
            "nav_order": metadata.get("nav_order"),
            "has_children": bool(metadata.get("has_children")),
            "has_toc": bool(metadata.get("toc", True)),
            "permalink": builder._string(
                metadata.get("permalink"), field="docs permalink", maximum=500, optional=True
            ),
            "edit_url": f"{EDIT_BASE_URL}/{relative}",
        }
        data = {
            "stable_key": stable_key,
            "public_path": self._public_path(path),
            "source_path": relative,
            "title": builder._string(metadata.get("title"), field="docs title", maximum=500),
            "description": builder._string(
                metadata.get("description") or metadata.get("summary"),
                field="docs description",
                maximum=4_000,
                optional=True,
            ),
            "body": body,
            "body_html": body_html,
            "nav_order": _nav_order(metadata.get("nav_order")),
            "parent_title": record["parent"],
            # Resolved against the discovered pages in ``discover``.
            "parent_key": None,
            "record": record,
            "commit_sha": checkout.commit_sha,
            "checksum": checksum,
        }
        return SourceItem(key=stable_key, path=relative, data=data)

    @staticmethod
    def _stable_key(path: PurePosixPath) -> str:
        parts = list(path.with_suffix("").parts)
        if parts[-1] == _INDEX_STEM:
            parts.pop()
        return "/".join(parts) or _INDEX_STEM

    @staticmethod
    def _public_path(path: PurePosixPath) -> str:
        parts = [part for part in path.with_suffix("").parts if part != _INDEX_STEM]
        joined = "/".join(parts)
        return "/docs/" + joined + "/" if joined else "/docs/"

    @staticmethod
    def _images(body: str, page_dir: PurePosixPath) -> list[str]:
        seen: list[str] = []
        # The source wraps every image reference in Jekyll's ``relative_url`` filter
        # (``{{ '/assets/images/foo.png' | relative_url }}``); unwrap it first so the
        # reference scan below sees the bare source-root path it names.
        unwrapped = _LIQUID_RELATIVE_URL.sub(lambda match: match.group("path"), body)
        for match in _IMAGE_REFERENCE.finditer(unwrapped):
            value = match.group(1)
            if value.startswith(("http://", "https://")):
                continue
            candidate = PurePosixPath(value)
            if candidate.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            if value.startswith("/assets/"):
                # A checkout-root asset path -- the pretty-permalink prefix the source's
                # own ``relative_url`` filter adds -- is already checkout-relative once
                # the leading slash is dropped, so it never resolves against the page.
                key = value.removeprefix("/")
            elif value.startswith("/"):
                # Any other source-root-absolute reference names an asset outside the
                # checkout's own ``assets/`` tree; the parser cannot resolve it.
                continue
            else:
                # A relative reference resolves against the page's own directory;
                # the upload and the served asset path are checkout-relative.
                normalised = PurePosixPath(posixpath.normpath(str(page_dir / candidate)))
                if normalised.parts[:1] == ("..",):
                    base.fail("docs image reference escapes the checkout", value)
                key = normalised.as_posix()
            if key not in seen:
                seen.append(key)
        return seen


register_parser(CONTENT_KIND, DocsParser())
