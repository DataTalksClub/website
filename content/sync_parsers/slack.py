"""Slack page parser: the community's front door.

The page is authored in the structured content repository (``slack.yaml``); it
used to be a Liquid page in the legacy main-site repository that the staged
pipeline rendered into a ``resolve_public_document`` row.  The record carries
exactly what the page renders -- the hero copy, the benefits with the channels
each one names, the three joining steps, the FAQ (Markdown answers), the
closing line -- so the view keeps reading a
row's own shape instead of a rendered-document column it never used.

Like the platform catalog, the page is one published row: it exists when the
repository authors it and 404s when none does, which is the contract the
navigation listing already encodes.
"""

import re
from pathlib import PurePosixPath
from typing import Any

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from content import projection_helpers as builder

from . import base

SOURCE_SLUG = "dtc-content"
CONTENT_KIND = "slack_page"
REPOSITORY = "DataTalksClub/content"
SOURCE_FILE = "slack.yaml"
STABLE_KEY = "slack"
PUBLIC_PATH = "/slack"

_CHANNEL = re.compile(r"#[a-z0-9][a-z0-9_-]*\Z")

#: The page's plain-text fields and their length caps, in reading order.
_TEXT_FIELDS = {
    "title": 200,
    "lead": 500,
    "proof": 200,
    "benefits_intro": 400,
    "start_here": 300,
    "close_title": 200,
    "close_text": 400,
}
_FIELDS = {*_TEXT_FIELDS, "benefits", "steps", "faq"}


def _entries(
    raw: Any,
    name: str,
    text_fields: dict[str, int],
    extra: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """A non-empty list of mappings holding exactly ``text_fields`` (+ ``extra``)."""

    if not isinstance(raw, list) or not raw:
        base.fail(f"slack page {name} rejected", SOURCE_FILE)
    entries: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {*text_fields, *extra}:
            base.fail(f"slack page {name} entry rejected", SOURCE_FILE)
        prepared = {
            field: builder._string(entry[field], field=f"slack page {name} {field}", maximum=cap)
            for field, cap in text_fields.items()
        }
        prepared.update({field: entry[field] for field in extra})
        entries.append(prepared)
    return entries


def _channels(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        base.fail("slack page channels rejected", SOURCE_FILE)
    channels: list[str] = []
    for channel in raw:
        name = builder._string(channel, field="slack channel name", maximum=100)
        if not _CHANNEL.fullmatch(name):
            base.fail("slack channel name rejected", SOURCE_FILE)
        if name in channels:
            base.fail("duplicate slack channel", SOURCE_FILE)
        channels.append(name)
    return channels


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
        if not isinstance(raw, dict) or not set(raw) <= _FIELDS:
            base.fail("slack page record rejected", SOURCE_FILE)
        page = {
            name: builder._string(raw.get(name), field=f"slack page {name}", maximum=maximum)
            for name, maximum in _TEXT_FIELDS.items()
        }
        benefits = [
            {**entry, "channels": _channels(entry["channels"])}
            for entry in _entries(
                raw.get("benefits"),
                "benefits",
                {"title": 100, "body": 400},
                frozenset({"channels"}),
            )
        ]
        steps = _entries(raw.get("steps"), "steps", {"title": 100, "body": 400})
        if len(steps) != 3:
            base.fail("slack page steps must be three", SOURCE_FILE)
        faq = _entries(raw.get("faq"), "faq", {"question": 200, "answer": 1000})
        record = {
            "public_path": PUBLIC_PATH,
            **page,
            "benefits": benefits,
            "steps": steps,
            "faq": faq,
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
