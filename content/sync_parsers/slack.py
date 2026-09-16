"""Slack page parser: the community's front door.

The page is authored in the structured content repository (``slack.yaml``); it
used to be a Liquid page in the legacy main-site repository that the staged
pipeline rendered into a ``resolve_public_document`` row.  The record carries
exactly what the page renders -- the title, the lead, the channel names in
their offered order, and the troubleshooting form's URL -- so the view keeps
reading a row's own shape instead of a rendered-document column it never used.

Like the platform catalog, the page is one published row: it exists when the
repository authors it and 404s when none does, which is the contract the
navigation listing already encodes.
"""

import re
from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from scripts import build_public_projection as builder

from . import base

SOURCE_SLUG = "dtc-content"
CONTENT_KIND = "slack_page"
REPOSITORY = "DataTalksClub/content"
SOURCE_FILE = "slack.yaml"
STABLE_KEY = "slack"
PUBLIC_PATH = "/slack"

_CHANNEL = re.compile(r"#[a-z0-9][a-z0-9_-]*\Z")


class SlackPageParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        paths = {PurePosixPath(str(relative)).as_posix() for relative in checkout.files()}
        if SOURCE_FILE not in paths:
            # The page is authored content: a repository without it publishes
            # no /slack row, and the stale row goes with the source.
            return []
        raw = builder._load_yaml(base.snapshot_path(checkout, SOURCE_FILE))
        if not isinstance(raw, dict) or not set(raw) <= {
            "title",
            "lead",
            "channels",
            "troubleshooting_url",
        }:
            base.fail("slack page record rejected", SOURCE_FILE)
        title = builder._string(raw.get("title"), field="slack page title", maximum=200)
        lead = builder._string(raw.get("lead"), field="slack page lead", maximum=500)
        raw_channels = raw.get("channels")
        if not isinstance(raw_channels, list) or not raw_channels:
            base.fail("slack page channels rejected", SOURCE_FILE)
        channels: list[str] = []
        for channel in raw_channels:
            name = builder._string(channel, field="slack channel name", maximum=100)
            if not _CHANNEL.fullmatch(name):
                base.fail("slack channel name rejected", SOURCE_FILE)
            if name in channels:
                base.fail("duplicate slack channel", SOURCE_FILE)
            channels.append(name)
        troubleshooting_url = builder._safe_url(
            raw.get("troubleshooting_url"),
            field="slack troubleshooting URL",
        )
        if troubleshooting_url and not troubleshooting_url.startswith("https://"):
            base.fail("slack troubleshooting URL must use HTTPS", SOURCE_FILE)
        record = {
            "public_path": PUBLIC_PATH,
            "title": title,
            "lead": lead,
            "channels": channels,
            "troubleshooting_url": troubleshooting_url,
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
        record = item.data["record"]
        document, action = base.upsert_document(
            source,
            content_kind=CONTENT_KIND,
            stable_key=STABLE_KEY,
            slug="slack",
            title=record["title"],
            summary=record["lead"],
            public_path=PUBLIC_PATH,
            source_path=SOURCE_FILE,
            checksum=item.data["checksum"],
            record=record,
        )
        return UpsertResult(document, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug != SOURCE_SLUG:
            return 0
        return base.delete_missing(source, CONTENT_KIND, seen_keys)


register_parser(CONTENT_KIND, SlackPageParser())
