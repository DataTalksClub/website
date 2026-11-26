"""What an Academy page needs to draw, from its synced record and the shared copy.

Every word comes from the synced rows (``content.catalogue.academy_page`` and
``academy_shared``); this module only arranges them: it resolves which program
a page belongs to, picks the outcomes a home panel shows, lays the syllabus
out week by week, lists the articles, splits a session description into its
paragraphs and bullet list, and writes the structured data a search engine
reads.  It invents no copy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.safestring import SafeString, mark_safe

from academy.templatetags.academy_text import academy_inline, academy_markdown
from content import catalogue
from core.breadcrumbs import Trail, trail
from core.home_content import READING_WORDS_PER_MINUTE

Record = Mapping[str, Any]

SITE_ORIGIN = "https://datatalks.club"
ACADEMY_ROOT = "/academy/"
#: The sessions are held in Berlin; their structured data names local times.
SESSION_ZONE = ZoneInfo("Europe/Berlin")
#: How many of the newest articles the home page lists before its link to
#: the full index.
HOME_ARTICLES = 3
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
            "highlights": page["data"].get("highlights") or [],
        }
        for page in held
    ]


def syllabus_phases(phases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each phase with its modules gathered into the weeks they are taught in.

    A module names its week; two modules taught in the same week are one row.
    A syllabus still in the older module-numbered shape (no weeks) draws one
    row per module, labelled by its running number.
    """

    laid_out = []
    number = 0
    for phase in phases:
        weeks: list[dict[str, Any]] = []
        for module in phase["modules"]:
            number += 1
            week = module.get("week")
            if week is not None and weeks and weeks[-1]["week"] == week:
                weeks[-1]["modules"].append(module)
                continue
            weeks.append({"week": week, "number": f"{number:02d}", "modules": [module]})
        laid_out.append({"title": phase["title"], "weeks": weeks})
    return laid_out


def articles(pages: Sequence[Record]) -> list[dict[str, Any]]:
    """The article pages (recaps, guides), newest first."""

    held = [page for page in pages if page.get("layout") == "article"]
    held.sort(key=lambda page: (page["data"]["published"], page["public_path"]), reverse=True)
    return [
        {
            "path": page["public_path"],
            "heading": page["data"]["heading"],
            "lead": page["data"]["lead"],
            "published": date.fromisoformat(page["data"]["published"]),
        }
        for page in held
    ]


def author_credit(name: str) -> dict[str, str]:
    """An author as the credit the shared person chip draws.

    The person records are matched on the name the page gives, so a byline
    that names a person with a profile shows their portrait and links to it;
    any other name stays plain text.
    """

    for person in catalogue.people_by_slug().values():
        if (person.get("name") or person.get("title")) == name:
            return {"name": name, "public_path": person.get("public_path") or ""}
    return {"name": name}


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


def faq_items(items: Sequence[Mapping[str, str]], prefix: str) -> list[dict[str, str]]:
    """Academy question/answer pairs in the FAQ block's shape (core/_faq.html).

    An Academy answer is one line of inline Markdown (a link at most); it is
    rendered here so the shared block draws it like every other answer, and
    each item keeps the ``<prefix>-<n>`` anchor the page has always published.
    """

    return [
        {
            "id": f"{prefix}-{number}",
            "question": item["question"],
            "answer_html": f"<p>{academy_inline(item['answer'])}</p>",
        }
        for number, item in enumerate(items, start=1)
    ]


#: A body heading that opens the page's questions: "## FAQ" and its spellings.
_FAQ_HEADING = re.compile(
    r"^##[ \t]+(?P<heading>FAQs?|Frequently asked questions|Frequent questions|"
    r"Common questions)[ \t]*#*[ \t]*$",
    re.IGNORECASE,
)
_SECTION_HEADING = re.compile(r"^#{1,2}[ \t]+\S")
_QUESTION_HEADING = re.compile(r"^###[ \t]+(?P<question>.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")


def split_body_faq(body: str) -> dict[str, Any]:
    """A Markdown body with its "## FAQ" section lifted out as FAQ items.

    Guides and recaps close on a "## FAQ" heading followed by "### question"
    headings, each with its answer paragraphs.  That section is drawn by the
    shared FAQ block (and published as FAQPage data) instead of as prose, so
    it is read out of the body here, at render time, and the source stays as
    written.  A section that is not purely question headings with answers is
    left in the body untouched.  Returns ``body`` (before the section),
    ``body_after`` (whatever follows it), ``heading`` and ``items``.
    """

    lines = body.split("\n")
    in_fence = False
    start = None
    heading = ""
    for index, line in enumerate(lines):
        match = _FAQ_HEADING.match(line)
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and match:
            start, heading = index, match.group("heading")
            break
    unchanged = {"body": body, "body_after": "", "heading": "", "items": []}
    if start is None:
        return unchanged
    end = len(lines)
    in_fence = False
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and _SECTION_HEADING.match(line):
            end = index
            break
    items: list[dict[str, str]] = []
    answer: list[str] = []
    for line in lines[start + 1 : end]:
        question = _QUESTION_HEADING.match(line)
        if question:
            if items:
                items[-1]["answer"] = "\n".join(answer).strip()
            items.append({"question": question.group("question"), "answer": ""})
            answer = []
        elif items:
            answer.append(line)
        elif line.strip():
            return unchanged
    if not items:
        return unchanged
    items[-1]["answer"] = "\n".join(answer).strip()
    if not all(item["answer"] for item in items):
        return unchanged
    return {
        "body": "\n".join(lines[:start]).rstrip() + "\n",
        "body_after": "\n".join(lines[end:]).strip(),
        "heading": heading,
        "items": [
            {
                "id": f"faq-{number}",
                "question": item["question"],
                "answer_html": str(academy_markdown(item["answer"])),
            }
            for number, item in enumerate(items, start=1)
        ],
    }


def _program_faq(shared: Record, prerequisites: str) -> list[dict[str, str]]:
    return [
        {"question": item["question"], "answer": item.get("answer") or prerequisites}
        for item in shared["faq"]["items"]
    ]


#: Pages that are steps of a flow rather than places in the Academy: the email
#: confirmation landing is reached from a link in a message, not from a parent.
UNTRAILED_LAYOUTS = frozenset({"home", "confirm"})


def crumb_label(page: Record) -> str:
    """What a page is called as a level of a trail: its name, not its pitch.

    A program is its course name ("Agentic Engineering"), a landing with a
    marketing hero its headline's subject, and a page with a plain heading
    that heading.
    """

    data = page["data"]
    if page["layout"] == "program":
        return data["program"]["name"]
    if page["layout"] in {"sessions", "guide"}:
        return data["hero"]["headline"]
    return data.get("heading") or page["title"]


def page_trail(page: Record, shared: Record, pages: Sequence[Record]) -> Trail | None:
    """The Academy root, the nearest page whose path holds this one, and the page.

    The parent is read from the URL rather than declared, so a session recap
    under ``/academy/sessions/`` sits under the sessions page and an article
    under ``/academy/guides/`` under the article index, without a field that
    could disagree with the path.
    """

    if page["layout"] in UNTRAILED_LAYOUTS:
        return None
    path = page["public_path"]
    parents = [
        item
        for item in pages
        if item["public_path"] != ACADEMY_ROOT
        and item["public_path"] != path
        and path.startswith(item["public_path"])
        and item["layout"] not in UNTRAILED_LAYOUTS
    ]
    levels = [(shared["brand"], ACADEMY_ROOT)]
    if parents:
        parent = max(parents, key=lambda item: len(item["public_path"]))
        levels.append((crumb_label(parent), parent["public_path"]))
    levels.append((crumb_label(page), path))
    return trail(*levels)


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
        "breadcrumbs": page_trail(page, shared, pages),
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
                "highlights": program["highlights"]
                or [
                    program["outcomes"][index - 1]
                    for index in picks
                    if 0 < index <= len(program["outcomes"])
                ],
                "button": f"{data['tracks']['button_prefix']} {program['name']}",
            }
            for program in all_programs
        ]
        context["articles"] = articles(pages)[:HOME_ARTICLES]
        sessions_page = sessions_page_of(pages)
        if data.get("sessions") and sessions_page is not None:
            context["home_sessions"] = sessions_context(sessions_page)["sessions"]
        faq_page = next((item for item in pages if item.get("layout") == "faq"), None)
        if data.get("faq") and faq_page is not None:
            questions = faq_page["data"]["items"]
            context["home_faq"] = faq_items(
                [
                    questions[index - 1]
                    for index in data["faq"]["picks"]
                    if 0 < index <= len(questions)
                ],
                "faq",
            )
            context["faq_path"] = faq_page["public_path"]

    elif layout == "program":
        program = data["program"]
        faq = _program_faq(shared, data["prerequisites"])
        context.update(
            {
                "program": program,
                "fit_for": [data["fit_first"], *shared["fit"]["for_shared"]],
                "syllabus": syllabus_phases(data["syllabus"]),
                "capstone_weeks": (
                    syllabus_phases([{"title": "", **data["capstone"]}])[0]["weeks"]
                    if data.get("capstone")
                    else []
                ),
                "interview_weeks": (
                    syllabus_phases([{"title": "", **shared["interviews"]}])[0]["weeks"]
                    if shared.get("interviews")
                    else []
                ),
                "faq": faq_items(faq, "faq"),
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

    elif layout == "sessions":
        context.update(sessions_context(page))
        if data.get("faq"):
            context["faq_items"] = faq_items(data["faq"]["items"], "faq")
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
        context["faq_items"] = faq_items(data["items"], "question")

    elif layout == "articles":
        context["articles"] = articles(pages)

    elif layout == "article":
        # Questions come from the record's own `faq` section or, in a guide or
        # recap, from the "## FAQ" section that closes its Markdown body.
        split = split_body_faq(page["body"])
        context["article_body"] = split["body"]
        context["article_body_after"] = split["body_after"]
        context["faq_heading"] = (data.get("faq") or {}).get("heading") or split["heading"]
        context["faq_items"] = [
            {**item, "id": f"faq-{number}"}
            for number, item in enumerate(
                [*faq_items((data.get("faq") or {}).get("items") or [], "faq"), *split["items"]],
                start=1,
            )
        ]
        context["published"] = date.fromisoformat(data["published"])
        context["author"] = author_credit(data["author"])
        # Written the way the blog article writes its estimate.
        words = len(page["body"].split())
        context["reading_time"] = f"{max(1, round(words / READING_WORDS_PER_MINUTE))} min read"
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

    context["alternates"] = [
        {"lang": item["lang"], "href": absolute(item["path"])}
        for item in data.get("alternates") or []
    ]
    context["json_ld"] = json_ld(schema)
    context["articles_path"] = next(
        (path for path, item in by_path.items() if item.get("layout") == "articles"), ""
    )
    context["articles_index"] = by_path.get(context["articles_path"], {}).get("data", {})
    context["sessions_path"] = next(
        (path for path, item in by_path.items() if item.get("layout") == "sessions"), ""
    )
    return context


def sessions_page_of(pages: Sequence[Record]) -> Record | None:
    return next((page for page in pages if page.get("layout") == "sessions"), None)


def session_event_paths(series_slug: str) -> dict[int, str]:
    """Each session's event page, by session number.

    The free sessions are ordinary site events: people register on the event
    page, like for any other event.  The series' events belong to the event
    series whose slug is the sessions page's ``calendar_slug``, at the
    position of the session's number (``scripts/prod/import_academy_sessions.py``
    links them), so the lookup is one indexed query and never guesses by title
    or date.
    """

    from community_base.events.models import Event

    from events.lifecycle import PUBLISHED_STATUSES

    rows = Event.objects.filter(
        event_series__slug=series_slug,
        series_position__isnull=False,
        public_id__isnull=False,
        status__in=PUBLISHED_STATUSES,
    ).values_list("series_position", "public_id", "slug")
    return {position: f"/events/{public_id}/{slug}" for position, public_id, slug in rows}


def sessions_context(page: Record) -> dict[str, Any]:
    data = page["data"]
    now = timezone.now()
    event_paths = session_event_paths(data["series"]["calendar_slug"])
    sessions = [
        {
            **session,
            "blocks": session_blocks(session["description"]),
            # A session that has ended no longer takes registrations; its
            # recap, when it has one, is linked wherever it is listed.
            "past": datetime.fromisoformat(session["ends"].replace("Z", "+00:00")) < now,
            # Where to register: the session's own event page.
            "event_path": event_paths.get(session["number"], ""),
        }
        for session in data["sessions"]
    ]
    return {
        "sessions_page": page,
        "sessions_data": data,
        "sessions": sessions,
        "session_ids": " ".join(session["id"] for session in sessions),
        "calendar_all_path": f"{page['public_path']}{data['series']['calendar_slug']}.ics",
    }
