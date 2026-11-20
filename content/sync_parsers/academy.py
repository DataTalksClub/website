"""Academy parser: the DataTalks.Club Academy pages from the content repository.

Academy is ordinary editorial content.  ``academy/*.md`` holds one page per
file -- structured frontmatter for the parts a page lays out (hero, outcomes,
syllabus, sessions, FAQ items, form copy), and a Markdown body for the pages
that are prose (the privacy notice, the Impressum).  ``academy/shared.yaml``
holds the copy several pages share: the facts strip, the instructor, the
admission steps, the waitlist form.  The artwork lives in ``images/academy/``
and is published by the media parser like every other editorial image.

Each layout has one schema below.  A file that names an unknown layout, misses
a field its layout requires, carries a field its layout does not know, or
points at an image the repository does not hold is refused, so a page cannot
publish half-drawn.  The record keeps the authored values as they are; the
Markdown fields (links in an answer, the legal bodies) are rendered by the
website at read time through the shared sanitizer.

The pages publish at the paths their files declare, all below ``/academy/``.
A repository without ``academy/`` publishes no Academy, and the routes 404.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from content import projection_helpers as builder
from content.projection_helpers import ProjectionBuildError

from . import base

SOURCE_SLUG = "dtc-content"
PAGE_KIND = "academy_page"
SHARED_KIND = "academy_shared"
REPOSITORY = "DataTalksClub/content"
ROOT = "academy"
SHARED_FILE = "academy/shared.yaml"
SHARED_KEY = "shared"
#: The shared copy is not a page; its row still needs a path of its own.
SHARED_PATH = "/-/academy/shared"
IMAGE_PREFIX = "images/academy/"
LANGUAGES = ("en", "de")

_PATH = re.compile(r"/academy/(?:[a-z0-9][a-z0-9-]*/){0,2}\Z")
_KEY = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class Optional:
    """A field the page may leave out."""

    def __init__(self, spec: Any) -> None:
        self.spec = spec


# Leaf specs: "text" a plain string, "markdown" a string that may carry inline
# Markdown, "href" a site path below /academy/, an https URL or a mailto link,
# "url" an https URL, "key" a lower-case identifier, "image" a file below
# images/academy/, "utc" an ISO UTC instant, "int", "bool".
LINK = {"label": "text", "href": "href"}
QUESTION = {"question": "text", "answer": "markdown"}
FACTS = {"label": "text", "items": [{"label": "text", "value": "text"}]}

COMMON = {
    "layout": "key",
    "path": "path",
    "title": "text",
    "description": "text",
    "lang": Optional("lang"),
    "hreflang": Optional("bool"),
    "noindex": Optional("bool"),
    "sitemap": Optional("int"),
    "og_image": Optional("image"),
}

LAYOUTS: dict[str, dict[str, Any]] = {
    "home": {
        "hero": {"eyebrow": "text", "headline": "text", "lead": "text", "links": [LINK]},
        "tracks": {"label": "text", "button_prefix": "text", "outcomes": ["int"]},
        "funding": {"heading": "text", "paragraphs": ["text"]},
        "schema": {
            "organization": {"name": "text", "url": "url", "same_as": ["url"]},
        },
    },
    "program": {
        "program": {"slug": "key", "name": "text", "label": "text", "survey_option": "text"},
        "hero": {"eyebrow": "text", "headline": "text", "lead": "text", "basis": "text"},
        "outcomes": {"heading": "text", "items": ["text"]},
        "project": "text",
        "fit_first": "text",
        "prerequisites": "text",
        "syllabus": [
            {
                "weeks": "text",
                "title": "text",
                "modules": [
                    {
                        "title": "text",
                        "produce": "text",
                        "description": Optional("text"),
                        "topics": Optional(["text"]),
                    }
                ],
            }
        ],
        "schema": {
            "start_date": "text",
            "course_modes": ["text"],
            "location": "text",
            "country": "text",
            "instructor": "text",
            "provider": "text",
            "provider_url": "url",
        },
    },
    "sessions": {
        "series": {"name": "text", "calendar_slug": "key", "hero_time": "text"},
        "hero": {
            "eyebrow": "text",
            "headline": "text",
            "lead": "text",
            "button": "text",
            "under_button": "text",
            "speaker_line": "text",
            "speaker_photo": "image",
            "speaker_alt": "text",
        },
        "sessions": [
            {
                "id": "key",
                "number": "int",
                "date_label": "text",
                "weekday_date": "text",
                "starts": "utc",
                "ends": "utc",
                "title": "text",
                "hero_topic": "text",
                "short_label": "text",
                "description": "text",
                "zoom_url": "url",
                "meetup_url": Optional("url"),
                "hero_time": Optional("text"),
            }
        ],
        "rsvp_published": "bool",
        "audience": {
            "heading": "text",
            "intro": "text",
            "items": ["text"],
            "outro": "text",
            "note": "markdown",
        },
        "schedule": {"heading": "text", "after": "text"},
        "host": {"heading": "text", "text": "text"},
        "numbers": {"heading": "text", "items": ["markdown"]},
        "faq": {"heading": "text", "items": [QUESTION]},
        "final": {"heading": "text", "text": "text"},
        "form": {
            "email_label": "text",
            "email_placeholder": "text",
            "email_error": "text",
            "name_label": "text",
            "sessions_label": "text",
            "sessions_error": "text",
            "track_label": "text",
            "tracks": [{"value": "key", "label": "text"}],
            "status_label": "text",
            "statuses": [{"value": "key", "label": "text"}],
            "years_label": "text",
            "based_label": "text",
            "based": [{"value": "key", "label": "text"}],
            "bildung_label": "text",
            "bildung": [{"value": "key", "label": "text"}],
            "free_text_label": "text",
            "optin_label": "text",
            "micro": "text",
            "button": "text",
            "busy": "text",
            "failed": "markdown",
            "noscript": "markdown",
            "privacy_label": "text",
            "success": {
                "eyebrow": "text",
                "title": "text",
                "text": "text",
                "rsvp_heading": "text",
                "rsvp_text": "text",
                "rsvp_button": "text",
                "calendar_heading": "text",
                "calendar_all": "text",
            },
        },
        "calendar": {
            "prodid": "text",
            "uid_domain": "text",
            "stamp": "text",
            "zoom_label": "text",
            "note": "text",
            "alarm_prefix": "text",
        },
        "footer_links": [LINK],
        "schema": {
            "location_name": "text",
            "organizer": "text",
            "organizer_url": "url",
            "performer": "text",
        },
    },
    "materials": {
        "heading": "text",
        "text": "text",
        "button": "text",
        "busy": "text",
        "success": "text",
        "failed": "markdown",
        "noscript": "markdown",
        "micro": "text",
    },
    "confirm": {
        "eyebrow": "text",
        "heading": "text",
        "message": "text",
        "back": "text",
        "states": {
            name: "text"
            for name in (
                "error_eyebrow",
                "confirmed_eyebrow",
                "missing_title",
                "missing_message",
                "unconfigured_title",
                "unconfigured_message",
                "confirmed_title",
                "confirmed_message",
                "invalid_title",
                "invalid_message",
                "failed_title",
                "failed_message",
            )
        },
    },
    "guide": {
        "hero": {"eyebrow": "text", "headline": "text", "lead": "text", "funding_line": "text"},
        "facts": FACTS,
        "fit": {
            "heading": "text",
            "for_title": "text",
            "for": ["text"],
            "not_title": "text",
            "not": ["text"],
        },
        "programs": {
            "heading": "text",
            "intro": "text",
            "items": [
                {
                    "slug": "key",
                    "name": "text",
                    "summary": "text",
                    "basis": "text",
                    "button": "text",
                }
            ],
        },
        "guide": {
            "heading": "text",
            "paragraphs": ["text"],
            "steps_title": "text",
            "steps": ["text"],
            "links": [LINK],
        },
        "join": {
            "heading": "text",
            "text": "text",
            "actions": [LINK],
            "sessions_label": "text",
        },
    },
    "faq": {
        "eyebrow": "text",
        "heading": "text",
        "lead": "text",
        "button": "text",
        "list_label": "text",
        "items": [QUESTION],
    },
    "legal": {"heading": "text"},
}

#: The layouts whose page is a Markdown body; every other layout has none.
BODY_LAYOUTS = frozenset({"legal"})

SHARED = {
    "brand": "text",
    "contact_email": "text",
    "seat_caption": "text",
    "waitlist_button": "text",
    "facts": FACTS,
    "hero": {"instructor_line": "text", "honesty": "text"},
    "instructor": {
        "heading": "text",
        "paragraphs": ["text"],
        "photo": "image",
        "photo_alt": "text",
        "proof": [LINK],
    },
    "project_heading": "text",
    "fit": {
        "for_title": "text",
        "not_title": "text",
        "for_shared": ["text"],
        "not": ["markdown"],
    },
    "syllabus": {"heading": "text", "intro": "text", "produce_label": "text"},
    "funding": {"heading": "text", "paragraphs": ["text"], "link": LINK},
    "admission": {
        "heading": "text",
        "body": "text",
        "steps_heading": "text",
        "steps": [{"title": "text", "text": "text"}],
    },
    # An item without an answer takes the program page's own prerequisites.
    "faq": {
        "heading": "text",
        "items": [{"question": "text", "answer": Optional("markdown")}],
    },
    "join": {"heading": "text", "text": "text"},
    "footer": {"links": [LINK], "copyright": "text", "other_track": "text"},
    "signup": {
        "program_label": "text",
        "name_label": "text",
        "name_placeholder": "text",
        "payment_label": "text",
        "payment_placeholder": "text",
        "payment_options": ["text"],
        "payment_error": "text",
        "email_label": "text",
        "email_placeholder": "text",
        "button": "text",
        "busy": "text",
        "email_error": "text",
        "failed": "markdown",
        "noscript": "markdown",
        "micro": "text",
        "privacy_label": "text",
        "success": {
            "eyebrow": "text",
            "title": "text",
            "text": "text",
            "questions_label": "text",
            "questions": [
                {
                    "key": "key",
                    "question": "text",
                    "options": [{"value": "key", "label": "text"}],
                }
            ],
            "answers_error": "text",
            "survey_text": "text",
            "survey_button": "text",
            "survey_url": "url",
        },
    },
}


class _Validator:
    def __init__(self, files: frozenset[str], source_path: str) -> None:
        self.files = files
        self.source_path = source_path

    def fail(self, field: str) -> None:
        base.fail(f"academy field rejected ({field})", self.source_path)

    def value(self, spec: Any, raw: Any, field: str) -> Any:
        if isinstance(spec, dict):
            if not isinstance(raw, dict) or not set(raw) <= set(spec):
                self.fail(field)
            cleaned = {}
            for key, child in spec.items():
                if isinstance(child, Optional):
                    if raw.get(key) is None:
                        continue
                    child = child.spec
                elif key not in raw:
                    self.fail(f"{field}.{key}")
                cleaned[key] = self.value(child, raw[key], f"{field}.{key}")
            return cleaned
        if isinstance(spec, list):
            if not isinstance(raw, list) or not raw:
                self.fail(field)
            return [self.value(spec[0], item, f"{field}[]") for item in raw]
        return self.leaf(spec, raw, field)

    def leaf(self, spec: str, raw: Any, field: str) -> Any:
        if spec == "int":
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                self.fail(field)
            return raw
        if spec == "bool":
            if not isinstance(raw, bool):
                self.fail(field)
            return raw
        try:
            value = builder._string(raw, field=field, maximum=20_000)
        except ProjectionBuildError:
            self.fail(field)
        if spec in {"text", "markdown"}:
            return value
        if spec == "key":
            if not _KEY.fullmatch(value):
                self.fail(field)
        elif spec == "lang":
            if value not in LANGUAGES:
                self.fail(field)
        elif spec == "path":
            if not _PATH.fullmatch(value):
                self.fail(field)
        elif spec == "utc":
            if not _UTC.fullmatch(value):
                self.fail(field)
        elif spec == "url":
            try:
                url = builder._safe_url(value, field=field, optional=False)
            except ProjectionBuildError:
                self.fail(field)
            if not url.startswith("https://"):
                self.fail(field)
        elif spec == "href":
            if not (
                (value.startswith("/academy/") and _PATH.fullmatch(value.split("#", 1)[0]))
                or value.startswith("mailto:")
            ):
                self.leaf("url", value, field)
        elif spec == "image":
            path = PurePosixPath(value)
            if not value.startswith(IMAGE_PREFIX) or ".." in path.parts or value not in self.files:
                self.fail(field)
        else:  # pragma: no cover - a schema typo, not a source defect
            raise AssertionError(spec)
        return value


def _files(checkout) -> frozenset[str]:
    return frozenset(PurePosixPath(str(relative)).as_posix() for relative in checkout.files())


def _provenance(checkout, relative: str, key: str, checksum: str) -> dict[str, str]:
    return builder._provenance(
        repository=REPOSITORY,
        revision=checkout.commit_sha,
        source_path=relative,
        source_key=key,
        checksum=checksum,
    )


def parse_page(checkout, relative: str, files: frozenset[str]) -> dict[str, Any]:
    """One validated page record from ``academy/<name>.md``."""

    try:
        metadata, body = builder._frontmatter(base.snapshot_path(checkout, relative))
    except ProjectionBuildError:
        base.fail("academy page front matter rejected", relative)
    layout = metadata.get("layout")
    if layout not in LAYOUTS:
        base.fail("academy page layout rejected", relative)
    validator = _Validator(files, relative)
    data = validator.value({**COMMON, **LAYOUTS[layout]}, metadata, "page")
    if (layout in BODY_LAYOUTS) != bool(body.strip()):
        base.fail("academy page body rejected", relative)
    if layout == "program" and data["path"] != f"/academy/{data['program']['slug']}/":
        base.fail("academy program path does not match its slug", relative)
    if layout == "sessions":
        ids = [session["id"] for session in data["sessions"]]
        if len(ids) != len(set(ids)) or data["series"]["calendar_slug"] in ids:
            base.fail("academy session ids are not unique", relative)
    checksum = base.checksum_of(checkout, relative)
    key = data["path"]
    return {
        "public_path": data["path"],
        "layout": layout,
        "lang": data.get("lang", "en"),
        "title": data["title"],
        "description": data["description"],
        "og_image": f"/{data['og_image']}" if data.get("og_image") else "",
        "sitemap": data.get("sitemap"),
        "noindex": data.get("noindex", False),
        "data": data,
        "body": body.strip(),
        "checksum": checksum,
        "provenance": _provenance(checkout, relative, key, checksum),
    }


class AcademyPageParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        base.activate(checkout)
        files = _files(checkout)
        items: list[SourceItem] = []
        programs: set[str] = set()
        for relative in sorted(files):
            path = PurePosixPath(relative)
            if path.parent.as_posix() != ROOT or path.suffix != ".md":
                continue
            if path.name.startswith("_") or path.name.upper() == "README.MD":
                continue
            record = parse_page(checkout, relative, files)
            if any(item.key == record["public_path"] for item in items):
                base.fail("academy page path is published twice", relative)
            if record["layout"] == "program":
                programs.add(record["data"]["program"]["slug"])
            items.append(
                SourceItem(
                    key=record["public_path"],
                    path=relative,
                    data={"record": record, "checksum": record["checksum"]},
                )
            )
        if items and SHARED_FILE not in files:
            base.fail("academy pages need the shared copy", SHARED_FILE)
        for item in items:
            record = item.data["record"]
            if record["layout"] == "guide":
                for program in record["data"]["programs"]["items"]:
                    if program["slug"] not in programs:
                        base.fail("academy guide names an unknown program", item.path)
        return sorted(items, key=lambda item: item.key)

    def upsert(self, item, source, media):
        record = item.data["record"]
        document, action = base.upsert_document(
            source,
            content_kind=PAGE_KIND,
            stable_key=record["public_path"],
            slug=record["public_path"],
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
        return base.delete_missing(source, PAGE_KIND, seen_keys)


class AcademySharedParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        files = _files(checkout)
        if SHARED_FILE not in files:
            return []
        raw = builder._load_yaml(base.snapshot_path(checkout, SHARED_FILE))
        data = _Validator(files, SHARED_FILE).value(SHARED, raw, "shared")
        checksum = base.checksum_of(checkout, SHARED_FILE)
        record = {
            "data": data,
            "provenance": _provenance(checkout, SHARED_FILE, SHARED_KEY, checksum),
        }
        return [
            SourceItem(
                key=SHARED_KEY,
                path=SHARED_FILE,
                data={"record": record, "checksum": checksum},
            )
        ]

    def upsert(self, item, source, media):
        document, action = base.upsert_document(
            source,
            content_kind=SHARED_KIND,
            stable_key=SHARED_KEY,
            slug=SHARED_KEY,
            title="Academy shared copy",
            summary="",
            public_path=SHARED_PATH,
            source_path=SHARED_FILE,
            checksum=item.data["checksum"],
            record=item.data["record"],
        )
        return UpsertResult(document, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug != SOURCE_SLUG:
            return 0
        return base.delete_missing(source, SHARED_KIND, seen_keys)


register_parser(PAGE_KIND, AcademyPageParser())
register_parser(SHARED_KIND, AcademySharedParser())
