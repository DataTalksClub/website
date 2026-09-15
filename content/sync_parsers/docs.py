"""Docs parser: the documentation site from ``DataTalksClub/docs``.

The record rules follow the reviewed docs import the staged pipeline carried
(``scripts/prod/import_docs.py``): one published page per Jekyll page, the
pretty-permalink public path (``courses/foo.md`` serves at
``/docs/courses/foo/``), the raw markdown body so the site keeps rendering
markdown at request time, and the hierarchy front matter the navigation reads
(``parent``/``nav_order``/``has_children``/``toc``/``permalink``) resolved to
parent paths the same way the reviewed file did -- by page title within the
same sync.

The repository also carries the FAQ index pages under ``courses/``; those are
documentation pages here and stay docs pages, exactly as the staged import
treated them.
"""

import posixpath
import re
from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from scripts import build_public_projection as builder

from . import base

SOURCE_SLUG = "dtc-docs"
CONTENT_KIND = "docs"
REPOSITORY = "DataTalksClub/docs"
EDIT_BASE_URL = f"https://github.com/{REPOSITORY}/edit/main"
PAGES_ROOTS = ("activities", "courses", "general", "touch")

_MD_SUFFIX = ".md"
_INDEX_STEM = "index"


class DocsParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        base.activate(checkout)
        items = [self._item(checkout, path) for path in self._page_paths(checkout)]
        items.sort(key=lambda item: item.key)
        seen_paths: set[str] = set()
        for item in items:
            public_path = item.data["record"]["public_path"]
            if public_path in seen_paths:
                base.fail("docs pages collide on one public path", public_path)
            seen_paths.add(public_path)
        # The hierarchy front matter names parents by title; resolve the parent
        # paths in the same pass so the record never carries an unresolved name.
        paths_by_title = {}
        for item in items:
            record = item.data["record"]
            title = record["metadata"]["title"]
            if title and title not in paths_by_title:
                paths_by_title[title] = record["public_path"]
        for item in items:
            record = item.data["record"]
            metadata = record["metadata"]
            parent = metadata["parent"]
            metadata["parent_path"] = paths_by_title.get(parent, "") if parent else ""
        return items

    def upsert(self, item, source, media):
        record = item.data["record"]
        for image in record["images"]:
            media.upload(base.active_checkout(), image, source)
        document, action = base.upsert_document(
            source,
            content_kind=CONTENT_KIND,
            stable_key=record["stable_key"],
            slug=record["stable_key"],
            title=record["metadata"]["title"],
            summary=record["metadata"]["description"],
            public_path=record["public_path"],
            source_path=record["source_path"],
            checksum=item.data["checksum"],
            record=record,
        )
        return UpsertResult(document, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug != SOURCE_SLUG:
            return 0
        return base.delete_missing(source, CONTENT_KIND, seen_keys)

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
        public_path = self._public_path(path)
        record = {
            "stable_key": self._stable_key(path),
            "public_path": public_path,
            "source_path": relative,
            "body": body,
            "images": self._images(body, path.parent),
            "metadata": {
                "title": builder._string(metadata.get("title"), field="docs title", maximum=500),
                "description": builder._string(
                    metadata.get("description") or metadata.get("summary"),
                    field="docs description",
                    maximum=4_000,
                    optional=True,
                ),
                "parent": builder._string(
                    metadata.get("parent"), field="docs parent", maximum=500, optional=True
                ),
                # Resolved against the discovered pages in ``discover``.
                "parent_path": "",
                "grand_parent": "",
                "grand_parent_path": "",
                "nav_order": metadata.get("nav_order"),
                "has_children": bool(metadata.get("has_children")),
                "has_toc": bool(metadata.get("toc", True)),
                "permalink": builder._string(
                    metadata.get("permalink"), field="docs permalink", maximum=500, optional=True
                ),
                "edit_url": f"{EDIT_BASE_URL}/{relative}",
            },
            "provenance": builder._provenance(
                repository=REPOSITORY,
                revision=checkout.commit_sha,
                source_path=relative,
                source_key=self._stable_key(path),
                checksum=checksum,
            ),
        }
        return SourceItem(
            key=record["stable_key"], path=relative, data={"record": record, "checksum": checksum}
        )

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
        for match in re.finditer(r"(?:!\[[^\]]*\]\(|src=\")([^\s)\"]+)", body):
            value = match.group(1)
            if value.startswith(("http://", "https://", "/")):
                continue
            candidate = PurePosixPath(value)
            if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
                continue
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
