"""Articles parser: blog posts from the structured content repository.

The record rules are the projection builder's preferred-mode article rules
(``_main_records``), applied per file; the frozen corpus-wide inventory
assertions are deliberately not carried, so the parser follows the repository
across commits instead of pinning one checked count.  (The staged adapter and
its ``verify_dtc_content`` pinned-corpus gate were deleted in ``1cd976a4``.)
"""

import re
from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from content import projection_helpers as builder
from content.article_faq_format import ArticleFaqFormatError, validate_faq_pairs

from . import base

SOURCE_SLUG = "dtc-content"
CONTENT_KIND = "article"
REPOSITORY = "DataTalksClub/content"
ARTICLES_ROOT = "articles"

_DATE_PREFIX = re.compile(r"^(?:\d{2}|\d{4})-\d{2}-\d{2}-(.+)$")
#: An article's picture is chosen like a Substack post's: the frontmatter
#: ``cover:`` when the author picked one, otherwise the first image the body
#: publishes.  Only an article with neither names a generated ``preview:`` (the
#: banner-generator ``dtc-article`` card): the square list thumbnail, and beside
#: it the social card, the same file with ``-og`` before the suffix.
_PREVIEW_PATH = re.compile(r"^images/posts/[A-Za-z0-9._/-]+\.(?:png|jpg|jpeg)$")
_COVER_PATH = re.compile(r"^images/posts/[A-Za-z0-9._/-]+\.(?:png|jpg|jpeg|gif|webp)$")
#: A body image, Markdown ``![alt](src)`` or HTML ``<img src>``, in document order.
_BODY_IMAGE = re.compile(
    r"!\[[^\]]*\]\(\s*<?(?P<markdown>[^)\s>]+)|<img\b[^>]*?\bsrc\s*=\s*[\"'](?P<html>[^\"']+)",
    re.IGNORECASE,
)


def _picture(checkout, relative: str) -> dict:
    picture: dict[str, object] = {"source": relative}
    dimensions = builder._image_dimensions(checkout.root / relative)
    if dimensions is not None:
        picture["width"], picture["height"] = dimensions
    return picture


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
        for picture in (
            record["card_source"],
            record["preview_source"],
            record["preview_og_source"],
        ):
            if picture:
                media.upload(base.active_checkout(), picture, source)
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
        faq = self._faq_pairs(metadata.get("faq"), relative)
        card = self._card(metadata.get("cover"), body, checkout, relative)
        preview, preview_og = self._preview(metadata.get("preview"), checkout, relative)
        if card and preview:
            # A generated card stands in only where the article has no picture of its
            # own; next to one it would be derived data written by hand.
            base.fail("article preview beside a cover or body image", relative)
        if faq is None and any(block.get("kind") == "faq" for block in blocks):
            base.fail("article FAQ marker without frontmatter FAQ", relative)
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
            "faq": faq,
            "sponsor": self._sponsor(metadata.get("sponsor"), relative),
            "image_source": builder._string(
                metadata.get("image"), field="article image", maximum=500, optional=True
            ),
            "card_source": card["source"] if card else None,
            "card_width": card.get("width") if card else None,
            "card_height": card.get("height") if card else None,
            "preview_source": preview,
            "preview_og_source": preview_og,
            "provenance": builder._provenance(
                repository=REPOSITORY,
                revision=checkout.commit_sha,
                source_path=relative,
                source_key=slug,
                checksum=checksum,
            ),
        }
        # The blocks carry the width and height read from each embedded image
        # file, so an image replaced in place changes the row without touching
        # the article file: the change checksum covers the derived record.
        return SourceItem(
            key=slug,
            path=relative,
            data={"record": record, "checksum": base.record_checksum(checksum, record)},
        )

    @staticmethod
    def _faq_pairs(value, source_path: str) -> list[dict[str, str]] | None:
        if value is None:
            return None
        try:
            questions = validate_faq_pairs(value)
        except ArticleFaqFormatError:
            base.fail("article frontmatter FAQ rejected", source_path)
        return [dict(question) for question in questions]

    @staticmethod
    def _card(value, body: str, checkout, source_path: str) -> dict | None:
        """The article's picture: the frontmatter ``cover:``, else the first body image.

        ``cover`` is optional and must name a file in the checkout.  Without it the
        first image the body publishes is the picture; a remote image is not
        published, so it is never the picture.  ``None`` when there is neither.
        The pixel size travels with it so a list can set a wide or tall picture
        whole instead of cropping it.
        """

        if value is not None:
            if not isinstance(value, str) or not _COVER_PATH.fullmatch(value) or ".." in value:
                base.fail("article cover rejected", source_path)
            if not (checkout.root / value).is_file():
                base.fail("article cover file missing", source_path)
            return _picture(checkout, value)
        for match in _BODY_IMAGE.finditer(body):
            reference = (match.group("markdown") or match.group("html")).strip()
            if not reference.startswith("/") or reference.startswith("//"):
                continue
            relative = reference.lstrip("/")
            if ".." in relative or not (checkout.root / relative).is_file():
                continue
            return _picture(checkout, relative)
        return None

    @staticmethod
    def _preview(value, checkout, source_path: str) -> tuple[str | None, str | None]:
        """Validate the frontmatter ``preview:`` path and derive its social card.

        ``preview`` names the square list thumbnail; the social card is the same
        file with ``-og`` before the suffix, never written by hand.  Both files
        must be in the checkout, so a published record never points at a missing
        picture.
        """

        if value is None:
            return None, None
        if not isinstance(value, str) or not _PREVIEW_PATH.fullmatch(value) or ".." in value:
            base.fail("article preview rejected", source_path)
        preview_path = PurePosixPath(value)
        social = preview_path.with_name(f"{preview_path.stem}-og{preview_path.suffix}").as_posix()
        for path in (value, social):
            if not (checkout.root / path).is_file():
                base.fail("article preview file missing", source_path)
        return value, social

    @staticmethod
    def _sponsor(value, source_path: str) -> dict[str, str] | None:
        """Validate the frontmatter ``sponsor: {name, url}`` disclosure.

        A sponsored post names its sponsor here rather than in a hand-written
        note inside the body, so every sponsored article carries the same
        disclosure, drawn by the page in the same place.
        """

        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"name", "url"}:
            base.fail("article sponsor rejected", source_path)
        try:
            name = builder._string(value["name"], field="article sponsor name", maximum=100)
            url = builder._safe_url(value["url"], field="article sponsor url", optional=False)
        except builder.ProjectionBuildError:
            base.fail("article sponsor rejected", source_path)
        if not name.strip() or not url.startswith("https://"):
            base.fail("article sponsor rejected", source_path)
        return {"name": name.strip(), "url": url}


register_parser(CONTENT_KIND, ArticlesParser())
