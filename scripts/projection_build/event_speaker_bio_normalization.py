"""Replayable cleanup for event bio boilerplate and linked person profiles.

The event-description bridge remains the source of the rendered event body.  This migration-owned
transform runs after that bridge, removes profile copy that was pasted into an event description,
and leaves the bridge provenance attached to the resulting record.  Its checked plan lives under
``_docs/migrations`` so a projection rebuild can repeat exactly the same review decisions without
reading an exporter checkout or a protected provider dump.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NORMALIZATION_PATH = (
    REPOSITORY_ROOT / "_docs" / "migrations" / "event-speaker-bio-normalization.json"
)
NORMALIZATION_PUBLIC_PATH = "_docs/migrations/event-speaker-bio-normalization.json"
NORMALIZATION_SCHEMA_VERSION = 1

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TAG = re.compile(r"<[^>]+>")
_SCREEN_READER_SUFFIX = re.compile(
    r'<span class="sr-only"> \(opens in a new tab\)</span>', re.IGNORECASE
)
_BLOCK = re.compile(
    r"<(?P<tag>p|h[1-6]|ul|ol|blockquote)\b[^>]*>.*?</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_BIO_START = re.compile(
    r"^(?:about\s+the\s+(?:speaker|speakers|guest|guests)|speaker\s+bio|bio(?:graphy)?)\b",
    re.IGNORECASE,
)
_BIO_PREFIX = re.compile(
    r"^(?:about\s+the\s+(?:speaker|speakers|guest|guests)|speaker\s+bio|bio(?:graphy)?)\s*[:.]?\s*",
    re.IGNORECASE,
)
_SPONSOR_START = re.compile(
    r"^(?:this\s+(?:event|post|workshop|podcast)\s+is\s+sponsored"
    r"|this\s+workshop\s+is\s+hosted\s+by)\b",
    re.IGNORECASE,
)
_BIO_SECTION_END = re.compile(
    r"^(?:content\s+warning|links?|resources?|more\s+details|"
    r"all\s+events\s+in\s+this\s+series|thinking\s+about\s+joining|"
    r"register(?:ation)?|recording|follow|learn\s+more|for\s+more\s+information|"
    r"this\s+is\s+going\s+to\s+be\s+the\s+first\s+iteration|be\s+among\s+the\s+first)\b",
    re.IGNORECASE,
)
_HREF = re.compile(
    r"(?P<prefix>\bhref\s*=\s*)(?P<quote>[\"'])(?P<url>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)

_PLATFORM_BOILERPLATE = frozenset(
    {
        "datatalks club is the place to talk about data",
        "datatalks club is a place to talk about data",
    }
)


class EventSpeakerBioNormalizationError(RuntimeError):
    """A migration plan or replayed projection failed closed."""


@dataclass(frozen=True, slots=True)
class DescriptionNormalization:
    html: str
    text: str
    removed_speaker_bio: bool
    removed_platform_boilerplate: int
    normalized_internal_links: int


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _pretty_digest(value: Any) -> str:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    return hashlib.sha256(payload).hexdigest()


def _plain_text(fragment: str) -> str:
    without_accessibility_suffix = _SCREEN_READER_SUFFIX.sub("", fragment)
    return " ".join(html.unescape(_TAG.sub(" ", without_accessibility_suffix)).split())


def _comparison_text(value: str) -> str:
    """Compare a block after tolerating source punctuation, case, and inline markup."""

    return " ".join(re.sub(r"[^a-z0-9]+", " ", _plain_text(value).casefold()).split())


def _is_platform_boilerplate(value: str) -> bool:
    return _comparison_text(value) in _PLATFORM_BOILERPLATE


def _normalize_internal_links(fragment: str) -> tuple[str, int]:
    """Make approved absolute self-links root-relative while retaining query and fragment."""

    normalized_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal normalized_count
        url = match.group("url")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return match.group(0)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or parsed.hostname is None
            or parsed.hostname.casefold() != "datatalks.club"
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 80, 443}
        ):
            return match.group(0)
        path = parsed.path or "/"
        if not path.startswith("/"):
            path = "/" + path
        normalized = urlunsplit(("", "", path, parsed.query, parsed.fragment))
        if normalized == url:
            return match.group(0)
        normalized_count += 1
        return f"{match.group('prefix')}{match.group('quote')}{normalized}{match.group('quote')}"

    return _HREF.sub(replace, fragment), normalized_count


def _block_parts(fragment: str) -> list[re.Match[str]]:
    return list(_BLOCK.finditer(fragment))


def _replace_blocks(fragment: str, matches: list[re.Match[str]], removed: set[int]) -> str:
    pieces: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        pieces.append(fragment[cursor : match.start()])
        if index not in removed:
            pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(fragment[cursor:])
    return "".join(pieces).strip()


def normalize_description_html(fragment: str) -> DescriptionNormalization:
    """Remove speaker bio sections and exact DataTalks.Club footer blocks.

    A bio section starts only at a standalone block or at the beginning of a block whose label is
    ``About the Speaker``, ``About the Guest``, ``Speaker Bio``, ``Bio``, or ``Biography``.  The
    first reviewed platform footer or sponsor block ends the section; this preserves sponsor copy
    and genuinely event-specific content.  A description without either marker is unchanged.
    """

    if not isinstance(fragment, str):
        raise EventSpeakerBioNormalizationError("event description is not text")
    fragment, normalized_internal_links = _normalize_internal_links(fragment)
    matches = _block_parts(fragment)
    block_text = [_plain_text(match.group(0)) for match in matches]
    bio_start: int | None = None
    for index, text in enumerate(block_text):
        if _BIO_START.match(text):
            bio_start = index
            break

    remove: set[int] = set()
    removed_bio = False
    if bio_start is not None:
        removed_bio = True
        bio_end = len(matches)
        for index in range(bio_start + 1, len(matches)):
            if (
                _is_platform_boilerplate(matches[index].group(0))
                or _SPONSOR_START.match(block_text[index])
                or _BIO_SECTION_END.match(block_text[index])
            ):
                bio_end = index
                break
        remove.update(range(bio_start, bio_end))

    footer_count = 0
    for index, match in enumerate(matches):
        if _is_platform_boilerplate(match.group(0)):
            remove.add(index)
            footer_count += 1

    normalized_html = _replace_blocks(fragment, matches, remove)
    normalized_text = _plain_text(normalized_html)
    return DescriptionNormalization(
        html=normalized_html,
        text=normalized_text,
        removed_speaker_bio=removed_bio,
        removed_platform_boilerplate=footer_count,
        normalized_internal_links=normalized_internal_links,
    )


def _validate_plan_shape(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict) or set(plan) != {
        "schema_version",
        "schema",
        "source",
        "rules",
        "counts",
        "events",
        "people",
        "conflicts",
        "content_sha256",
    }:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan shape mismatch")
    if plan["schema_version"] != NORMALIZATION_SCHEMA_VERSION:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan version mismatch")
    schema = plan["schema"]
    if not isinstance(schema, dict) or schema.get("path") != NORMALIZATION_PUBLIC_PATH:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan schema binding mismatch")
    source = plan["source"]
    if not isinstance(source, dict) or set(source) != {
        "bridge_content_sha256",
        "bridge_schema_version",
        "event_count",
        "people_repository",
        "people_revision",
    }:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan source shape mismatch")
    if (
        not _SHA256.fullmatch(source["bridge_content_sha256"])
        or source["bridge_schema_version"] != 1
        or source["event_count"] != 421
        or source["people_repository"] != "DataTalksClub/datatalksclub.github.io"
        or source["people_revision"] != "ee43d3fa0929faf691178d79f19528e6f15a83e5"
    ):
        raise EventSpeakerBioNormalizationError("event speaker-bio plan source binding mismatch")
    rules = plan["rules"]
    if not isinstance(rules, dict) or set(rules) != {
        "platform_boilerplate",
        "platform_boilerplate_variants",
        "bio_section_markers",
        "canonical_person_policy",
        "internal_link_policy",
    }:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan rules shape mismatch")
    if (
        rules["platform_boilerplate"] != "datatalks.club is the place to talk about data"
        or rules["platform_boilerplate_variants"]
        != [
            "datatalks.club is the place to talk about data",
            "datatalks.club is a place to talk about data",
        ]
        or rules["bio_section_markers"]
        != [
            "about the speaker",
            "about the speakers",
            "about the guest",
            "about the guests",
            "speaker bio",
            "bio",
            "biography",
        ]
        or rules["canonical_person_policy"]
        != "retain the checked person profile; merge only reviewed non-duplicate facts"
        or rules["internal_link_policy"]
        != (
            "rewrite absolute https://datatalks.club self-links to root-relative paths "
            "while preserving query strings and fragments"
        )
    ):
        raise EventSpeakerBioNormalizationError("event speaker-bio plan rules mismatch")
    counts = plan["counts"]
    if not isinstance(counts, dict) or set(counts) != {
        "events",
        "described_events",
        "undescribed_events",
        "speaker_bio_events",
        "platform_boilerplate_events",
        "platform_boilerplate_blocks",
        "people_with_bio_candidates",
        "people_changed",
        "conflicts",
        "internal_links_normalized",
    }:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan count shape mismatch")
    events = plan["events"]
    if not isinstance(events, list) or len(events) != source["event_count"]:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan event count mismatch")
    seen_ids: set[str] = set()
    for item in events:
        if not isinstance(item, dict) or set(item) != {
            "identity_id",
            "slug",
            "description_present",
            "remove_speaker_bio",
            "platform_boilerplate_blocks",
            "internal_links_normalized",
            "before_description_sha256",
            "outcome",
        }:
            raise EventSpeakerBioNormalizationError("event speaker-bio plan event shape mismatch")
        identity_id = item["identity_id"]
        if (
            not isinstance(identity_id, str)
            or not identity_id
            or identity_id in seen_ids
            or not isinstance(item["slug"], str)
            or not item["slug"]
            or not isinstance(item["description_present"], bool)
            or not isinstance(item["remove_speaker_bio"], bool)
            or isinstance(item["platform_boilerplate_blocks"], bool)
            or not isinstance(item["platform_boilerplate_blocks"], int)
            or item["platform_boilerplate_blocks"] < 0
            or isinstance(item["internal_links_normalized"], bool)
            or not isinstance(item["internal_links_normalized"], int)
            or item["internal_links_normalized"] < 0
            or not _SHA256.fullmatch(item["before_description_sha256"])
            or item["outcome"]
            not in {
                "processed_no_description",
                "processed_no_duplicate",
                "normalized_speaker_bio",
                "normalized_speaker_bio_and_platform_boilerplate",
                "normalized_platform_boilerplate",
            }
        ):
            raise EventSpeakerBioNormalizationError("event speaker-bio plan event value mismatch")
        seen_ids.add(identity_id)
    if events != sorted(events, key=lambda item: item["identity_id"]):
        raise EventSpeakerBioNormalizationError("event speaker-bio plan events are not sorted")
    people = plan["people"]
    if not isinstance(people, list):
        raise EventSpeakerBioNormalizationError("event speaker-bio plan people shape mismatch")
    seen_people: set[str] = set()
    for item in people:
        if not isinstance(item, dict) or set(item) != {
            "slug",
            "action",
            "source_blocks_sha256",
            "blocks",
            "reason",
        }:
            raise EventSpeakerBioNormalizationError("event speaker-bio plan person shape mismatch")
        if (
            not isinstance(item["slug"], str)
            or not item["slug"]
            or item["slug"] in seen_people
            or item["action"] not in {"append", "replace"}
            or not _SHA256.fullmatch(item["source_blocks_sha256"])
            or not isinstance(item["blocks"], list)
            or not item["blocks"]
            or any(not isinstance(block, str) or not block.strip() for block in item["blocks"])
            or not isinstance(item["reason"], str)
            or not item["reason"]
        ):
            raise EventSpeakerBioNormalizationError("event speaker-bio plan person value mismatch")
        seen_people.add(item["slug"])
    if people != sorted(people, key=lambda item: item["slug"]):
        raise EventSpeakerBioNormalizationError("event speaker-bio plan people are not sorted")
    conflicts = plan["conflicts"]
    if not isinstance(conflicts, list) or any(
        not isinstance(item, dict)
        or set(item) != {"event_slug", "speaker_keys", "decision", "reason"}
        or not isinstance(item["event_slug"], str)
        or not isinstance(item["speaker_keys"], list)
        or any(not isinstance(key, str) for key in item["speaker_keys"])
        or not isinstance(item["decision"], str)
        or not isinstance(item["reason"], str)
        for item in conflicts
    ):
        raise EventSpeakerBioNormalizationError("event speaker-bio plan conflict shape mismatch")
    expected_digest = _canonical_digest(
        {key: value for key, value in plan.items() if key != "content_sha256"}
    )
    if plan["content_sha256"] != expected_digest:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan content digest mismatch")
    if counts["events"] != len(events) or counts["people_changed"] != len(people):
        raise EventSpeakerBioNormalizationError("event speaker-bio plan aggregate count mismatch")
    if counts["conflicts"] != len(conflicts):
        raise EventSpeakerBioNormalizationError("event speaker-bio plan conflict count mismatch")
    if counts["internal_links_normalized"] != sum(
        item["internal_links_normalized"] for item in events
    ):
        raise EventSpeakerBioNormalizationError("event speaker-bio link count mismatch")
    return plan


@lru_cache(maxsize=1)
def load_normalization_plan() -> dict[str, Any]:
    try:
        plan = json.loads(NORMALIZATION_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan is unavailable") from exc
    return _validate_plan_shape(plan)


def normalization_manifest_binding(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = plan or load_normalization_plan()
    return {
        "path": NORMALIZATION_PUBLIC_PATH,
        "schema_version": NORMALIZATION_SCHEMA_VERSION,
        "schema": dict(selected["schema"]),
        "content_sha256": selected["content_sha256"],
        "source": dict(selected["source"]),
        "counts": dict(selected["counts"]),
    }


def _plan_event(plan: dict[str, Any], identity_id: str) -> dict[str, Any]:
    for item in plan["events"]:
        if item["identity_id"] == identity_id:
            return item
    raise EventSpeakerBioNormalizationError("event speaker-bio plan identity is missing")


def normalize_event_description(event: dict[str, Any]) -> DescriptionNormalization:
    """Normalize one projected event and verify its reviewed before-state."""

    if not isinstance(event, dict) or not isinstance(event.get("identity_id"), str):
        raise EventSpeakerBioNormalizationError("event speaker-bio identity is missing")
    plan = load_normalization_plan()
    item = _plan_event(plan, event["identity_id"])
    if event.get("slug") != item["slug"]:
        raise EventSpeakerBioNormalizationError("event speaker-bio plan slug mismatch")
    fragment = event.get("description_html", "")
    if not isinstance(fragment, str):
        raise EventSpeakerBioNormalizationError("event description is not text")
    before_digest = hashlib.sha256(fragment.encode()).hexdigest()
    if before_digest != item["before_description_sha256"]:
        provenance = event.get("description_provenance")
        if (
            isinstance(provenance, dict)
            and provenance.get("normalization_schema_version") == NORMALIZATION_SCHEMA_VERSION
            and provenance.get("normalization_content_sha256") == plan["content_sha256"]
            and provenance.get("normalization_original_description_sha256")
            == item["before_description_sha256"]
            and provenance.get("removed_speaker_bio") == item["remove_speaker_bio"]
            and provenance.get("removed_platform_boilerplate")
            == item["platform_boilerplate_blocks"]
            and provenance.get("normalized_internal_links") == item["internal_links_normalized"]
        ):
            replayed = normalize_description_html(fragment)
            if (
                replayed.html == fragment
                and replayed.text == event.get("description_text")
                and not replayed.removed_speaker_bio
                and replayed.removed_platform_boilerplate == 0
                and replayed.normalized_internal_links == 0
            ):
                return DescriptionNormalization(
                    html=fragment,
                    text=replayed.text,
                    removed_speaker_bio=item["remove_speaker_bio"],
                    removed_platform_boilerplate=item["platform_boilerplate_blocks"],
                    normalized_internal_links=item["internal_links_normalized"],
                )
        raise EventSpeakerBioNormalizationError("event speaker-bio source description drift")
    result = normalize_description_html(fragment)
    if (
        bool(fragment) != item["description_present"]
        or result.removed_speaker_bio != item["remove_speaker_bio"]
        or result.removed_platform_boilerplate != item["platform_boilerplate_blocks"]
        or result.normalized_internal_links != item["internal_links_normalized"]
    ):
        raise EventSpeakerBioNormalizationError("event speaker-bio review decision drift")
    return result


def _blocks_digest(blocks: Any) -> str:
    return _canonical_digest(blocks)


def apply_event_speaker_bio_normalization(
    events: list[dict[str, Any]], people: list[dict[str, Any]]
) -> dict[str, int]:
    """Replay the checked event and person transforms in-place.

    The returned counters are intentionally small and safe to log in an engineer handoff.  Source
    description text is never logged; the original bridge digest remains in each event's
    ``description_provenance`` alongside the normalization digest and removal counters.
    """

    plan = load_normalization_plan()
    if len(events) != plan["source"]["event_count"]:
        raise EventSpeakerBioNormalizationError("event speaker-bio projection count mismatch")
    plan_by_id = {item["identity_id"]: item for item in plan["events"]}
    seen_ids: set[str] = set()
    description_count = 0
    bio_count = 0
    footer_count = 0
    internal_link_count = 0
    for event in events:
        identity_id = event.get("identity_id")
        if (
            not isinstance(identity_id, str)
            or identity_id not in plan_by_id
            or identity_id in seen_ids
        ):
            raise EventSpeakerBioNormalizationError("event speaker-bio identity coverage mismatch")
        seen_ids.add(identity_id)
        item = plan_by_id[identity_id]
        if event.get("slug") != item["slug"]:
            raise EventSpeakerBioNormalizationError("event speaker-bio slug coverage mismatch")
        before = event.get("description_html", "")
        result = normalize_event_description(event)
        if not before:
            continue
        description_count += 1
        bio_count += result.removed_speaker_bio
        footer_count += result.removed_platform_boilerplate
        internal_link_count += result.normalized_internal_links
        event["description_html"] = result.html
        event["description_text"] = result.text
        provenance = event.get("description_provenance")
        if not isinstance(provenance, dict):
            raise EventSpeakerBioNormalizationError("event speaker-bio bridge provenance missing")
        provenance = dict(provenance)
        provenance.update(
            {
                "normalization_schema_version": NORMALIZATION_SCHEMA_VERSION,
                "normalization_content_sha256": plan["content_sha256"],
                "normalization_original_description_sha256": item["before_description_sha256"],
                "removed_speaker_bio": result.removed_speaker_bio,
                "removed_platform_boilerplate": result.removed_platform_boilerplate,
                "normalized_internal_links": result.normalized_internal_links,
            }
        )
        event["description_provenance"] = provenance
    if seen_ids != set(plan_by_id):
        raise EventSpeakerBioNormalizationError("event speaker-bio event rows are incomplete")

    plan_people = {item["slug"]: item for item in plan["people"]}
    seen_people: set[str] = set()
    for person in people:
        slug = person.get("slug")
        if not isinstance(slug, str):
            continue
        item = plan_people.get(slug)
        if item is None:
            continue
        seen_people.add(slug)
        blocks = person.get("blocks")
        if not isinstance(blocks, list):
            raise EventSpeakerBioNormalizationError("person bio source blocks drift")
        additions = [{"kind": "paragraph", "text": text.strip()} for text in item["blocks"]]
        if _blocks_digest(blocks) != item["source_blocks_sha256"]:
            expected_blocks = additions
            if item["action"] == "append":
                expected_blocks = list(blocks)
                existing_text = {
                    _comparison_text(block.get("text", "")) for block in expected_blocks
                }
                for addition in additions:
                    if _comparison_text(addition["text"]) not in existing_text:
                        expected_blocks.append(addition)
                        existing_text.add(_comparison_text(addition["text"]))
            if blocks != expected_blocks:
                raise EventSpeakerBioNormalizationError("person bio source blocks drift")
            continue
        if item["action"] == "replace":
            person["blocks"] = additions
        else:
            existing = list(blocks)
            existing_text = {_comparison_text(block.get("text", "")) for block in existing}
            for addition in additions:
                if _comparison_text(addition["text"]) not in existing_text:
                    existing.append(addition)
                    existing_text.add(_comparison_text(addition["text"]))
            person["blocks"] = existing
    if seen_people != set(plan_people):
        raise EventSpeakerBioNormalizationError("person bio plan target is missing")
    return {
        "events": len(events),
        "described_events": description_count,
        "speaker_bio_events": bio_count,
        "platform_boilerplate_blocks": footer_count,
        "people_changed": len(plan_people),
        "conflicts": len(plan["conflicts"]),
        "internal_links_normalized": internal_link_count,
    }
