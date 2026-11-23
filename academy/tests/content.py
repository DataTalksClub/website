"""A synthetic Academy content tree, and the sync that publishes it in a test.

The copy is invented test text, shaped like the content repository's
``academy/`` files (``academy/*.md`` pages, ``academy/shared.yaml`` and the
``images/academy/`` artwork).  Tests publish it through the real parsers, so
they exercise the same rows a ``sync_content`` run writes.
"""

from __future__ import annotations

import copy
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

PNG = b"\x89PNG\r\n\x1a\nsynthetic"
JPEG = b"\xff\xd8\xffsynthetic\xff\xd9"
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"

LINK = {"label": "Synthetic link", "href": "/academy/faq/"}
CONTACT = "test@example.invalid"


def _choice(*values: str) -> list[dict[str, str]]:
    return [{"value": value, "label": f"Option {value}"} for value in values]


SHARED: dict[str, Any] = {
    "brand": "Synthetic Academy",
    "contact_email": CONTACT,
    "seat_caption": "Synthetic seats",
    "waitlist_button": "Join the synthetic list",
    "facts": {
        "label": "Synthetic facts",
        "items": [{"label": "START", "value": "Synthetic start"}],
    },
    "hero": {"instructor_line": "Synthetic instructor line.", "honesty": "Synthetic honesty."},
    "instructor": {
        "heading": "Synthetic instructor",
        "paragraphs": ["Synthetic instructor paragraph."],
        "photo": "images/academy/portrait.jpg",
        "photo_alt": "Synthetic portrait",
        "proof": [{"label": "SYNTHETIC PROOF", "href": "https://example.invalid/proof"}],
    },
    "project_heading": "Synthetic project",
    "fit": {
        "for_title": "Synthetic for",
        "not_title": "Synthetic not for",
        "for_shared": ["Synthetic shared fit."],
        "not": ["Start with [synthetic courses](https://example.invalid/courses)."],
    },
    "syllabus": {
        "heading": "Synthetic syllabus",
        "intro": "Synthetic intro.",
        "produce_label": "You make:",
    },
    "funding": {
        "heading": "Synthetic funding",
        "paragraphs": ["Synthetic funding paragraph."],
        "link": {"label": "Synthetic funding link", "href": "https://example.invalid/funding"},
    },
    "admission": {
        "heading": "Synthetic admission",
        "body": "Synthetic admission body.",
        "steps_heading": "Synthetic steps",
        "steps": [{"title": "Step one.", "text": "Synthetic step."}],
    },
    "faq": {
        "heading": "Synthetic questions",
        "items": [
            {
                "question": "Synthetic shared question?",
                "answer": "See the [privacy notice](/academy/privacy/).",
            },
            {"question": "What do I need to know?"},
        ],
    },
    "join": {"heading": "Synthetic join", "text": "Synthetic join text."},
    "footer": {
        "links": [{"label": "Synthetic FAQ", "href": "/academy/faq/"}],
        "copyright": "Synthetic copyright",
        "other_track": "Synthetic other track?",
    },
    "signup": {
        "program_label": "Synthetic program",
        "name_label": "Synthetic name",
        "name_placeholder": "Synthetic placeholder",
        "payment_label": "Synthetic payment",
        "payment_placeholder": "Synthetic choose",
        "payment_options": ["Bildungsgutschein", "Self-funded"],
        "payment_error": "Synthetic payment error.",
        "email_label": "Synthetic email",
        "email_placeholder": "you@example.invalid",
        "button": "Synthetic join",
        "busy": "Synthetic joining",
        "email_error": "Synthetic email error.",
        "failed": f"Synthetic failure, write to [{CONTACT}](mailto:{CONTACT}).",
        "noscript": "Synthetic noscript.",
        "micro": "Synthetic micro.",
        "privacy_label": "Synthetic privacy",
        "success": {
            "eyebrow": "Synthetic inbox",
            "title": "Synthetic confirm your email",
            "text": "Synthetic success text.",
            "questions_label": "Synthetic questions",
            "questions": [
                {
                    "key": "location",
                    "question": "Synthetic where?",
                    "options": _choice("berlin", "germany"),
                },
                {
                    "key": "german_level",
                    "question": "Synthetic German?",
                    "options": _choice("b1", "native"),
                },
            ],
            "answers_error": "Synthetic answer error.",
            "survey_text": "Synthetic survey text.",
            "survey_button": "Synthetic survey",
            "survey_url": "https://example.invalid/survey?usp=pp_url",
        },
    },
}


def _program(slug: str, name: str, position: int) -> dict[str, Any]:
    return {
        "layout": "program",
        "path": f"/academy/{slug}/",
        "title": f"{name} synthetic title",
        "description": f"{name} synthetic description.",
        "og_image": f"images/academy/og/{slug}.png",
        "sitemap": position,
        "program": {
            "slug": slug,
            "name": name,
            "label": name[0],
            "survey_option": f"{name[0]}. {name}",
        },
        "hero": {
            "eyebrow": "Synthetic eyebrow",
            "headline": f"{name} synthetic headline",
            "lead": f"{name} synthetic lead.",
            "basis": f"{name} synthetic basis.",
        },
        "outcomes": {
            "heading": f"{name} synthetic outcomes",
            "items": [f"{name} outcome {n}." for n in range(1, 6)],
        },
        "project": f"{name} synthetic project.",
        "fit_first": f"{name} synthetic first fit.",
        "prerequisites": f"{name} synthetic prerequisites.",
        "syllabus": [
            {
                "weeks": "Weeks 1 to 2",
                "title": f"{name} first group",
                "modules": [
                    {"title": f"{name} module one.", "produce": "a synthetic result."},
                    {
                        "title": f"{name} module two.",
                        "produce": "another synthetic result.",
                        "description": "Synthetic module description.",
                        "topics": ["Synthetic topic."],
                    },
                ],
            },
            {
                "weeks": "Weeks 3 to 4",
                "title": f"{name} second group",
                "modules": [{"title": f"{name} module three.", "produce": "a third result."}],
            },
        ],
        "schema": {
            "start_date": "2027-04",
            "course_modes": ["full-time"],
            "location": "Berlin",
            "country": "DE",
            "instructor": "Synthetic Instructor",
            "provider": "DataTalks.Club",
            "provider_url": "https://datatalks.club",
        },
    }


def _session(number: int) -> dict[str, Any]:
    return {
        "id": f"session-{number}",
        "number": number,
        "date_label": f"Mon {number} Synthetic",
        "weekday_date": f"Monday {number} Synthetic",
        "starts": f"2026-11-2{number}T16:00:00Z",
        "ends": f"2026-11-2{number}T17:00:00Z",
        "title": f"Synthetic session {number}, a talk",
        "hero_topic": f"Synthetic topic {number}",
        "short_label": f"Synthetic session {number}",
        "description": "Synthetic intro.\n\n- first point\n- second point\n\nSynthetic close.",
        "zoom_url": f"https://example.invalid/zoom/{number}",
        "meetup_url": f"https://example.invalid/meetup/{number}",
    }


PAGES: dict[str, tuple[dict[str, Any], str]] = {
    "index": (
        {
            "layout": "home",
            "path": "/academy/",
            "title": "Synthetic Academy home",
            "description": "Synthetic home description.",
            "og_image": "images/academy/og/home.png",
            "sitemap": 1,
            "hero": {
                "eyebrow": "Synthetic Academy",
                "headline": "Synthetic home headline",
                "lead": "Synthetic home lead.",
                "links": [{"label": "Synthetic sessions link", "href": "/academy/sessions/"}],
            },
            "tracks": {
                "label": "Synthetic tracks",
                "button_prefix": "Read about",
                "outcomes": [1, 3, 5],
            },
            "funding": {
                "heading": "Synthetic home funding",
                "paragraphs": ["Synthetic home funding text."],
            },
            "schema": {
                "organization": {
                    "name": "DataTalks.Club",
                    "url": "https://datatalks.club",
                    "same_as": ["https://example.invalid/profile"],
                }
            },
        },
        "",
    ),
    "agentic-engineering": (_program("agentic-engineering", "Agentic Engineering", 2), ""),
    "ai-engineering": (_program("ai-engineering", "AI Engineering", 3), ""),
    "sessions": (
        {
            "layout": "sessions",
            "path": "/academy/sessions/",
            "title": "Synthetic sessions title",
            "description": "Synthetic sessions description.",
            "og_image": "images/academy/og/sessions.png",
            "sitemap": 4,
            "series": {
                "name": "Synthetic Series",
                "calendar_slug": "synthetic-series",
                "hero_time": "17:00 Berlin",
            },
            "hero": {
                "eyebrow": "Synthetic sessions eyebrow",
                "headline": "Synthetic Series",
                "lead": "Synthetic sessions lead.",
                "button": "Synthetic choose sessions",
                "under_button": "Synthetic under button.",
                "speaker_line": "Synthetic speaker line.",
                "speaker_photo": "images/academy/portrait.jpg",
                "speaker_alt": "Synthetic speaker",
            },
            "sessions": [_session(1), _session(2), _session(3)],
            "rsvp_published": False,
            "audience": {
                "heading": "Synthetic audience",
                "intro": "Synthetic audience intro:",
                "items": ["Synthetic audience item."],
                "outro": "Synthetic audience outro.",
                "note": "Synthetic [note link](https://example.invalid/note).",
            },
            "schedule": {"heading": "Synthetic schedule", "after": "Synthetic after."},
            "host": {"heading": "Synthetic host", "text": "Synthetic host text."},
            "numbers": {
                "heading": "Synthetic numbers",
                "items": ["Synthetic [number](https://example.invalid/n)."],
            },
            "faq": {
                "heading": "Synthetic session questions",
                "items": [{"question": "Synthetic free?", "answer": "Synthetic yes."}],
            },
            "final": {"heading": "Synthetic choose your sessions", "text": "Synthetic final text."},
            "form": {
                "email_label": "Synthetic session email",
                "email_placeholder": "you@example.invalid",
                "email_error": "Synthetic session email error.",
                "name_label": "Synthetic first name",
                "sessions_label": "Synthetic which sessions?",
                "sessions_error": "Synthetic pick one.",
                "track_label": "Synthetic track?",
                "tracks": _choice("agentic", "ai", "both", "not_sure"),
                "status_label": "Synthetic status?",
                "statuses": _choice("job_hunting", "employed_upskilling", "other"),
                "years_label": "Synthetic years",
                "based_label": "Synthetic based?",
                "based": _choice("berlin", "germany", "outside"),
                "bildung_label": "Synthetic voucher?",
                "bildung": _choice("yes", "no", "dont_know"),
                "free_text_label": "Synthetic learn?",
                "optin_label": "Synthetic opt in",
                "micro": "Synthetic session micro.",
                "button": "Synthetic register",
                "busy": "Synthetic sending",
                "failed": "Synthetic session failure.",
                "noscript": "Synthetic session noscript.",
                "privacy_label": "Synthetic privacy",
                "success": {
                    "eyebrow": "Synthetic inbox",
                    "title": "Synthetic you are in",
                    "text": "Synthetic session success.",
                    "rsvp_heading": "Synthetic RSVP",
                    "rsvp_text": "Synthetic RSVP text.",
                    "rsvp_button": "Synthetic RSVP: {date}",
                    "calendar_heading": "Synthetic calendar",
                    "calendar_all": "Synthetic all sessions",
                },
            },
            "calendar": {
                "prodid": "-//Synthetic//Series//EN",
                "uid_domain": "series.example.invalid",
                "stamp": "20261114T120000Z",
                "zoom_label": "Zoom link:",
                "note": "Synthetic calendar note, with a comma.",
                "alarm_prefix": "Starting in 15 minutes:",
            },
            "footer_links": [{"label": "Synthetic Impressum", "href": "/academy/impressum/"}],
            "schema": {
                "location_name": "Zoom",
                "organizer": "DataTalks.Club",
                "organizer_url": "https://datatalks.club",
                "performer": "Synthetic Speaker",
            },
        },
        "",
    ),
    "sessions-materials": (
        {
            "layout": "materials",
            "path": "/academy/sessions/materials/",
            "title": "Synthetic materials title",
            "description": "Synthetic materials description.",
            "og_image": "images/academy/og/sessions.png",
            "noindex": True,
            "heading": "Synthetic get the recording",
            "text": "Synthetic materials text.",
            "button": "Synthetic send recording",
            "busy": "Synthetic sending",
            "success": "Synthetic materials success.",
            "failed": "Synthetic materials failure.",
            "noscript": "Synthetic materials noscript.",
            "micro": "Synthetic materials micro.",
        },
        "",
    ),
    "confirm": (
        {
            "layout": "confirm",
            "path": "/academy/confirm/",
            "title": "Synthetic confirm title",
            "description": "Synthetic confirm description.",
            "noindex": True,
            "eyebrow": "Synthetic confirmation",
            "heading": "Synthetic confirming",
            "message": "Synthetic checking.",
            "back": "Synthetic back",
            "states": {
                "error_eyebrow": "Synthetic error",
                "confirmed_eyebrow": "Synthetic confirmed",
                "missing_title": "Synthetic missing title",
                "missing_message": "Synthetic missing message.",
                "unconfigured_title": "Synthetic unconfigured title",
                "unconfigured_message": "Synthetic unconfigured message.",
                "confirmed_title": "Synthetic confirmed title",
                "confirmed_message": "Synthetic confirmed message.",
                "invalid_title": "Synthetic invalid title",
                "invalid_message": "Synthetic invalid message.",
                "failed_title": "Synthetic failed title",
                "failed_message": "Synthetic failed message.",
            },
        },
        "",
    ),
    "bildungsgutschein": (
        {
            "layout": "guide",
            "path": "/academy/bildungsgutschein/",
            "lang": "de",
            "hreflang": True,
            "title": "Synthetischer Leitfaden",
            "description": "Synthetische Beschreibung.",
            "sitemap": 5,
            "hero": {"eyebrow": "Synthetic Academy", "headline": "Synthetischer Leitfaden"},
            "intro": {
                "heading": "Synthetische erste Frage?",
                "paragraphs": ["Synthetischer Vorspann.", "Synthetischer zweiter Absatz."],
            },
            "facts": {
                "label": "Synthetische Fakten",
                "items": [{"label": "START", "value": "Synthetisch"}],
            },
            "programs": {
                "items": [
                    {
                        "slug": "agentic-engineering",
                        "heading": "Synthetische Programmfrage?",
                        "summary": "Synthetische Zusammenfassung.",
                        "basis": "Synthetische Grundlage.",
                        "button": "Synthetisch Agentic",
                    }
                ],
            },
            "fit": {"heading": "Synthetisch für wen?", "paragraphs": ["Synthetisch passend."]},
            "funding": {
                "heading": "Synthetische Förderfrage?",
                "paragraphs": [
                    "Synthetischer Absatz.",
                    "Siehe [Synthetischer Link](https://example.invalid/guide).",
                ],
            },
            "faq": {
                "heading": "Synthetische häufige Fragen",
                "items": [{"question": "Synthetische Frage?", "answer": "Synthetische Antwort."}],
            },
            "join": {
                "heading": "Synthetisch beitreten",
                "text": "Synthetischer Text.",
                "action": {
                    "label": "Synthetisch auf die Liste",
                    "href": "/academy/?utm_source=synthetic&utm_medium=article#signup-hero",
                },
                "links": [
                    {"label": "Synthetisch Agentic", "href": "/academy/agentic-engineering/"}
                ],
            },
        },
        "",
    ),
    "faq": (
        {
            "layout": "faq",
            "path": "/academy/faq/",
            "title": "Synthetic FAQ title",
            "description": "Synthetic FAQ description.",
            "og_image": "images/academy/og/home.png",
            "sitemap": 6,
            "eyebrow": "Synthetic Academy",
            "heading": "Synthetic FAQ",
            "lead": "Synthetic FAQ lead.",
            "button": "Synthetic join",
            "list_label": "Synthetic questions and answers",
            "items": [{"question": "Synthetic FAQ question?", "answer": "Synthetic FAQ answer."}],
        },
        "",
    ),
    "privacy": (
        {
            "layout": "legal",
            "path": "/academy/privacy/",
            "title": "Synthetic privacy title",
            "description": "Synthetic privacy description.",
            "sitemap": 7,
            "heading": "Synthetic privacy",
        },
        "## 1. Synthetic section\n\n"
        "Synthetic body with `utm_*` and [mail](mailto:test@example.invalid).\n",
    ),
    "impressum": (
        {
            "layout": "legal",
            "path": "/academy/impressum/",
            "lang": "de",
            "title": "Synthetisches Impressum",
            "description": "Synthetische Beschreibung.",
            "sitemap": 8,
            "heading": "Synthetisches Impressum",
        },
        "Synthetische Straße 1\\\n10000 Berlin\n",
    ),
}

IMAGES = {
    "images/academy/portrait.jpg": JPEG,
    "images/academy/og/home.png": PNG,
    "images/academy/og/agentic-engineering.png": PNG,
    "images/academy/og/ai-engineering.png": PNG,
    "images/academy/og/sessions.png": PNG,
    "images/academy/qr/session-1.svg": SVG,
}


def _dump(value: Any) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=10_000)


def academy_tree(
    pages: dict[str, tuple[dict[str, Any], str]] | None = None,
    shared: dict[str, Any] | None = None,
) -> dict[str, bytes]:
    """The synthetic tree as relative path -> bytes."""

    tree = {"academy/shared.yaml": _dump(shared or SHARED).encode()}
    for name, (metadata, body) in (pages or PAGES).items():
        text = "---\n" + _dump(metadata) + "---\n" + (f"\n{body}" if body else "")
        tree[f"academy/{name}.md"] = text.encode()
    tree.update(IMAGES)
    return tree


def pages_copy() -> dict[str, tuple[dict[str, Any], str]]:
    return copy.deepcopy(PAGES)


def write_tree(tree: dict[str, bytes]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="academy-content-test-"))
    for relative, payload in tree.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


def git_repository(tree: dict[str, bytes]) -> Path:
    root = write_tree(tree)
    identity = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid")
    subprocess.run(("git", "init", "-q", str(root)), check=True)
    subprocess.run(("git", "-C", str(root), "add", "-A"), check=True)
    subprocess.run(("git", "-C", str(root), *identity, "commit", "-q", "-m", "fixture"), check=True)
    return root


def editorial_source():
    from community_base.content_sync.models import ContentSource

    return ContentSource.objects.get_or_create(
        slug="dtc-content",
        defaults={
            "repo_name": "DataTalksClub/content",
            "webhook_secret": "test-secret",
            "max_files": 1000,
        },
    )[0]


def publish(tree: dict[str, bytes] | None = None) -> None:
    """Publish the tree through the Academy and media parsers, as a sync does."""

    from community_base.content_sync.checkout import ImmutableCheckout
    from community_base.content_sync.media import media_store
    from community_base.content_sync.parsers import get_parser

    source = editorial_source()
    with ImmutableCheckout(write_tree(tree or academy_tree()), commit_sha="a" * 40) as checkout:
        for kind in ("academy_shared", "academy_page", "media"):
            parser = get_parser(kind)
            for item in parser.discover(checkout, source):
                if kind == "media" and not item.key.startswith("images/academy/"):
                    continue
                parser.upsert(item, source, media_store())
