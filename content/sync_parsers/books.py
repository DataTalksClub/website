"""Books parser: book pages from the structured content repository.

The record rules are the projection builder's preferred-mode book rules
(``_main_records``); the frozen corpus-wide inventory assertions stay with the
pinned-corpus gates (``verify_dtc_content``, projection CI), so the parser
follows the repository across commits.
"""

from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from scripts import build_public_projection as builder

from . import base

SOURCE_SLUG = "dtc-content"
CONTENT_KIND = "book"
REPOSITORY = "DataTalksClub/content"
BOOKS_ROOT = "books"


class BooksParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        base.activate(checkout)
        items = []
        for relative in checkout.files():
            path = PurePosixPath(str(relative))
            if path.parts[:1] != (BOOKS_ROOT,) or path.suffix != ".yaml":
                continue
            if path.name.startswith("_"):
                continue
            items.append(self._item(checkout, path))
        items.sort(key=lambda item: item.key)
        return items

    def upsert(self, item, source, media):
        record = item.data["record"]
        image = record["image_source"]
        if image and not image.startswith(("http://", "https://")):
            media.upload(base.active_checkout(), image.lstrip("/"), source)
        document, action = base.upsert_document(
            source,
            content_kind=CONTENT_KIND,
            stable_key=record["slug"],
            slug=record["slug"],
            title=record["title"],
            summary=record["summary"],
            public_path=record["public_path"],
            source_path=str(item.path),
            checksum=item.data["checksum"],
            record=record,
        )
        return UpsertResult(document, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug != SOURCE_SLUG:
            return 0
        return base.delete_missing(source, CONTENT_KIND, seen_keys)

    def _item(self, checkout, path) -> SourceItem:
        relative = path.as_posix()
        checksum = base.checksum_of(checkout, relative)
        raw = builder._load_yaml(base.snapshot_path(checkout, relative))
        if not isinstance(raw, dict):
            base.fail("book record rejected", relative)
        slug = builder._safe_key(raw.get("slug"), field="book slug")
        legacy_path = builder._string(raw.get("legacy_path"), field="book path", maximum=500)
        if legacy_path != f"/books/{slug}.html":
            base.fail("book route mismatch", relative)
        raw_links = raw.get("links")
        if raw_links is not None and not isinstance(raw_links, list):
            base.fail("book links rejected", relative)
        links = []
        for link in raw_links or []:
            if not isinstance(link, dict):
                base.fail("book link rejected", relative)
            links.append(
                {
                    "label": builder._string(
                        link.get("text") or "Book link", field="book link label"
                    ),
                    "url": builder._safe_url(
                        link.get("link") or link.get("list"),
                        field="book link",
                        optional=False,
                    ),
                }
            )
        record = {
            "slug": slug,
            "public_path": f"/books/{slug}.html",
            "title": builder._title_from_record(raw, slug),
            "description": builder._string(
                raw.get("description"), field="book description", maximum=4_000, optional=True
            ),
            "summary": builder._string(
                raw.get("summary"), field="book summary", maximum=100_000, optional=True
            ),
            "authors": builder._string_list(raw.get("authors"), field="book author", maximum=300),
            "published": builder._string(
                raw.get("start"), field="book date", maximum=50, optional=True
            ),
            "links": links,
            "archive": builder._book_archive(raw.get("archive"), source_name=path.name),
            "image_source": builder._string(
                raw.get("image") or raw.get("cover"),
                field="book image",
                maximum=500,
                optional=True,
            ),
            "provenance": builder._provenance(
                repository=REPOSITORY,
                revision=checkout.commit_sha,
                source_path=relative,
                source_key=slug,
                checksum=checksum,
            ),
        }
        return SourceItem(key=slug, path=relative, data={"record": record, "checksum": checksum})


register_parser(CONTENT_KIND, BooksParser())
