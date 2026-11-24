"""What an Academy page needs to draw, from its synced record and the shared copy.

Every word comes from the synced rows (``content.catalogue.academy_page`` and
``academy_shared``); this module only arranges them: it resolves which program
a page belongs to, picks the outcomes a home panel shows, numbers the syllabus,
splits a session description into its paragraphs and bullet list, and writes
the structured data a search engine reads.  It invents no copy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils.safestring import SafeString, mark_safe

Record = Mapping[str, Any]

SITE_ORIGIN = "https://datatalks.club"
ACADEMY_ROOT = "/academy/"
#: The sessions are held in Berlin; their structured data names local times.
SESSION_ZONE = ZoneInfo("Europe/Berlin")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
#: The layouts that draw the waitlist card (``#signup-hero``) themselves.
WAITLIST_LAYOUTS = frozenset({"home", "program"})
#: The layouts whose Academy bar carries the tagline, as the retired site's did;
#: the sessions series, its QR form and the legal pages show the bar without it.


def absolute(path: str) -> str:
    return f"{SITE_ORIGIN}{path}" if path else ""


def plain(text: str) -> str:
    """Markdown link syntax reduced to its label, for structured data."""

    return _MARKDOWN_LINK.sub(lambda match: match.group(1), text)


def json_ld(items: Sequence[Mapping[str, Any]]) -> list[SafeString]:
    """Each item as a script-safe JSON document."""

    rendered = []
    for item in items:
        text = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        rendered.append(mark_safe(text))  # noqa: S308 - escaped JSON, not markup
    return rendered


def programs(pages: Sequence[Record]) -> list[dict[str, Any]]:
    """The program pages, in their sitemap order."""

    held = [page for page in pages if page.get("layout") == "program"]
    held.sort(key=lambda page: (page.get("sitemap") or 0, page["public_path"]))
    return [
        {
            **page["data"]["program"],
            "path": page["public_path"],
            "headline": page["data"]["hero"]["headline"],
            "outcomes": page["data"]["outcomes"]["items"],
        }
        for page in held
    ]


def session_blocks(description: str) -> list[dict[str, Any]]:
    """A session description as paragraphs and bullet lists (``- `` lines)."""

    blocks: list[dict[str, Any]] = []
    for chunk in description.split("\n\n"):
        lines = chunk.split("\n")
        if all(line.startswith("- ") for line in lines):
            blocks.append({"bullets": [line[2:] for line in lines]})
        else:
            blocks.append({"text": chunk})
    return blocks


def _berlin(instant: str) -> str:
    moment = datetime.fromisoformat(instant.replace("Z", "+00:00"))
    return moment.astimezone(SESSION_ZONE).isoformat()


def _faq_schema(items: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": item["question"],
                "acceptedAnswer": {"@type": "Answer", "text": plain(item["answer"])},
            }
            for item in items
        ],
    }


def _program_faq(shared: Record, prerequisites: str) -> list[dict[str, str]]:
    return [
        {"question": item["question"], "answer": item.get("answer") or prerequisites}
        for item in shared["faq"]["items"]
    ]


def page_context(page: Record, shared: Record, pages: Sequence[Record]) -> dict[str, Any]:
    data = page["data"]
    layout = page["layout"]
    all_programs = programs(pages)
    by_path = {item["public_path"]: item for item in pages}
    context: dict[str, Any] = {
        "page": page,
        "data": data,
        "shared": shared,
        "html_lang": page["lang"],
        "canonical_url": absolute(page["public_path"]),
        "og_image_url": absolute(page["og_image"]),
        "academy_root": ACADEMY_ROOT,
        "programs": all_programs,
        "program_slugs": " ".join(item["slug"] for item in all_programs),
        "api_origin": settings.ACADEMY_API_ORIGIN,
        "consent_version": settings.ACADEMY_CONSENT_VERSION,
        "is_home": page["public_path"] == ACADEMY_ROOT,
    }
    schema: list[dict[str, Any]] = []

    if layout == "home":
        organization = data["schema"]["organization"]
        schema.append(
            {
                "@context": "https://schema.org",
                "@type": "Organization",
                "name": organization["name"],
                "url": organization["url"],
                "sameAs": organization["same_as"],
            }
        )
        picks = data["tracks"]["outcomes"]
        context["tracks"] = [
            {
                **program,
                "highlights": [
                    program["outcomes"][index - 1]
                    for index in picks
                    if 0 < index <= len(program["outcomes"])
                ],
                "button": f"{data['tracks']['button_prefix']} {program['name']}",
            }
            for program in all_programs
        ]

    elif layout == "program":
        program = data["program"]
        number = 0
        syllabus = []
        for group in data["syllabus"]:
            modules = []
            for module in group["modules"]:
                number += 1
                modules.append({**module, "number": f"{number:02d}"})
            syllabus.append({**group, "modules": modules})
        faq = _program_faq(shared, data["prerequisites"])
        context.update(
            {
                "program": program,
                "fit_for": [data["fit_first"], *shared["fit"]["for_shared"]],
                "syllabus": syllabus,
                "faq": faq,
                "other_program": next(
                    (item for item in all_programs if item["slug"] != program["slug"]), None
                ),
            }
        )
        course = data["schema"]
        course_url = absolute(page["public_path"])
        schema.append(
            {
                "@context": "https://schema.org",
                "@type": "Course",
                "name": program["name"],
                "description": data["hero"]["lead"],
                "url": course_url,
                "inLanguage": page["lang"],
                "provider": {
                    "@type": "Organization",
                    "name": course["provider"],
                    "url": course["provider_url"],
                },
                "hasCourseInstance": [
                    {
                        "@type": "CourseInstance",
                        "courseMode": course["course_modes"],
                        "inLanguage": page["lang"],
                        "startDate": course["start_date"],
                        "location": [
                            {"@type": "VirtualLocation", "url": course_url},
                            {
                                "@type": "Place",
                                "name": course["location"],
                                "address": {
                                    "@type": "PostalAddress",
                                    "addressLocality": course["location"],
                                    "addressCountry": course["country"],
                                },
                            },
                        ],
                        "instructor": {"@type": "Person", "name": course["instructor"]},
                    }
                ],
            }
        )
        schema.append(_faq_schema(faq))

    elif layout == "sessions":
        context.update(sessions_context(page))
        events = data["schema"]
        for session in data["sessions"]:
            schema.append(
                {
                    "@context": "https://schema.org",
                    "@type": "Event",
                    "name": session["title"],
                    "description": session["description"],
                    "startDate": _berlin(session["starts"]),
                    "endDate": _berlin(session["ends"]),
                    "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
                    "eventStatus": "https://schema.org/EventScheduled",
                    "isAccessibleForFree": True,
                    "inLanguage": page["lang"],
                    "location": {
                        "@type": "VirtualLocation",
                        "name": events["location_name"],
                        "url": absolute(page["public_path"]),
                    },
                    "organizer": {
                        "@type": "Organization",
                        "name": events["organizer"],
                        "url": events["organizer_url"],
                    },
                    "performer": {"@type": "Person", "name": events["performer"]},
                }
            )

    elif layout == "materials":
        sessions_page = sessions_page_of(pages)
        if sessions_page is None:
            raise LookupError("the materials page needs the sessions page")
        context.update(sessions_context(sessions_page))

    elif layout == "guide":
        context["guide_programs"] = [
            {**item, "path": f"{ACADEMY_ROOT}{item['slug']}/"} for item in data["programs"]["items"]
        ]

    elif layout == "faq":
        schema.append(_faq_schema(data["items"]))

    elif layout == "article":
        context["published"] = date.fromisoformat(data["published"])
        schema.append(
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": data["heading"],
                "description": page["description"],
                "url": absolute(page["public_path"]),
                "mainEntityOfPage": absolute(page["public_path"]),
                "inLanguage": page["lang"],
                "datePublished": data["published"],
                "author": {"@type": "Person", "name": data["author"]},
                "publisher": {"@type": "Organization", "name": shared["brand"], "url": SITE_ORIGIN},
                **({"image": absolute(page["og_image"])} if page["og_image"] else {}),
            }
        )

    context["json_ld"] = json_ld(schema)
    context["sessions_path"] = next(
        (path for path, item in by_path.items() if item.get("layout") == "sessions"), ""
    )
    return context


def sessions_page_of(pages: Sequence[Record]) -> Record | None:
    return next((page for page in pages if page.get("layout") == "sessions"), None)


def sessions_context(page: Record) -> dict[str, Any]:
    data = page["data"]
    sessions = [
        {**session, "blocks": session_blocks(session["description"])}
        for session in data["sessions"]
    ]
    return {
        "sessions_page": page,
        "sessions_data": data,
        "sessions": sessions,
        "session_ids": " ".join(session["id"] for session in sessions),
        "calendar_all_path": f"{page['public_path']}{data['series']['calendar_slug']}.ics",
    }
