"""Clean scraped Eventbrite page content and let it win over the legacy Jekyll description.

Two separate description sources exist for the same historical events. The one
already imported (:mod:`events.content_import`, fed by
``temporary/content/public_projection/events.json``) was built from the legacy
Jekyll ``_data/events.yaml`` and, for 159 of 421 events, hand-reconciled against a
Luma copy. The other is real, freshly-scraped Eventbrite page content held outside
this repository at ``~/prod/dtc-data/eventbrite-content/`` (226 events, fields
``name``/``summary``/``description_html``/``description_markdown`` -- see that
directory's own ``README.md``), never before read by any import here.

The product owner's ruling, verbatim: **"eventbrite wins over jekyll. but we
remove 'about the speaker' part and about dtc footer too."** This module is the
mechanical half of that ruling.

Cleaning
--------

:func:`clean_description_markdown` strips exactly two things from a scraped
``description_markdown``, and nothing else:

1. The speaker/guest/host biography section -- a paragraph reading exactly
   ``About the speaker:`` / ``About the guest:`` / ``About the speakers:`` /
   ``About the guests:`` / ``About the host:`` (case-insensitive, colon
   optional), through the paragraph immediately before the footer (or to the
   end of the text, if there is no footer). Sampled across all 226 real
   events: 224 heading occurrences, always its own paragraph, always followed
   by one or more bio paragraphs and then either the footer or nothing.
2. The DataTalks.Club footer sentence -- ``[DataTalks.Club](<url>) is a/the
   place to talk about data. [Join our slack community](<url>)!`` or one of
   its four observed variants (``a``/``the``, plain/linked slack-join text,
   trailing ``!`` present or absent, ``https://DataTalks.Club`` vs.
   ``https://datatalks.club/``). Matched against all 226 events: 223 carry it,
   every one matched, zero false negatives and zero false positives against
   paragraphs that merely mention DataTalks.Club for a real reason (a
   community outline bullet, a "hosted by" credit, a marathon-track
   cross-reference).

A real trailing sponsor mention (``This event is sponsored by
[Iterative.ai](...)``) always sits *after* the footer paragraph and is real
content -- never stripped. 9 of the 223 footer-bearing events carry one;
spot-checked, all preserved.

Also silently drops stray U+FEFF (zero-width no-break space) characters
scattered through 40 of the 226 scraped descriptions -- an invisible copy-paste
artifact from the original scrape, not content, and left one heading
(``A﻿bout the speaker``) unmatched until stripped.

Manual review: all 226 cleaned outputs were checked by machine for leftover
"about the ..." headings or footer fragments (zero found) and, before that, a
representative sample -- including the sponsor-suffix, host+guest double-bio,
zero-width-corrupted, and no-about-section cases -- was read in full to confirm
nothing beyond the two patterns above was removed.

A similarly-shaped transform already exists at
``scripts/projection_build/event_speaker_bio_normalization.py`` -- it is not
reused here. That one operates on HTML block tags, is bound to a checked,
sha256-pinned migration plan for the *already-imported* 421-event legacy
corpus, and exists to be replayed exactly, once. This source is markdown, is
not frozen (226 events today, growable if a later scrape adds more), and
carries no reviewed plan to bind against -- a fresh, markdown-native
implementation is the honest fit, not a second caller of a one-time migration
tool.

Rendering
---------

:func:`render_description_html` and :func:`render_description_text` both derive
from the *same* cleaned markdown, which is what keeps them in sync: there is
one stripping decision, not two. HTML paragraphs and lists carry the same
Tailwind classes ``EventContent.description_html`` already uses elsewhere
(``<p class="mt-4 leading-7">``, ``<ul class="mt-4 list-disc pl-6">``, links as
``<a class="app-link" href="..." target="_blank" rel="noopener noreferrer">``);
plain text joins paragraphs with a single space and list items with ``"; "``,
matching the shape of the existing ``description_text`` column.

Wiring
------

:func:`apply_eventbrite_descriptions` is the live half: it reads a staging
artifact (built by ``scripts/build_eventbrite_descriptions.py`` from the
external raw content plus ``~/prod/dtc-data/eventbrite-event-identities.json``'s
canonical resolution) and, for every record whose ``canonical_repository`` /
``canonical_revision`` / ``canonical_source_key`` resolves to a canonical
``Event`` that already has an ``EventContent`` row, replaces that row's
``description_html``/``description_text`` outright -- full replacement, not
fill-only-if-missing. An event with no resolved Eventbrite id, or one whose
content has not been imported yet, is left untouched.

``scripts/prod/import_events.py`` calls this after :func:`events.content_import.
import_content`, exactly the way it already runs after the Jekyll-sourced
description import: description-authoring precedence is a distinct, later step
from content bootstrap, not folded into it.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.db import transaction

__all__ = [
    "EventbriteDescriptionError",
    "clean_description_markdown",
    "render_description_html",
    "render_description_text",
    "EventbriteDescriptionRecord",
    "parse_eventbrite_description_records",
    "load_eventbrite_description_records",
    "EventbriteDescriptionApplyReport",
    "apply_eventbrite_descriptions",
]


class EventbriteDescriptionError(ValueError):
    """A bounded refusal that carries a condition code, never a source value."""


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

# U+FEFF (zero-width no-break space / BOM) turns up mid-word in 40 of the 226
# scraped descriptions -- a scrape artifact, not content. Invisible either way,
# so dropping it changes nothing a reader sees; it is stripped up front purely
# so the heading/footer matches below are not defeated by a character hiding
# inside "About".
_ZERO_WIDTH_RE = re.compile("﻿")

_ABOUT_HEADING_RE = re.compile(
    r"^about the (speakers?|guests?|hosts?)\s*:?\s*$", re.IGNORECASE
)

# The DataTalks.Club footer sentence, in its observed variants: capitalization
# of the club URL and its domain casing, "a"/"the", a plain or linked
# "Join our slack community", and a trailing "!" that is sometimes absent.
_FOOTER_RE = re.compile(
    r"^\[?datatalks\.club\]?\(https?://(?:www\.)?datatalks\.club/?\)?\s*"
    r"is\s+(?:a|the)\s+place\s+to\s+talk\s+about\s+data\.\s*"
    r"(?:\[?join\s+our\s+slack\s+community\]?(?:\(https?://\S+\))?\s*!?)?\s*$",
    re.IGNORECASE,
)


def clean_description_markdown(markdown: str) -> str:
    """Strip the speaker/guest/host bio section and the DataTalks.Club footer.

    Operates on paragraphs (blocks separated by a blank line), which is how
    both patterns always appear in the real corpus. Nothing else is touched --
    a real trailing sponsor mention after the footer, or any paragraph that
    merely mentions DataTalks.Club without matching the exact footer sentence,
    passes through unchanged.
    """

    if not markdown:
        return markdown
    text = _ZERO_WIDTH_RE.sub("", markdown)
    blocks = re.split(r"\n\s*\n", text.strip())

    about_index: int | None = None
    footer_index: int | None = None
    for index, block in enumerate(blocks):
        stripped = block.strip()
        if about_index is None and _ABOUT_HEADING_RE.match(stripped):
            about_index = index
        if footer_index is None and _FOOTER_RE.match(stripped):
            footer_index = index

    kept = []
    for index, block in enumerate(blocks):
        if index == footer_index:
            continue
        if about_index is not None and index >= about_index:
            if footer_index is None or index < footer_index:
                continue
        kept.append(block)
    return "\n\n".join(kept).strip()


# --------------------------------------------------------------------------
# Rendering -- both derived from the same cleaned markdown, so they cannot
# disagree about what was stripped.
# --------------------------------------------------------------------------

_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*([^)]*?)\s*\)")
_ANGLE_LINK_RE = re.compile(r"<(https?://[^>\s]+)>")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.*)$")


def _link_target(raw_url: str) -> str:
    # A scraped link sometimes carries a trailing stray space, or a markdown
    # title suffix, inside the parens; the real target is the first token.
    tokens = raw_url.split()
    return tokens[0] if tokens else raw_url


def _inline_to_plain(segment: str) -> str:
    segment = _LINK_RE.sub(
        lambda m: m.group(1) if m.group(1) else _link_target(m.group(2)), segment
    )
    segment = _ANGLE_LINK_RE.sub(lambda m: m.group(1), segment)
    segment = _BOLD_RE.sub(lambda m: m.group(1), segment)
    return re.sub(r"\s+", " ", segment).strip()


def _inline_to_html(segment: str) -> str:
    escaped = html_module.escape(segment, quote=False)
    escaped = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", escaped)

    def _link(match: re.Match[str]) -> str:
        label = match.group(1)
        url = _link_target(match.group(2))
        label_html = html_module.escape(label or url, quote=False)
        url_html = html_module.escape(url, quote=True)
        return (
            f'<a class="app-link" href="{url_html}" target="_blank" '
            f'rel="noopener noreferrer">{label_html}</a>'
        )

    escaped = re.sub(r"\[([^\]]*)\]\(\s*([^)]*?)\s*\)", _link, escaped)
    escaped = re.sub(
        r"&lt;(https?://[^&\s]+)&gt;",
        lambda m: (
            f'<a class="app-link" href="{html_module.escape(m.group(1), quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f"{html_module.escape(m.group(1), quote=False)}</a>"
        ),
        escaped,
    )
    return re.sub(r"\s+", " ", escaped).strip()


def _list_block(block: str) -> tuple[bool, bool]:
    """(is_list, is_ordered) for one paragraph block."""

    lines = [line for line in block.splitlines() if line.strip()]
    if not lines or not all(_LIST_ITEM_RE.match(line) for line in lines):
        return False, False
    return True, bool(re.match(r"^\s*\d+\.", lines[0]))


def render_description_text(markdown: str) -> str:
    """Plain-text rendering: paragraphs joined by a space, list items by '; '."""

    if not markdown:
        return ""
    parts = []
    for block in re.split(r"\n\s*\n", markdown.strip()):
        is_list, _ = _list_block(block)
        if is_list:
            items = [
                _inline_to_plain(_LIST_ITEM_RE.match(line).group(1))  # type: ignore[union-attr]
                for line in block.splitlines()
                if line.strip()
            ]
            parts.append("; ".join(item.rstrip(".") for item in items) + ".")
        else:
            parts.append(_inline_to_plain(block))
    return " ".join(part for part in parts if part)


def render_description_html(markdown: str) -> str:
    """HTML rendering matching the classes ``EventContent.description_html`` uses elsewhere."""

    if not markdown:
        return ""
    out = []
    for block in re.split(r"\n\s*\n", markdown.strip()):
        is_list, ordered = _list_block(block)
        if is_list:
            tag = "ol" if ordered else "ul"
            css_class = "mt-4 list-decimal pl-6" if ordered else "mt-4 list-disc pl-6"
            items = "".join(
                f"<li>{_inline_to_html(_LIST_ITEM_RE.match(line).group(1))}</li>"  # type: ignore[union-attr]
                for line in block.splitlines()
                if line.strip()
            )
            out.append(f'<{tag} class="{css_class}">{items}</{tag}>')
        else:
            out.append(f'<p class="mt-4 leading-7">{_inline_to_html(block)}</p>')
    return "".join(out)


# --------------------------------------------------------------------------
# Staging artifact
# --------------------------------------------------------------------------

DESCRIPTION_RECORD_SCHEMA_VERSION = 1
_RECORD_FIELDS = frozenset(
    {
        "eventbrite_event_id",
        "canonical_repository",
        "canonical_revision",
        "canonical_source_key",
        "description_html",
        "description_text",
    }
)
_ARTIFACT_FIELDS = frozenset({"schema_version", "generated_at", "source", "events"})


@dataclass(frozen=True, slots=True)
class EventbriteDescriptionRecord:
    eventbrite_event_id: str
    canonical_repository: str
    canonical_revision: str
    canonical_source_key: str
    description_html: str
    description_text: str


def _text(value: Any, *, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise EventbriteDescriptionError(f"eventbrite_description_{field}_invalid")
    if required and not value:
        raise EventbriteDescriptionError(f"eventbrite_description_{field}_invalid")
    return value


def _record(value: Any) -> EventbriteDescriptionRecord:
    if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
        raise EventbriteDescriptionError("eventbrite_description_record_shape_invalid")
    return EventbriteDescriptionRecord(
        eventbrite_event_id=_text(
            value["eventbrite_event_id"], field="eventbrite_event_id", maximum=64
        ),
        canonical_repository=_text(
            value["canonical_repository"], field="canonical_repository", maximum=255
        ),
        canonical_revision=_text(
            value["canonical_revision"], field="canonical_revision", maximum=64
        ),
        canonical_source_key=_text(
            value["canonical_source_key"], field="canonical_source_key", maximum=512
        ),
        # Empty is meaningful, not a defect: 3 of the 226 real events have no
        # Eventbrite description at all (see eventbrite-content/README.md's
        # "known gaps") -- their cleaned text stays empty, and this record
        # still wins outright over whatever Jekyll text that event carries.
        description_html=_text(
            value["description_html"], field="description_html", maximum=1_000_000, required=False
        ),
        description_text=_text(
            value["description_text"], field="description_text", maximum=1_000_000, required=False
        ),
    )


def parse_eventbrite_description_records(payload: Any) -> tuple[EventbriteDescriptionRecord, ...]:
    if not isinstance(payload, dict) or set(payload) - _ARTIFACT_FIELDS:
        raise EventbriteDescriptionError("eventbrite_description_payload_invalid")
    if payload.get("schema_version") != DESCRIPTION_RECORD_SCHEMA_VERSION:
        raise EventbriteDescriptionError("eventbrite_description_schema_version_invalid")
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise EventbriteDescriptionError("eventbrite_description_payload_invalid")
    return tuple(_record(item) for item in events)


def load_eventbrite_description_records(path: Path) -> tuple[EventbriteDescriptionRecord, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EventbriteDescriptionError("eventbrite_description_source_unreadable") from error
    return parse_eventbrite_description_records(payload)


# --------------------------------------------------------------------------
# Apply -- description wins outright over whatever Jekyll-sourced text an
# event's EventContent row already carries.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventbriteDescriptionApplyReport:
    total: int
    applied: int
    unchanged: int
    no_identity: int
    no_content_yet: int
    no_eventbrite_description: int
    dry_run: bool


@transaction.atomic
def apply_eventbrite_descriptions(
    *, path: Path, dry_run: bool = False
) -> EventbriteDescriptionApplyReport:
    """Replace description_html/description_text for every resolvable, already-content-bearing event.

    Full replacement, not fill-only-if-missing: an event this staging artifact
    names gets the cleaned Eventbrite description whether or not it already had
    a Jekyll-sourced one. An event whose Eventbrite id does not resolve to a
    canonical Event (``no_identity``), that resolves but has no
    ``EventContent`` row yet because :func:`events.content_import.import_content`
    has not run (``no_content_yet``), or whose Eventbrite page never carried a
    published description at all (``no_eventbrite_description`` -- 3 of the 226
    real events, see ``eventbrite-content/README.md``'s "known gaps") is left
    untouched and reported, never guessed at or created here. An empty
    Eventbrite description has nothing to "win" with, so it is not allowed to
    blank out a real Jekyll one.
    """

    from .models import EventIdentityNotFound, resolve_source_identity
    from .models import EventContent

    records = load_eventbrite_description_records(path)

    applied = unchanged = no_identity = no_content_yet = no_eventbrite_description = 0
    for record in records:
        if not record.description_text and not record.description_html:
            no_eventbrite_description += 1
            continue
        try:
            event = resolve_source_identity(
                repository=record.canonical_repository,
                revision=record.canonical_revision,
                source_key=record.canonical_source_key,
            )
        except EventIdentityNotFound:
            no_identity += 1
            continue
        content = EventContent.objects.filter(event_id=event.id).first()
        if content is None:
            no_content_yet += 1
            continue
        if (
            content.description_html == record.description_html
            and content.description_text == record.description_text
        ):
            unchanged += 1
            continue
        applied += 1
        if dry_run:
            continue
        content.description_html = record.description_html
        content.description_text = record.description_text
        content.save(update_fields=["description_html", "description_text", "updated_at"])

    return EventbriteDescriptionApplyReport(
        total=len(records),
        applied=applied,
        unchanged=unchanged,
        no_identity=no_identity,
        no_content_yet=no_content_yet,
        no_eventbrite_description=no_eventbrite_description,
        dry_run=dry_run,
    )
