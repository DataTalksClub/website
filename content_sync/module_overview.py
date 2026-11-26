"""Parse a module ``README.md`` into the structured overview the module page shows.

The module README is the GitHub index of a schema-2 module.  Authors write it
in a few house styles (zoomcamp-ops ``STRUCTURE.md`` §3), and the module page
needs its parts as fields, not as one blob of Markdown: the intro, the lesson
groups with each lesson's one-line description, the original workshop
recordings, the module's code link and the community notes.  This parser runs
at import time only -- a request never parses a README.

It is a pure function of the README text and the module's lesson file names.
It never invents anything: a README without a section simply yields an empty
field.  Relative links are left as written; the importer resolves them against
the repository once it knows the branch.

Recognised shapes:

* lesson list items, numbered or bulleted, optionally with a ``2.1`` ordinal
  before or inside the link, and an optional description after `` - ``,
  `` — ``, `` – `` or ``: ``  (``1. [Search](05-search.md) - Building ...``);
* lesson headings, ``## 1.1 [Introduction](01-introduction.md)``, whose first
  paragraph is the lesson's description;
* a ``Code: [code/](code/)`` line, bulleted or not;
* a section whose heading mentions *recording* (workshop recordings) or
  *notes* (community notes), at heading level one or two.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from courses.services.curriculum_source import (
    ModuleOverviewSource,
    OverviewLessonSource,
    OverviewLinkSource,
    OverviewPartSource,
    OverviewSectionSource,
)

_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_ORDINAL = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
# A status note before the ordinal, as in ``- (Outdated) 9.3 [Title](file.md)``.
_LEADING_NOTE = re.compile(r"^\([^)]*\)\s*")
_DESCRIPTION_SEPARATOR = re.compile(r"^\s*(?:\((?P<note>[^)]*)\)\s*)?(?:[-—–:]\s+)(?P<text>.+)$")
_CODE_LINE = re.compile(r"^\s*(?:[-*+]\s+)?\**Code\**\s*:\s*\[[^\]]*\]\(([^)\s]+)\)")
_HTML_LINE = re.compile(r"^\s*</?[A-Za-z][^>]*>")
_NOTES_PLACEHOLDER = re.compile(r"add your notes", re.IGNORECASE)
_RECORDING_HEADING = re.compile(r"recording", re.IGNORECASE)
_NOTES_HEADING = re.compile(r"\bnotes\b", re.IGNORECASE)


def _blocks(markdown: str) -> list[tuple[str, list[str]]]:
    """Split on level-1/2 headings outside fenced code: ``[(heading, lines)]``.

    The first block has an empty heading and holds anything before the first
    heading; the README's own title heading is the first heading.
    """

    blocks: list[tuple[str, list[str]]] = [("", [])]
    fence: str | None = None
    for line in markdown.splitlines():
        fence_match = _FENCE.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
        heading = _HEADING.match(line) if fence is None else None
        if heading:
            blocks.append((heading.group(2).strip(), []))
        else:
            blocks[-1][1].append(line)
    return blocks


def _strip_markup(text: str) -> str:
    text = _LINK.sub(lambda match: match.group(1), text)
    return re.sub(r"\s+", " ", text.replace("**", "").replace("`", "")).strip()


def _lesson_from_link(
    text: str, lesson_slugs: Mapping[str, str]
) -> tuple[OverviewLessonSource, str] | None:
    """Return the lesson an index line links to, and the text after the link."""

    stripped = _ORDINAL.sub("", _LEADING_NOTE.sub("", text.strip()))
    match = _LINK.match(stripped)
    if match is None:
        return None
    target = match.group(2).split("#", 1)[0]
    slug = lesson_slugs.get(target.removeprefix("./"))
    if slug is None:
        return None
    title = _ORDINAL.sub("", _strip_markup(match.group(1)))
    return OverviewLessonSource(slug=slug, title=title, description=""), stripped[match.end() :]


def _description(rest: str) -> str:
    match = _DESCRIPTION_SEPARATOR.match(rest)
    if match is None:
        return ""
    return _strip_markup(match.group("text"))


def _paragraph(lines: list[str]) -> str:
    """Return the first plain paragraph of a section body."""

    collected: list[str] = []
    for line in lines:
        if not line.strip():
            if collected:
                break
            continue
        if _HTML_LINE.match(line) or line.lstrip().startswith((">", "```", "|", "!")):
            if collected:
                break
            continue
        collected.append(line.strip())
    return _strip_markup(" ".join(collected))


def _clean_markdown(lines: list[str]) -> str:
    """Drop raw HTML blocks, ``<details>`` wrappers and edge blank lines."""

    kept = [line.rstrip() for line in lines if not _HTML_LINE.match(line)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _links(lines: list[str]) -> list[OverviewLinkSource]:
    links: list[OverviewLinkSource] = []
    for line in lines:
        item = _LIST_ITEM.match(line)
        if item is None:
            continue
        text = item.group(1)
        if _NOTES_PLACEHOLDER.search(text):
            continue
        match = _LINK.search(text)
        if match is None:
            continue
        label = _strip_markup(text[: match.start()] + text[match.end() :])
        label = re.sub(r"^[\s:,–—-]+|[\s:,–—-]+$", "", label)
        links.append(
            OverviewLinkSource(label=label, title=_strip_markup(match.group(1)), url=match.group(2))
        )
    return links


def _lesson_list(
    lines: list[str], lesson_slugs: Mapping[str, str]
) -> tuple[str, list[OverviewLessonSource], list[str]] | None:
    """Split a body into (intro, lessons, remaining lines) when it lists lessons."""

    lessons: list[OverviewLessonSource] = []
    intro: list[str] = []
    rest: list[str] = []
    for line in lines:
        item = _LIST_ITEM.match(line)
        found = _lesson_from_link(item.group(1), lesson_slugs) if item else None
        if found is not None:
            lesson, after = found
            lessons.append(
                OverviewLessonSource(
                    slug=lesson.slug, title=lesson.title, description=_description(after)
                )
            )
        elif lessons:
            rest.append(line)
        else:
            intro.append(line)
    if not lessons:
        return None
    return _clean_markdown(intro), lessons, rest


def parse_module_overview(
    markdown: str, *, lesson_slugs: Mapping[str, str]
) -> ModuleOverviewSource:
    """Parse one module README.

    ``lesson_slugs`` maps each lesson file name in the module (``05-search.md``)
    to its lesson slug; only links to those files count as lesson entries.
    """

    blocks = _blocks(markdown.removeprefix("\ufeff"))
    # The first heading is the README title; text before it is furniture
    # (a cover image) and text after it, up to the next heading, is the intro.
    preamble = blocks[0][1] + (blocks[1][1] if len(blocks) > 1 else [])
    sections = blocks[2:] if len(blocks) > 1 else []

    code_target: str | None = None
    parts: list[OverviewPartSource] = []
    recordings: list[OverviewLinkSource] = []
    notes: list[OverviewLinkSource] = []
    other: list[OverviewSectionSource] = []

    def take_code(lines: list[str]) -> list[str]:
        nonlocal code_target
        kept = []
        for line in lines:
            match = _CODE_LINE.match(line)
            if match is not None:
                code_target = code_target or match.group(1)
                continue
            kept.append(line)
        return kept

    preamble = take_code(preamble)
    listed = _lesson_list(preamble, lesson_slugs)
    if listed is not None:
        intro_text, lessons, rest = listed
        # An unheaded lesson list ("Units:") belongs to the index, not the intro.
        intro_lines = [line for line in intro_text.splitlines() if line.strip() != "Units:"]
        intro = _clean_markdown(intro_lines + [""] + rest)
        parts.append(OverviewPartSource(heading="", intro_markdown="", lessons=tuple(lessons)))
    else:
        intro = _clean_markdown(preamble)

    heading_lessons: list[OverviewLessonSource] = []

    def flush_heading_lessons() -> None:
        if heading_lessons:
            parts.append(
                OverviewPartSource(heading="", intro_markdown="", lessons=tuple(heading_lessons))
            )
            heading_lessons.clear()

    for heading, lines in sections:
        lines = take_code(lines)
        heading_lesson = _lesson_from_link(heading, lesson_slugs)
        if heading_lesson is not None:
            lesson, _after = heading_lesson
            heading_lessons.append(
                OverviewLessonSource(
                    slug=lesson.slug, title=lesson.title, description=_paragraph(lines)
                )
            )
            continue
        flush_heading_lessons()
        plain_heading = _strip_markup(heading)
        if _RECORDING_HEADING.search(plain_heading):
            recordings.extend(_links(lines))
            continue
        if _NOTES_HEADING.search(plain_heading):
            notes.extend(_links(lines))
            continue
        listed = _lesson_list(lines, lesson_slugs)
        if listed is not None:
            part_intro, lessons, rest = listed
            parts.append(
                OverviewPartSource(
                    heading=plain_heading, intro_markdown=part_intro, lessons=tuple(lessons)
                )
            )
            leftover = _clean_markdown(rest)
            if leftover:
                other.append(OverviewSectionSource(heading=plain_heading, markdown=leftover))
            continue
        body = _clean_markdown(lines)
        if body:
            other.append(OverviewSectionSource(heading=plain_heading, markdown=body))
    flush_heading_lessons()

    return ModuleOverviewSource(
        intro_markdown=intro,
        parts=tuple(parts),
        recordings=tuple(recordings),
        notes=tuple(notes),
        sections=tuple(other),
        code_target=code_target,
    )


__all__ = ("parse_module_overview",)
