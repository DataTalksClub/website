"""People parser: public profiles from the legacy main-site repository.

The record rules are the projection builder's ``_people`` rules; the frozen
source-inventory assertion is deliberately not carried, so the parser follows
the repository across commits instead of pinning one checked count.
"""

import re
from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from scripts import build_public_projection as builder

from . import base
from .media import (
    MEDIA_KIND,
    PEOPLE_CONTENT_TYPES,
    PEOPLE_REPOSITORY,
    is_media_item,
    media_item,
    upsert_media_item,
)

SOURCE_SLUG = "dtc-main-site"
CONTENT_KIND = "people"
REPOSITORY = "DataTalksClub/datatalksclub.github.io"
PEOPLE_DIRECTORY = "_people"

_ALLOWED_METADATA = {
    "bio_short",
    "github",
    "layout",
    "linkedin",
    "picture",
    "short",
    "title",
    "twitter",
    "web",
}
_PICTURE_PATTERN = re.compile(r"images/authors/[A-Za-z0-9._()-]+\.(?:gif|jpe?g|png)")
# The accepted corpus carries exactly one legacy picture path with a stray
# space; it stays allowed so the checked corpus keeps parsing.
_PICTURE_EXCEPTIONS = {"images/authors/ aashishnair.jpg"}


class PeopleParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        base.activate(checkout)
        items = []
        for relative in checkout.files():
            path = PurePosixPath(str(relative))
            if path.parent.as_posix() != PEOPLE_DIRECTORY or path.suffix != ".md":
                continue
            if path.name.startswith("_"):
                continue
            items.append(self._item(checkout, path))
        items.sort(key=lambda item: item.data["record"]["slug"])
        # One media record per published profile picture, the reviewed build's
        # people media pass.  The picture is already allowlisted by the person
        # parse; a picture two profiles share was a duplicate-media build error
        # there and is the same refusal here.
        pictures: dict[str, str] = {}
        media_items = []
        for item in items:
            picture = item.data["record"]["image_source"]
            if not picture:
                continue
            if pictures.setdefault(picture, item.key) != item.key:
                base.fail("person picture is published by two profiles", picture)
            media_items.append(
                media_item(
                    checkout,
                    picture,
                    repository=PEOPLE_REPOSITORY,
                    content_types=PEOPLE_CONTENT_TYPES,
                )
            )
        items.extend(media_items)
        return items

    def upsert(self, item, source, media):
        if is_media_item(item):
            return upsert_media_item(item, source, media)
        record = item.data["record"]
        if record["image_source"]:
            media.upload(base.active_checkout(), record["image_source"], source)
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
        # The profiles and their published pictures withdraw together: a
        # picture no synced profile declares is no longer published.
        return base.delete_missing(source, CONTENT_KIND, seen_keys) + base.delete_missing(
            source, MEDIA_KIND, seen_keys
        )

    def _item(self, checkout, path) -> SourceItem:
        relative = path.as_posix()
        checksum = base.checksum_of(checkout, relative)
        metadata, body = builder._frontmatter(base.snapshot_path(checkout, relative))
        if not set(metadata).issubset(_ALLOWED_METADATA):
            base.fail("person fields are not public-allowlisted", relative)
        key = builder._person_key(metadata.get("short"))
        if key != path.stem:
            base.fail("person key does not match source path", relative)
        picture = builder._string(metadata.get("picture"), field="person picture", maximum=500)
        if _PICTURE_PATTERN.fullmatch(picture) is None and picture not in _PICTURE_EXCEPTIONS:
            base.fail("person picture is outside the allowlist", relative)
        links = []
        for label, network, field in (
            ("Website", "website", "web"),
            ("LinkedIn", "linkedin", "linkedin"),
            ("GitHub", "github", "github"),
            ("X", "x", "twitter"),
        ):
            url = builder._profile_url(metadata.get(field), network=network)
            if url:
                links.append({"label": label, "url": url})
        record = {
            "slug": key,
            "public_path": f"/people/{key}.html",
            "title": builder._title_from_record(metadata, key),
            "summary": builder._string(
                metadata.get("bio_short"),
                field="person short biography",
                maximum=4_000,
                optional=True,
            ),
            "blocks": builder._body_blocks(body),
            "links": links,
            "image_source": picture,
            "provenance": builder._provenance(
                repository=REPOSITORY,
                revision=checkout.commit_sha,
                source_path=relative,
                source_key=key,
                checksum=checksum,
            ),
        }
        return SourceItem(key=key, path=relative, data={"record": record, "checksum": checksum})


register_parser(CONTENT_KIND, PeopleParser())
