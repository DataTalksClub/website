"""Articles parser: blog posts from the structured content repository.

The record rules are the projection builder's preferred-mode article rules
(``_main_records``), applied per file; the frozen corpus-wide inventory
assertions are deliberately not carried, so the parser follows the repository
across commits instead of pinning one checked count.  The staged adapter and
its ``verify_dtc_content`` contract remain the pinned-corpus gate.
"""

import re
from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from content.article_faq_format import ArticleFaqFormatError, validate_faq_pairs
from scripts import build_public_projection as builder

from . import base

SOURCE_SLUG = "dtc-content"
CONTENT_KIND = "article"
REPOSITORY = "DataTalksClub/content"
ARTICLES_ROOT = "articles"

_DATE_PREFIX = re.compile(r"^(?:\d{2}|\d{4})-\d{2}-\d{2}-(.+)$")


class ArticlesParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        base.activate(checkout)
        items = []
        for relative in checkout.files():
            path = PurePosixPath(str(relative))
            if path.parts[:1] != (ARTICLES_ROOT,) or path.suffix != ".md":
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
            summary=record["description"],
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
        match = _DATE_PREFIX.fullmatch(path.stem)
        if match is None:
            base.fail("article selection key rejected", relative)
        slug = builder._safe_key(match.group(1), field="article slug")
        metadata, body = builder._frontmatter(base.snapshot_path(checkout, relative))
        published = builder._string(
            metadata.get("date") or metadata.get("datepublished") or path.name[:10],
            field="article date",
            maximum=50,
        )
        blocks = builder._article_blocks(body, media_root=checkout.root, counters={})
        record = {
            "slug": slug,
            "public_path": f"/blog/{slug}.html",
            "title": builder._title_from_record(metadata, slug),
            "subtitle": builder._string(
                metadata.get("subtitle"), field="article subtitle", maximum=2_000, optional=True
            ),
            "description": builder._string(
                metadata.get("description"),
                field="article description",
                maximum=4_000,
                optional=True,
            ),
            "published": published,
            "authors": builder._safe_key_list(metadata.get("authors"), field="article author"),
            "blocks": blocks,
            # Frontmatter FAQ travels with the article, validated by the same
            # format rule the staged adapter applies.  The projection builder's
            # stricter accordion-position invariant is tied to its pinned
            # recovery and stays with the pinned-corpus gates, not here.
            "faq": self._faq_pairs(metadata.get("faq"), relative),
            "image_source": builder._string(
                metadata.get("image"), field="article image", maximum=500, optional=True
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

    @staticmethod
    def _faq_pairs(value, source_path: str) -> list[dict[str, str]] | None:
        if value is None:
            return None
        try:
            questions = validate_faq_pairs(value)
        except ArticleFaqFormatError:
            base.fail("article frontmatter FAQ rejected", source_path)
        return [dict(question) for question in questions]


register_parser(CONTENT_KIND, ArticlesParser())
