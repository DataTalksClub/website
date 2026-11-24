"""Academy parser: the DataTalks.Club Academy pages from the content repository.

Academy is ordinary editorial content.  ``academy/*.md`` holds one page per
file -- structured frontmatter for the parts a page lays out (hero, outcomes,
syllabus, sessions, FAQ items, form copy), and a Markdown body for the pages
that are prose (the privacy notice, the Impressum, a session recap, a guide).
``academy/shared.yaml`` holds the copy several pages share: the facts strip, the instructor, the
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
from datetime import date
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
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class Optional:
    """A field the page may leave out."""

    def __init__(self, spec: Any) -> None:
        self.spec = spec


# Leaf specs: "text" a plain string, "markdown" a string that may carry inline
# Markdown, "href" a site path below /academy/, an https URL or a mailto link,
# "url" an https URL, "key" a lower-case identifier, "image" a file below
# images/academy/, "utc" an ISO UTC instant, "date" an ISO calendar date
# (quoted, so YAML keeps it a string), "int", "bool".
LINK = {"label": "text", "href": "href"}
QUESTION = {"question": "text", "answer": "markdown"}
FACTS = {"label": "text", "items": [{"label": "text", "value": "text"}]}

#: One syllabus module: the week it is taught in, an action-oriented title
#: and the one tangible thing it leaves you with.
MODULE = {
    "week": Optional("int"),
    "title": "text",
    "produce": "text",
    "description": Optional("text"),
    "topics": Optional(["text"]),
}

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
    # The same page in another language, declared both ways as hreflang
    # alternates (the German Bildungsgutschein guide and its English one).
    "alternates": Optional([{"lang": "lang", "path": "path"}]),
}

LAYOUTS: dict[str, dict[str, Any]] = {
    "home": {
        # ``links`` is no longer drawn: the free sessions and the funding
        # guide have chapters of their own (_docs/design/landing-pages.md).
        "hero": {"eyebrow": "text", "headline": "text", "lead": "text", "links": Optional([LINK])},
        # ``compare`` is the link to the page comparing the two programs.
        "tracks": {
            "label": "text",
            "button_prefix": "text",
            "outcomes": ["int"],
            "compare": Optional(LINK),
        },
        "funding": Optional(
            {
                "heading": "text",
                "paragraphs": ["text"],
                "link": Optional(LINK),
                "links": Optional([LINK]),
            }
        ),
        # The free session series, its dates read from the sessions page.
        "sessions": Optional({"heading": "text", "text": "text", "link_label": "text"}),
        # The chapter listing the Academy's articles (session recaps, guides).
        "articles": Optional(
            {"heading": "text", "intro": Optional("text"), "link_label": Optional("text")}
        ),
        # A few questions from the FAQ page, by their place in its list.
        "faq": Optional({"heading": "text", "picks": ["int"], "link_label": "text"}),
        "schema": {
            "organization": {"name": "text", "url": "url", "same_as": ["url"]},
        },
    },
    "program": {
        "program": {"slug": "key", "name": "text", "label": "text", "survey_option": "text"},
        # The three short lines the home page's program card shows; the two
        # programs' cards mirror each other, so each line is about one line.
        "highlights": Optional(["text"]),
        "hero": {"eyebrow": "text", "headline": "text", "lead": "text", "basis": "text"},
        # ``intro`` is the one sentence under the heading: what the outcomes
        # add up to.
        "outcomes": {"heading": "text", "intro": Optional("text"), "items": ["text"]},
        # The challenge, what the program covers and how the format gets you
        # there.
        "overview": Optional({"heading": "text", "paragraphs": ["text"]}),
        # The final project: ``project`` says what you ship, ``capstone`` the
        # weeks it is built in, after the syllabus weeks.
        "project": "text",
        "capstone": Optional({"heading": "text", "modules": [MODULE]}),
        # The articles a reader of this program goes on to (the comparison
        # guide, the funding guides), above the closing waitlist.
        "related": Optional({"heading": "text", "links": [LINK]}),
        "fit_first": "text",
        "prerequisites": "text",
        # The syllabus is read week by week: each phase lists its modules,
        # and each module names the week it is taught in.  A phase's week
        # span is derived from its modules, so ``weeks`` is no longer
        # written; it is accepted until the content tree drops it.
        "syllabus": [{"weeks": Optional("text"), "title": "text", "modules": [MODULE]}],
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
            # No longer drawn: the host chapter introduces the speaker.
            "speaker_line": Optional("text"),
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
                # The written recap of a session that has happened.
                "recap": Optional(LINK),
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
        "footer_links": Optional([LINK]),
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
        # ``funding_line`` is no longer drawn: the facts strip carries funding.
        "hero": {
            "eyebrow": "text",
            "headline": "text",
            "lead": "text",
            "funding_line": Optional("text"),
        },
        # The German page's pointer to the English page with the same facts.
        "english": Optional({"text": "text", "link": LINK}),
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
        # No longer drawn: the page's one waitlist action is its closing panel.
        "button": Optional("text"),
        "list_label": "text",
        "items": [QUESTION],
        # The closing waitlist panel under the questions.
        "join": Optional({"heading": "text", "text": "text"}),
    },
    "legal": {"heading": "text"},
    # The index of every article page (session recaps, guides), newest
    # first; the list itself is read from the article pages.
    "articles": {"heading": "text", "lead": "text"},
    # A free-form page read as prose -- a session recap, a guide: a heading
    # and lead over a Markdown body, the pages it points on to, and one
    # closing waitlist panel.
    "article": {
        "eyebrow": "text",
        "heading": "text",
        "lead": "text",
        "author": "text",
        "published": "date",
        "related": Optional({"heading": "text", "links": [LINK]}),
        # Questions the article answers, drawn as the site's FAQ folds and
        # published as FAQPage structured data.
        "faq": Optional({"heading": "text", "items": [QUESTION]}),
        # The closing action: the waitlist on the home page unless the article
        # names its own target (a program page's form, a link with campaign
        # parameters) and, optionally, its own label.
        "join": {
            "heading": "text",
            "text": "text",
            "href": Optional("href"),
            "label": Optional("text"),
        },
    },
}

#: The layouts whose page is a Markdown body; every other layout has none.
BODY_LAYOUTS = frozenset({"legal", "article"})

SHARED = {
    "brand": "text",
    "contact_email": "text",
    # No longer drawn: the facts strip carries the seats (owner, 2026-11-24).
    "seat_caption": Optional("text"),
    "waitlist_button": "text",
    # The Academy's own bar under the site masthead: its wordmark, tagline and
    # section links (its waitlist action reuses ``waitlist_button``).
    "header": Optional({"wordmark": "text", "tagline": "text", "label": "text", "links": [LINK]}),
    # The program pages' sticky waitlist bar.
    "sticky": Optional({"caption": "text"}),
    # The contact line under the questions, one Markdown sentence.
    "contact": Optional("markdown"),
    "other_track": Optional("text"),
    # Every signup form and its confirmation-pending state say this plainly.
    "confirm_notice": Optional("text"),
    "facts": FACTS,
    # ``instructor_line`` is the hero's credibility line; ``honesty`` (the
    # start line over the form) is no longer drawn.
    "hero": {"instructor_line": Optional("text"), "honesty": Optional("text")},
    "instructor": {
        "heading": "text",
        "paragraphs": ["text"],
        "photo": "image",
        "photo_alt": "text",
        "proof": [LINK],
    },
    # The program pages' final-project chapter heading.
    "project_heading": "text",
    # Interview preparation: its own chapter, after the capstone weeks.
    "interviews": Optional({"heading": "text", "intro": "text", "modules": [MODULE]}),
    # The daily rhythm, start and place, as a short chapter.
    "schedule": Optional({"heading": "text", "paragraphs": ["text"]}),
    "fit": {
        # The band head over the two fit cards.
        "heading": Optional("text"),
        "for_title": "text",
        "not_title": "text",
        "for_shared": ["text"],
        "not": ["markdown"],
    },
    # ``produce_label`` is no longer drawn (each module's outcome reads on
    # its own under the title); still accepted until the content drops it.
    # ``week_label`` is the word in front of each syllabus row's week number.
    "syllabus": {
        "heading": "text",
        "intro": "text",
        "week_label": Optional("text"),
        "produce_label": Optional("text"),
    },
    "funding": Optional(
        {
            "heading": "text",
            "paragraphs": ["text"],
            "link": LINK,
            "guide_link": Optional(LINK),
        }
    ),
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
    # Nothing draws it any more: the Academy has no footer of its own under the
    # site's.  It is still accepted so the content tree can drop it after this
    # parser ships, instead of failing the sync in between.
    "footer": Optional({"links": [LINK], "copyright": "text", "other_track": "text"}),
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
        elif spec == "date":
            if not _DATE.fullmatch(value):
                self.fail(field)
            try:
                date.fromisoformat(value)
            except ValueError:
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


def _weeks_follow_on(syllabus: list[dict[str, Any]]) -> bool:
    """Every module names its week, the first is week 1, and no week is skipped.

    A week may hold more than one module, so a module's week is the previous
    module's or the one after it.  A syllabus that names no weeks at all is the
    older module-numbered shape, accepted until the content tree moves over.
    """

    weeks = [module.get("week") for phase in syllabus for module in phase["modules"]]
    if all(week is None for week in weeks):
        return True
    return bool(weeks) and weeks[0] == 1 and _weeks_run_on(weeks)


def _weeks_run_on(weeks: list[Any]) -> bool:
    """Every module names a week, and each is the previous one or the next."""

    if any(week is None for week in weeks):
        return False
    return all(
        later in (earlier, earlier + 1) for earlier, later in zip(weeks, weeks[1:], strict=False)
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
    if layout == "program" and not _weeks_follow_on(
        [*data["syllabus"], *([data["capstone"]] if data.get("capstone") else [])]
    ):
        base.fail("academy syllabus weeks do not run 1, 2, 3 in order", relative)
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
        interviews = data.get("interviews")
        if interviews and not _weeks_run_on([m.get("week") for m in interviews["modules"]]):
            base.fail("academy interview weeks do not run on in order", SHARED_FILE)
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
