"""Newsletter page parser: the ``/newsletter`` landing page's copy.

The page is authored in the structured content repository
(``newsletter.yaml``), like ``/slack``.  The record carries exactly what the
page renders -- the hero copy, the issue parts (each naming which of the
site's live feeds the page lists under it), the FAQ (Markdown answers) and
the closing line.  The signup form and the live lists are the site's own;
nothing in the record describes a particular issue.

One published row: it exists when the repository authors it, and the page
404s when none does.
"""

from pathlib import PurePosixPath
from typing import Any

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from content import projection_helpers as builder

from . import base

SOURCE_SLUG = "dtc-content"
CONTENT_KIND = "newsletter_page"
REPOSITORY = "DataTalksClub/content"
SOURCE_FILE = "newsletter.yaml"
STABLE_KEY = "newsletter"
PUBLIC_PATH = "/newsletter"

#: The live feeds an issue part may name; the page lists the matching rows.
ISSUE_PARTS = frozenset({"events", "courses", "articles"})

#: The page's plain-text fields and their length caps, in reading order.
_TEXT_FIELDS = {
    "title": 200,
    "lead": 500,
    "proof": 200,
    "issue_intro": 400,
    "community_note": 300,
    "close_title": 200,
    "close_text": 400,
}
_FIELDS = {*_TEXT_FIELDS, "issue_parts", "faq"}


def _entries(raw: Any, name: str, text_fields: dict[str, int]) -> list[dict[str, str]]:
    """A non-empty list of mappings holding exactly ``text_fields``."""

    if not isinstance(raw, list) or not raw:
        base.fail(f"newsletter page {name} rejected", SOURCE_FILE)
    entries: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != set(text_fields):
            base.fail(f"newsletter page {name} entry rejected", SOURCE_FILE)
        entries.append(
            {
                field: builder._string(
                    entry[field], field=f"newsletter page {name} {field}", maximum=cap
                )
                for field, cap in text_fields.items()
            }
        )
    return entries


def _issue_parts(raw: Any) -> list[dict[str, str]]:
    parts = _entries(raw, "issue_parts", {"part": 20, "title": 100, "body": 400})
    names = [entry["part"] for entry in parts]
    if not set(names) <= ISSUE_PARTS or len(set(names)) != len(names):
        base.fail("newsletter page issue part rejected", SOURCE_FILE)
    return parts


class NewsletterPageParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        paths = {PurePosixPath(str(relative)).as_posix() for relative in checkout.files()}
        if SOURCE_FILE not in paths:
            return []
        raw = builder._load_yaml(base.snapshot_path(checkout, SOURCE_FILE))
        if not isinstance(raw, dict) or not set(raw) <= _FIELDS:
            base.fail("newsletter page record rejected", SOURCE_FILE)
        page = {
            name: builder._string(raw.get(name), field=f"newsletter page {name}", maximum=maximum)
            for name, maximum in _TEXT_FIELDS.items()
        }
        checksum = base.checksum_of(checkout, SOURCE_FILE)
        record = {
            "public_path": PUBLIC_PATH,
            **page,
            "issue_parts": _issue_parts(raw.get("issue_parts")),
            "faq": _entries(raw.get("faq"), "faq", {"question": 200, "answer": 1000}),
            "provenance": builder._provenance(
                repository=REPOSITORY,
                revision=checkout.commit_sha,
                source_path=SOURCE_FILE,
                source_key=STABLE_KEY,
                checksum=checksum,
            ),
        }
        return [
            SourceItem(
                key=STABLE_KEY,
                path=SOURCE_FILE,
                data={"record": record, "checksum": checksum},
            )
        ]

    def upsert(self, item, source, media):
        record = item.data["record"]
        document, action = base.upsert_document(
            source,
            content_kind=CONTENT_KIND,
            stable_key=STABLE_KEY,
            slug="newsletter",
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


register_parser(CONTENT_KIND, NewsletterPageParser())
