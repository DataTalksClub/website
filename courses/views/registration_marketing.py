"""Shape a registration campaign's marketing Markdown into page sections.

``RegistrationCampaign.marketing_markdown`` is one editor-owned Markdown blob.
Rendered as a single prose block it became a text wall under the hero, so the
registration page now presents the same copy as the site's own sections: each
heading of the source opens a section, and a section whose body is a list of a
recognisable shape is drawn with the matching component instead of bullets.

* a list whose every item reads ``Label: value`` is a **schedule** -- drawn as
  dashed definition rows, the label beside the date;
* a list whose every item opens with bold text is a **feature list** -- drawn
  as titled rows, the bold lead as the row title;
* anything else stays **prose**, rendered and sanitised exactly as before.

Nothing here adds or rewrites copy: every word on the page is still the
campaign's own, read through ``courses.registration.render_markdown`` (Markdown
plus the shared sanitiser).  A leading heading that only repeats the campaign
title is dropped, because the page already carries that title as its ``h1``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.utils.safestring import SafeString, mark_safe

from courses.registration import render_markdown

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM = re.compile(r"^[-*+]\s+")
_SCHEDULE_ITEM = re.compile(r"^(?P<label>[^:*\[\]`]{1,60}):\s+(?P<value>\S.*)$")
_FEATURE_ITEM = re.compile(r"^\*\*(?P<title>.+?)\*\*\s*(?P<body>.*)$")
_PARAGRAPH_WRAPPER = re.compile(r"^\s*<p>(?P<inner>.*)</p>\s*$", re.DOTALL)


@dataclass(frozen=True)
class MarketingRow:
    """One schedule or feature row: a label/title beside its value/body."""

    label: SafeString
    value: SafeString


@dataclass(frozen=True)
class MarketingSection:
    heading: str
    slug: str
    kind: str  # "schedule", "features" or "prose"
    html: SafeString = mark_safe("")
    rows: tuple[MarketingRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RegistrationMarketing:
    intro_html: SafeString
    sections: tuple[MarketingSection, ...]

    def __bool__(self) -> bool:
        return bool(self.intro_html.strip()) or bool(self.sections)


def _inline(markdown_text: str) -> SafeString:
    """Render one line of Markdown without the paragraph wrapper."""

    rendered = render_markdown(markdown_text)
    match = _PARAGRAPH_WRAPPER.match(rendered)
    return mark_safe((match.group("inner") if match else rendered).strip())  # noqa: S308 -- sanitised by render_markdown


def _split_sections(markdown_text: str) -> list[tuple[str | None, list[str]]]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    in_fence = False
    for line in markdown_text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            sections.append((heading.group(2).strip(), []))
        else:
            sections[-1][1].append(line)
    return sections


def _list_items(body: str) -> list[str] | None:
    """The body's top-level list items, or ``None`` when it is not only a list."""

    items: list[str] = []
    for line in body.strip().splitlines():
        if not line.strip():
            continue
        if _LIST_ITEM.match(line):
            items.append(_LIST_ITEM.sub("", line, count=1).strip())
        elif items and line.startswith((" ", "\t")):
            items[-1] = f"{items[-1]} {line.strip()}"
        else:
            return None
    return items or None


def _section(heading: str, body: str, index: int) -> MarketingSection | None:
    slug = re.sub(r"[^a-z0-9]+", "-", heading.casefold()).strip("-") or f"section-{index}"
    slug = f"registration-{slug}"
    items = _list_items(body)
    if items and len(items) > 1:
        schedule = [_SCHEDULE_ITEM.match(item) for item in items]
        if all(schedule):
            return MarketingSection(
                heading=heading,
                slug=slug,
                kind="schedule",
                rows=tuple(
                    MarketingRow(_inline(match["label"]), _inline(match["value"]))
                    for match in schedule
                    if match
                ),
            )
        features = [_FEATURE_ITEM.match(item) for item in items]
        if all(features):
            return MarketingSection(
                heading=heading,
                slug=slug,
                kind="features",
                rows=tuple(
                    MarketingRow(_inline(match["title"]), _inline(match["body"]))
                    for match in features
                    if match
                ),
            )
    html = render_markdown(body.strip())
    if not html.strip():
        return None
    return MarketingSection(
        heading=heading,
        slug=slug,
        kind="prose",
        html=mark_safe(html),  # noqa: S308 -- sanitised by render_markdown
    )


def registration_marketing(markdown_text: str, *, title: str) -> RegistrationMarketing:
    """Split the campaign copy into an intro and headed sections."""

    parts = _split_sections(markdown_text or "")
    intro_lines = parts[0][1]
    headed = parts[1:]
    if headed and headed[0][0] and headed[0][0].casefold() == title.strip().casefold():
        # The copy opens by restating the campaign title the page already shows
        # as its h1; that heading's body is the intro.
        intro_lines = [*intro_lines, *headed[0][1]]
        headed = headed[1:]
    intro_html = render_markdown("\n".join(intro_lines).strip())
    sections = tuple(
        section
        for index, (heading, lines) in enumerate(headed, start=1)
        if heading and (section := _section(heading, "\n".join(lines), index)) is not None
    )
    return RegistrationMarketing(
        intro_html=mark_safe(intro_html),  # noqa: S308 -- sanitised by render_markdown
        sections=sections,
    )


def _normalised_words(text: str) -> str:
    return " ".join(re.sub(r"[*_`>#\-]+", " ", text or "").split()).casefold()


def application_notice_in_copy(notice: str, markdown_text: str) -> bool:
    """Whether the campaign copy already carries the notice word for word.

    The application notice is an operational message and is shown above the
    form; it is only left out when the copy above already says exactly the same
    thing, so the page never prints one paragraph twice.
    """

    words = _normalised_words(notice)
    return bool(words) and words in _normalised_words(markdown_text)
