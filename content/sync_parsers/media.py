"""Media parser: the published site images, from the trees the reviewed build copied.

The record rules are the projection builder's ``_copy_media`` and
``_copy_people_media`` rules: every file under the editorial repository's
``images/{posts,podcast,books}`` trees -- whether or not a published record
still points at it, because legacy assets are deliberately kept serving --
plus one image per published profile.  Each record carries the sha256 the
media store verifies the served bytes against, so the ``/images/`` route can
read these rows without weakening its fail-closed checksum contract.

The frozen corpus-wide inventory stays with the pinned-corpus gates; the
parser follows the repositories across commits.  The profile pictures ride
with the people parser (it already reads and allowlists the ``picture``
frontmatter); this module owns the record shape, the byte rules and the
upsert for both trees.
"""

import re
from pathlib import PurePosixPath
from typing import Any

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from scripts import build_public_projection as builder

from . import base

MEDIA_KIND = "media"
EDITORIAL_SOURCE_SLUG = "dtc-content"
PEOPLE_REPOSITORY = "DataTalksClub/datatalksclub.github.io"
EDITORIAL_REPOSITORY = "DataTalksClub/content"

#: The editorial tree prefixes the reviewed build copied media from.
EDITORIAL_MEDIA_PREFIXES = (
    "images/posts/",
    "images/podcast/",
    "images/books/",
)
#: Extension to recorded content type, per ``_copy_media``.
MEDIA_CONTENT_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}
#: A profile picture never publishes as SVG, per ``_copy_people_media``.
PEOPLE_CONTENT_TYPES = {key: value for key, value in MEDIA_CONTENT_TYPES.items() if key != ".svg"}

#: The reviewed build reads media objects through a bounded read; the same
#: ceiling travels with the parser.
MAX_MEDIA_BYTES = 16 * 1024 * 1024

#: The reviewed build's unsafe-SVG scan, verbatim.
_UNSAFE_SVG = re.compile(
    r"<script\b|<style\b|<!doctype|<!entity|expression\s*\(|"
    r"url\s*\(\s*['\"]?(?:https?:|//|data:)|"
    r"\bon[a-z]+\s*=|\b(?:href|src)\s*=\s*['\"](?:https?:|//|data:)"
)


def media_payload_defect(suffix: str, payload: bytes) -> str | None:
    """The reviewed media signature rules, applied to one file's bytes."""

    if suffix in {".jpg", ".jpeg"} and not (
        payload.startswith(b"\xff\xd8\xff") and payload.endswith(b"\xff\xd9")
    ):
        return "JPEG media signature mismatch"
    if suffix == ".png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG media signature mismatch"
    if suffix == ".gif" and not payload.startswith((b"GIF87a", b"GIF89a")):
        return "GIF media signature mismatch"
    if suffix == ".svg":
        try:
            svg = payload.decode("utf-8")
        except UnicodeDecodeError:
            return "SVG media is not UTF-8"
        lowered = svg.casefold()
        if "<svg" not in lowered[:1000] or _UNSAFE_SVG.search(lowered):
            return "unsafe SVG media"
    return None


def media_item(
    checkout,
    relative: str,
    *,
    repository: str,
    content_types: dict[str, str],
) -> SourceItem:
    """One media record for one image file in the active checkout.

    The bytes are read once here: the payload rules are a parse-time refusal,
    and the digest they travel with is both the record's serving checksum and
    the row checksum an unchanged file is recognised by.
    """

    suffix = PurePosixPath(relative).suffix.lower()
    if suffix not in content_types:
        base.fail("media source path is outside the allowlist", relative)
    payload = checkout.read_bytes(relative)
    if len(payload) > MAX_MEDIA_BYTES:
        base.fail("media source exceeds the reviewed size bound", relative)
    defect = media_payload_defect(suffix, payload)
    if defect is not None:
        base.fail(defect, relative)
    record: dict[str, Any] = {
        "record_key": relative,
        "slug": relative,
        "public_path": f"/{relative}",
        "content_type": content_types[suffix],
        "provenance": builder._provenance(
            repository=repository,
            revision=checkout.commit_sha,
            source_path=relative,
            source_key=relative,
            checksum=builder._sha256_bytes(payload),
        ),
    }
    return SourceItem(
        key=relative,
        path=relative,
        data={"kind": MEDIA_KIND, "record": record, "checksum": record["provenance"]["checksum"]},
    )


def is_media_item(item: SourceItem) -> bool:
    return item.data.get("kind") == MEDIA_KIND


def upsert_media_item(item: SourceItem, source, media) -> UpsertResult:
    """Write one media record; the bytes also leave through the engine's store."""

    record = item.data["record"]
    media.upload(base.active_checkout(), record["record_key"], source)
    document, action = base.upsert_document(
        source,
        content_kind=MEDIA_KIND,
        stable_key=record["record_key"],
        slug=record["slug"],
        title=record["record_key"],
        summary="",
        public_path=record["public_path"],
        source_path=record["provenance"]["source_path"],
        checksum=item.data["checksum"],
        record=record,
    )
    return UpsertResult(document, action)


class MediaParser:
    def discover(self, checkout, source):
        if source.slug != EDITORIAL_SOURCE_SLUG:
            return []
        base.activate(checkout)
        found = sorted(
            str(relative)
            for relative in checkout.files()
            if str(relative).startswith(EDITORIAL_MEDIA_PREFIXES)
        )
        return [
            media_item(
                checkout,
                relative,
                repository=EDITORIAL_REPOSITORY,
                content_types=MEDIA_CONTENT_TYPES,
            )
            for relative in found
        ]

    def upsert(self, item: SourceItem, source, media) -> UpsertResult:
        return upsert_media_item(item, source, media)

    def soft_delete_missing(self, seen_keys: set[str], source) -> int:
        if source.slug != EDITORIAL_SOURCE_SLUG:
            return 0
        return base.delete_missing(source, MEDIA_KIND, seen_keys)


register_parser(MEDIA_KIND, MediaParser())
