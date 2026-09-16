"""Podcast platforms parser: the show's listening platforms.

The catalog is authored in the structured content repository
(``podcast-platforms.yaml``); it used to live in this repository's code, which
the database-owned-content rule forbids.  The record rules follow the reviewed
projection's platform pass (``_podcast_platforms``): the provider is the
canonical key episode ``links:`` entries normalize against, the rendered name
is the ``label`` (the reviewed build's redundant ``key``/``title`` echoes are
derived here, not authored), the ``dot`` is the list marker's color class, and
the URL must use HTTPS.  The catalog-wide "exactly these providers" rule stays
with the pinned-corpus gates, so the parser follows the repository as platforms
come and go.

The collection is a singleton: one published row carries the whole catalog,
and the catalogue reads it with ``singleton("podcast_platforms")``.
"""

import re
from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from scripts import build_public_projection as builder

from . import base

SOURCE_SLUG = "dtc-content"
CONTENT_KIND = "podcast_platforms"
REPOSITORY = "DataTalksClub/content"
SOURCE_FILE = "podcast-platforms.yaml"
STABLE_KEY = "podcast_platforms"
PUBLIC_PATH = "/-/content/podcast_platforms"

_DOT_CLASS = re.compile(r"dot-[a-z0-9-]+\Z")


class PodcastPlatformsParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        paths = {PurePosixPath(str(relative)).as_posix() for relative in checkout.files()}
        if SOURCE_FILE not in paths:
            # The catalog is authored content: a repository without it offers
            # no platforms, and the stale row goes with the source.
            return []
        raw = builder._load_yaml(base.snapshot_path(checkout, SOURCE_FILE))
        if not isinstance(raw, dict) or not isinstance(raw.get("platforms"), list):
            base.fail("podcast platform catalog rejected", SOURCE_FILE)
        platforms: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw["platforms"]:
            if not isinstance(item, dict) or set(item) != {"provider", "label", "url", "dot"}:
                base.fail("podcast platform record rejected", SOURCE_FILE)
            provider = builder._canonical_podcast_platform_key(item["provider"])
            if provider in seen:
                base.fail("duplicate podcast platform provider", SOURCE_FILE)
            label = builder._string(item["label"], field="podcast platform label", maximum=100)
            dot = builder._string(item["dot"], field="podcast platform dot", maximum=50)
            if not _DOT_CLASS.fullmatch(dot):
                base.fail("podcast platform dot class rejected", SOURCE_FILE)
            url = builder._safe_url(item["url"], field="podcast platform URL", optional=False)
            if not url.startswith("https://"):
                base.fail("podcast platform URL must use HTTPS", SOURCE_FILE)
            seen.add(provider)
            platforms.append(
                {
                    "key": provider,
                    "provider": provider,
                    "label": label,
                    "title": label,
                    "url": url,
                    "dot": dot,
                }
            )
        if not platforms:
            base.fail("podcast platform catalog is empty", SOURCE_FILE)
        record = {
            "platforms": platforms,
            "provenance": builder._provenance(
                repository=REPOSITORY,
                revision=checkout.commit_sha,
                source_path=SOURCE_FILE,
                source_key=STABLE_KEY,
                checksum=base.checksum_of(checkout, SOURCE_FILE),
            ),
        }
        return [
            SourceItem(
                key=STABLE_KEY,
                path=SOURCE_FILE,
                data={
                    "record": record,
                    "checksum": base.checksum_of(checkout, SOURCE_FILE),
                },
            )
        ]

    def upsert(self, item, source, media):
        document, action = base.upsert_document(
            source,
            content_kind=CONTENT_KIND,
            stable_key=STABLE_KEY,
            slug="",
            title="Podcast platforms",
            summary="The listening platforms the show publishes.",
            public_path=PUBLIC_PATH,
            source_path=SOURCE_FILE,
            checksum=item.data["checksum"],
            record=item.data["record"],
        )
        return UpsertResult(document, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug != SOURCE_SLUG:
            return 0
        return base.delete_missing(source, CONTENT_KIND, seen_keys)


register_parser(CONTENT_KIND, PodcastPlatformsParser())
