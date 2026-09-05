"""Fail-closed loading for the reviewed, public-safe event-description bridge."""

from __future__ import annotations

import hashlib
import html
import json
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bleach import Cleaner  # type: ignore[import-untyped]

from .event_description_link_policy import (
    EXPECTED_LINK_DECISION_COUNTS,
    EventDescriptionLinkPolicyError,
    classify_rendered_url,
    classify_source_url,
    projection_routes_and_fragments,
)
from .event_speaker_bio_normalization import (
    NORMALIZATION_SCHEMA_VERSION,
    load_normalization_plan,
    normalize_description_html,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPOSITORY_ROOT / "content" / "event_description_bridge.json"
BRIDGE_SCHEMA_PATH = (
    REPOSITORY_ROOT / "_docs" / "compatibility" / "event-description-bridge.schema.json"
)
BRIDGE_SCHEMA_PUBLIC_PATH = "_docs/compatibility/event-description-bridge.schema.json"
BRIDGE_PUBLIC_PATH = "content/event_description_bridge.json"

BRIDGE_SCHEMA_VERSION = 1
EVENT_RECORD_SCHEMA_VERSION = 2
EXPORTER_REVISION = "a0ede3c1451d34c5bc99acf58459ef1c65ae51df"
PUBLIC_EVENT_ALLOWLIST_SHA256 = "b2859a7be33fb29821d53e25265ce91c37f376f1de7467780a351d707294fbc5"
DESCRIPTION_MANIFEST_SHA256 = "6ffd000db03b71c7308d83328a5d744ccec298b1cca458aa04831689431b030a"
SAFE_SOURCE_SHA256 = "ddcd0eae7b60e886a1b30f644bd09f5d6f0433f0f27d95fa40d6171570dceaf1"
LINK_REVIEW_INVENTORY_SHA256 = "7e03446eeb7b119af17ee4996add65cfbad4917aff81b8e7e1a0dcb0885b62ce"
BASELINE_EVENTS_SHA256 = "64cdab5893afb3558fdf5a3d81fe23c9ed9481016897d823f41dcdbe5bebf580"
LEGACY_REPOSITORY = "DataTalksClub/datatalksclub.github.io"
LEGACY_REVISION = "ee43d3fa0929faf691178d79f19528e6f15a83e5"
LEGACY_SOURCE_PATH = "_data/events.yaml"
LEGACY_SOURCE_CHECKSUM = "7eac8bcc9bfb3ec5f0b35434343a58eb766f8cc8451dca8a4a82ac4674aa213d"

MATCHING_POLICY_VERSION = "exact-provider-path-v1"
MARKDOWN_POLICY_VERSION = "event-description-markdown-v2"
LINK_POLICY_VERSION = "event-description-links-v1"

EXPECTED_PAIR_COUNT = 168
EXPECTED_MATCH_COUNT = 159
EXPECTED_GAP_COUNT = 9
EXPECTED_EVENT_COUNT = 421
EXPECTED_UNDESCRIBED_COUNT = 262
EXPECTED_LUMA_LINK_REMOVALS = 168
EXPECTED_NON_LUMA_LINKS = 682
EXPECTED_EVENTS_WITH_LINKS = 403
EXPECTED_EVENTS_WITHOUT_LINKS = 18
EXPECTED_URL_OCCURRENCES = 540
EXPECTED_DISTINCT_URLS = 118
EXPECTED_REMOTE_IMAGES_OMITTED = 1

SHA256 = re.compile(r"^[0-9a-f]{64}$")
HTML_TAG = re.compile(r"<[^>]+>")
SCREEN_READER_SUFFIX = re.compile(r'<span class="sr-only"> \(opens in a new tab\)</span>')
DANGLING_ACTION_COPY = re.compile(
    r"\buse\s+(?:this|the)\s+link\s+to\s+submit\b"
    r"|\bcourse\s+contents\s*!\s*(?:form\b|\.)",
    re.IGNORECASE,
)
SAFE_HTML_TAGS = frozenset(
    {
        "a",
        "blockquote",
        "code",
        "em",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ol",
        "p",
        "span",
        "strong",
        "ul",
    }
)
SAFE_CLASS_VALUES = frozenset(
    {
        "app-link",
        "border-l-4",
        "font-semibold",
        "leading-7",
        "list-decimal",
        "list-disc",
        "mt-4",
        "mt-6",
        "mt-8",
        "pl-4",
        "pl-6",
        "sr-only",
        "text-lg",
        "text-xl",
    }
)


class EventDescriptionBridgeError(RuntimeError):
    """A bounded, content-free bridge failure."""


def canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def pretty_json_sha256(payload: Any) -> str:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    return hashlib.sha256(encoded).hexdigest()


def _schema_sha256() -> str:
    try:
        return hashlib.sha256(BRIDGE_SCHEMA_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        raise EventDescriptionBridgeError("event description bridge schema is unavailable") from exc


def _safe_html_attributes(tag: str, name: str, value: str) -> bool:
    if name == "class":
        return bool(value) and set(value.split()).issubset(SAFE_CLASS_VALUES)
    if tag == "a" and name == "href":
        return True
    if tag == "a" and name == "target":
        return value == "_blank"
    if tag == "a" and name == "rel":
        return value == "noopener noreferrer"
    return False


SAFE_HTML_CLEANER = Cleaner(
    tags=SAFE_HTML_TAGS,
    attributes=_safe_html_attributes,
    protocols=frozenset({"http", "https"}),
    strip=True,
    strip_comments=True,
)


class _SafeFragmentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.external_link_depths: list[int] = []
        self.external_labels: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in SAFE_HTML_TAGS:
            raise EventDescriptionBridgeError("event description HTML tag is not allowed")
        if any(value is None for _, value in attrs):
            raise EventDescriptionBridgeError("event description HTML attribute is invalid")
        values: dict[str, str] = {name: value for name, value in attrs if value is not None}
        if tag == "a":
            href = values.get("href", "")
            _validate_rendered_link(href, values)
            if values.get("target") == "_blank":
                self.external_link_depths.append(len(self.stack))
                self.external_labels.append(False)
        elif any(name not in {"class"} for name in values):
            raise EventDescriptionBridgeError("event description HTML attribute is not allowed")
        classes = values.get("class", "").split()
        if not set(classes).issubset(SAFE_CLASS_VALUES):
            raise EventDescriptionBridgeError("event description HTML class is not allowed")
        if tag == "span" and classes == ["sr-only"] and self.external_labels:
            self.external_labels[-1] = True
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            raise EventDescriptionBridgeError("event description HTML nesting is invalid")
        if tag == "a" and self.external_link_depths:
            depth = self.external_link_depths.pop()
            labelled = self.external_labels.pop()
            if depth != len(self.stack) - 1 or not labelled:
                raise EventDescriptionBridgeError(
                    "event description external link accessibility label is missing"
                )
        self.stack.pop()

    def close(self) -> None:
        super().close()
        if self.stack or self.external_link_depths:
            raise EventDescriptionBridgeError("event description HTML is incomplete")


@lru_cache(maxsize=1)
def _reviewed_route_registry() -> tuple[set[str], dict[str, set[str]]]:
    try:
        return projection_routes_and_fragments()
    except EventDescriptionLinkPolicyError as exc:
        raise EventDescriptionBridgeError(
            "event description route registry is unavailable"
        ) from exc


def _validate_rendered_link(href: str, attributes: dict[str, str]) -> None:
    public_paths, fragments = _reviewed_route_registry()
    if href.startswith("/") and not href.startswith("//"):
        try:
            decision, canonical = classify_source_url(
                f"https://datatalks.club{href}",
                public_paths=public_paths,
                fragments=fragments,
            )
            parsed = urlsplit(canonical)
            canonical_root = urlunsplit(("", "", parsed.path or "/", parsed.query, parsed.fragment))
        except EventDescriptionLinkPolicyError as exc:
            raise EventDescriptionBridgeError(
                "event description internal link violates reviewed policy"
            ) from exc
        if decision != "internal_rewritten" or canonical_root != href:
            raise EventDescriptionBridgeError("event description internal link is not canonical")
        if set(attributes) != {"class", "href"}:
            raise EventDescriptionBridgeError("event description internal link is not canonical")
        return
    try:
        decision = classify_rendered_url(
            href,
            public_paths=public_paths,
            fragments=fragments,
        )
    except EventDescriptionLinkPolicyError as exc:
        raise EventDescriptionBridgeError(
            "event description link violates reviewed policy"
        ) from exc
    if decision == "internal_rewritten":
        if set(attributes) != {"class", "href"}:
            raise EventDescriptionBridgeError("event description internal link is not canonical")
    elif decision == "external_resource_kept" and attributes != {
        "class": "app-link",
        "href": href,
        "rel": "noopener noreferrer",
        "target": "_blank",
    }:
        raise EventDescriptionBridgeError("event description external link attributes are invalid")
    elif decision not in {"internal_rewritten", "external_resource_kept"}:
        raise EventDescriptionBridgeError("event description link decision is invalid")


def validate_description_html(fragment: str) -> None:
    if not fragment or len(fragment.encode()) > 96_000:
        raise EventDescriptionBridgeError("event description HTML size is invalid")
    if SAFE_HTML_CLEANER.clean(fragment) != fragment:
        raise EventDescriptionBridgeError("event description HTML is not pre-sanitized")
    lowered = fragment.casefold()
    if any(value in lowered for value in ("luma.com", "lu.ma", "images.lumacdn.com")):
        raise EventDescriptionBridgeError("event description provider value is forbidden")
    plain_text = " ".join(html.unescape(HTML_TAG.sub(" ", fragment)).split())
    if DANGLING_ACTION_COPY.search(plain_text):
        raise EventDescriptionBridgeError("event description contains dangling action copy")
    parser = _SafeFragmentParser()
    try:
        parser.feed(fragment)
        parser.close()
    except EventDescriptionBridgeError:
        raise
    except Exception as exc:
        raise EventDescriptionBridgeError("event description HTML is invalid") from exc


def description_plain_text(fragment: str) -> str:
    without_accessibility_suffix = SCREEN_READER_SUFFIX.sub("", fragment)
    return " ".join(html.unescape(HTML_TAG.sub(" ", without_accessibility_suffix)).split())


def _target_tuple(provenance: dict[str, Any]) -> dict[str, str]:
    return {
        "repository": provenance.get("repository", ""),
        "revision": provenance.get("revision", ""),
        "source_path": provenance.get("source_path", ""),
        "source_key": provenance.get("source_key", ""),
        "checksum": provenance.get("checksum", ""),
    }


def _validate_bridge(bridge: Any) -> dict[str, Any]:
    if not isinstance(bridge, dict) or set(bridge) != {
        "schema_version",
        "schema",
        "source",
        "projection",
        "policies",
        "counts",
        "matches",
        "gaps",
        "link_review",
        "content_sha256",
    }:
        raise EventDescriptionBridgeError("event description bridge shape mismatch")
    if bridge["schema_version"] != BRIDGE_SCHEMA_VERSION:
        raise EventDescriptionBridgeError("event description bridge version mismatch")
    if bridge["schema"] != {
        "path": BRIDGE_SCHEMA_PUBLIC_PATH,
        "sha256": _schema_sha256(),
    }:
        raise EventDescriptionBridgeError("event description bridge schema mismatch")
    source = bridge["source"]
    if not isinstance(source, dict) or set(source) != {
        "exporter_revision",
        "public_event_allowlist_sha256",
        "description_manifest_sha256",
        "safe_source_sha256",
    }:
        raise EventDescriptionBridgeError("event description bridge source shape mismatch")
    if source["exporter_revision"] != EXPORTER_REVISION:
        raise EventDescriptionBridgeError("event description exporter revision mismatch")
    if source["public_event_allowlist_sha256"] != PUBLIC_EVENT_ALLOWLIST_SHA256:
        raise EventDescriptionBridgeError("event description public audit anchor mismatch")
    if source["description_manifest_sha256"] != DESCRIPTION_MANIFEST_SHA256:
        raise EventDescriptionBridgeError("event description Markdown audit anchor mismatch")
    if source["safe_source_sha256"] != SAFE_SOURCE_SHA256:
        raise EventDescriptionBridgeError("event description safe-source digest mismatch")
    if bridge["projection"] != {
        "events_sha256": BASELINE_EVENTS_SHA256,
        "event_count": EXPECTED_EVENT_COUNT,
        "repository": LEGACY_REPOSITORY,
        "revision": LEGACY_REVISION,
        "source_path": LEGACY_SOURCE_PATH,
        "source_checksum": LEGACY_SOURCE_CHECKSUM,
    }:
        raise EventDescriptionBridgeError("event description projection binding mismatch")
    if bridge["policies"] != {
        "matching": MATCHING_POLICY_VERSION,
        "markdown": MARKDOWN_POLICY_VERSION,
        "links": LINK_POLICY_VERSION,
    }:
        raise EventDescriptionBridgeError("event description policy mismatch")
    if bridge["counts"] != {
        "source_pairs": EXPECTED_PAIR_COUNT,
        "matches": EXPECTED_MATCH_COUNT,
        "gaps": EXPECTED_GAP_COUNT,
        "described_events": EXPECTED_MATCH_COUNT,
        "undescribed_events": EXPECTED_UNDESCRIBED_COUNT,
        "title_and_instant_equal": 36,
        "title_equal_instant_different": 113,
        "title_different_instant_equal": 2,
        "title_and_instant_different": 8,
    }:
        raise EventDescriptionBridgeError("event description reconciliation count mismatch")
    matches = bridge["matches"]
    gaps = bridge["gaps"]
    if not isinstance(matches, list) or len(matches) != EXPECTED_MATCH_COUNT:
        raise EventDescriptionBridgeError("event description match count mismatch")
    if not isinstance(gaps, list) or len(gaps) != EXPECTED_GAP_COUNT:
        raise EventDescriptionBridgeError("event description gap count mismatch")
    seen_targets: set[str] = set()
    seen_source_identities: set[str] = set()
    for entry in matches:
        if not isinstance(entry, dict) or set(entry) != {
            "target",
            "source_identity_sha256",
            "source_description_sha256",
            "match_basis",
            "description_html",
            "description_text",
            "entry_sha256",
        }:
            raise EventDescriptionBridgeError("event description match shape mismatch")
        target = entry["target"]
        if not isinstance(target, dict) or set(target) != {
            "repository",
            "revision",
            "source_path",
            "source_key",
            "checksum",
        }:
            raise EventDescriptionBridgeError("event description target shape mismatch")
        if (
            target["repository"] != LEGACY_REPOSITORY
            or target["revision"] != LEGACY_REVISION
            or target["source_path"] != LEGACY_SOURCE_PATH
            or target["checksum"] != LEGACY_SOURCE_CHECKSUM
            or not target["source_key"]
        ):
            raise EventDescriptionBridgeError("event description target provenance mismatch")
        target_digest = canonical_json_sha256(target)
        if target_digest in seen_targets:
            raise EventDescriptionBridgeError("event description target collision")
        seen_targets.add(target_digest)
        source_identity = entry["source_identity_sha256"]
        if not isinstance(source_identity, str) or not SHA256.fullmatch(source_identity):
            raise EventDescriptionBridgeError("event description source identity is invalid")
        if source_identity in seen_source_identities:
            raise EventDescriptionBridgeError("event description source identity collision")
        seen_source_identities.add(source_identity)
        if not isinstance(entry["source_description_sha256"], str) or not SHA256.fullmatch(
            entry["source_description_sha256"]
        ):
            raise EventDescriptionBridgeError("event description source digest is invalid")
        if entry["match_basis"] != "exact_normalized_provider_path":
            raise EventDescriptionBridgeError("event description match basis mismatch")
        if not isinstance(entry["description_html"], str) or not isinstance(
            entry["description_text"], str
        ):
            raise EventDescriptionBridgeError("event description body shape mismatch")
        validate_description_html(entry["description_html"])
        if (
            entry["description_text"] != description_plain_text(entry["description_html"])
            or not entry["description_text"].strip()
            or len(entry["description_text"]) > 24_000
        ):
            raise EventDescriptionBridgeError("event description text is invalid")
        expected_entry_digest = canonical_json_sha256(
            {key: value for key, value in entry.items() if key != "entry_sha256"}
        )
        if entry["entry_sha256"] != expected_entry_digest:
            raise EventDescriptionBridgeError("event description entry digest mismatch")
    if matches != sorted(matches, key=lambda item: item["target"]["source_key"]):
        raise EventDescriptionBridgeError("event description matches are not sorted")
    for gap in gaps:
        if not isinstance(gap, dict) or set(gap) != {
            "source_identity_sha256",
            "reason",
        }:
            raise EventDescriptionBridgeError("event description gap shape mismatch")
        if (
            not isinstance(gap["source_identity_sha256"], str)
            or not SHA256.fullmatch(gap["source_identity_sha256"])
            or gap["source_identity_sha256"] in seen_source_identities
        ):
            raise EventDescriptionBridgeError("event description gap identity mismatch")
        seen_source_identities.add(gap["source_identity_sha256"])
        if gap["reason"] != "no_exact_projection_luma_path":
            raise EventDescriptionBridgeError("event description gap reason mismatch")
    if gaps != sorted(gaps, key=lambda item: item["source_identity_sha256"]):
        raise EventDescriptionBridgeError("event description gaps are not sorted")
    link_review = bridge["link_review"]
    if not isinstance(link_review, dict) or set(link_review) != {
        "url_occurrences",
        "distinct_url_literals",
        "decision_counts",
        "decision_inventory_sha256",
        "remote_images_omitted",
    }:
        raise EventDescriptionBridgeError("event description link review shape mismatch")
    if (
        link_review["url_occurrences"] != EXPECTED_URL_OCCURRENCES
        or link_review["distinct_url_literals"] != EXPECTED_DISTINCT_URLS
        or link_review["remote_images_omitted"] != EXPECTED_REMOTE_IMAGES_OMITTED
        or link_review["decision_inventory_sha256"] != LINK_REVIEW_INVENTORY_SHA256
        or link_review["decision_counts"] != dict(EXPECTED_LINK_DECISION_COUNTS)
    ):
        raise EventDescriptionBridgeError("event description link review mismatch")
    expected_content_digest = canonical_json_sha256(
        {key: value for key, value in bridge.items() if key != "content_sha256"}
    )
    if bridge["content_sha256"] != expected_content_digest:
        raise EventDescriptionBridgeError("event description bridge content digest mismatch")
    return bridge


@lru_cache(maxsize=2)
def load_event_description_bridge(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the reviewed bridge, defaulting to the runtime capture path.

    Builders pass their explicit helper path; every other caller uses the
    default, so runtime behavior is unchanged.
    """

    try:
        bridge = json.loads((path or BRIDGE_PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventDescriptionBridgeError("event description bridge is unavailable") from exc
    return _validate_bridge(bridge)


def bridge_manifest_binding(bridge: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = bridge or load_event_description_bridge()
    return {
        "path": BRIDGE_PUBLIC_PATH,
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "schema": dict(selected["schema"]),
        "content_sha256": selected["content_sha256"],
        "safe_source_sha256": selected["source"]["safe_source_sha256"],
        "exporter_revision": EXPORTER_REVISION,
        "matching_policy_version": MATCHING_POLICY_VERSION,
        "markdown_policy_version": MARKDOWN_POLICY_VERSION,
        "link_policy_version": LINK_POLICY_VERSION,
        "event_record_schema_version": EVENT_RECORD_SCHEMA_VERSION,
        "counts": dict(selected["counts"]),
        "link_review": dict(selected["link_review"]),
    }


def _is_luma_link(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        hostname = (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return False
    return hostname in {"luma.com", "lu.ma"}


def _remove_luma_links_and_validate(events: list[dict[str, Any]]) -> None:
    removed_luma = 0
    remaining_links = 0
    events_with_links = 0
    for event in events:
        original_links = event.get("links")
        if not isinstance(original_links, list) or any(
            not isinstance(link, dict) for link in original_links
        ):
            raise EventDescriptionBridgeError("event description baseline link shape mismatch")
        kept_links = [link for link in original_links if not _is_luma_link(link.get("url"))]
        removed_luma += len(original_links) - len(kept_links)
        event["links"] = kept_links
        remaining_links += len(kept_links)
        events_with_links += bool(kept_links)
    if len(events) != EXPECTED_EVENT_COUNT:
        raise EventDescriptionBridgeError("event description baseline event count mismatch")
    if removed_luma != EXPECTED_LUMA_LINK_REMOVALS:
        raise EventDescriptionBridgeError("event description Luma-link count mismatch")
    if remaining_links != EXPECTED_NON_LUMA_LINKS:
        raise EventDescriptionBridgeError("event description retained-link count mismatch")
    if events_with_links != EXPECTED_EVENTS_WITH_LINKS:
        raise EventDescriptionBridgeError("event description linked-event count mismatch")
    if len(events) - events_with_links != EXPECTED_EVENTS_WITHOUT_LINKS:
        raise EventDescriptionBridgeError("event description linkless-event count mismatch")


def apply_empty_description_rollback_to_events(events: list[dict[str, Any]]) -> None:
    """Apply the reviewed Luma-free emergency shape to the exact legacy projection."""

    if pretty_json_sha256(events) != BASELINE_EVENTS_SHA256:
        raise EventDescriptionBridgeError("event description baseline projection mismatch")
    _remove_luma_links_and_validate(events)
    for event in events:
        event["record_schema_version"] = EVENT_RECORD_SCHEMA_VERSION
        event["description_html"] = ""
        event["description_text"] = ""
        event["description_provenance"] = None


def apply_bridge_to_events(
    events: list[dict[str, Any]], bridge: dict[str, Any] | None = None
) -> None:
    selected = bridge or load_event_description_bridge()
    _validate_bridge(selected)
    if pretty_json_sha256(events) != BASELINE_EVENTS_SHA256:
        raise EventDescriptionBridgeError("event description baseline projection mismatch")
    matches = {canonical_json_sha256(entry["target"]): entry for entry in selected["matches"]}
    matched_targets: set[str] = set()
    _remove_luma_links_and_validate(events)
    for event in events:
        target_digest = canonical_json_sha256(_target_tuple(event.get("provenance", {})))
        entry = matches.get(target_digest)
        event["record_schema_version"] = EVENT_RECORD_SCHEMA_VERSION
        if entry is None:
            event["description_html"] = ""
            event["description_text"] = ""
            event["description_provenance"] = None
            continue
        matched_targets.add(target_digest)
        event["description_html"] = entry["description_html"]
        event["description_text"] = entry["description_text"]
        event["description_provenance"] = {
            "bridge_schema_version": BRIDGE_SCHEMA_VERSION,
            "bridge_content_sha256": selected["content_sha256"],
            "entry_sha256": entry["entry_sha256"],
            "source_identity_sha256": entry["source_identity_sha256"],
            "source_description_sha256": entry["source_description_sha256"],
            "matching_policy_version": MATCHING_POLICY_VERSION,
            "markdown_policy_version": MARKDOWN_POLICY_VERSION,
            "link_policy_version": LINK_POLICY_VERSION,
        }
    if len(events) != EXPECTED_EVENT_COUNT or len(matched_targets) != EXPECTED_MATCH_COUNT:
        raise EventDescriptionBridgeError("event description target coverage mismatch")
    if matched_targets != set(matches):
        raise EventDescriptionBridgeError("event description target is missing")


def validate_projected_event(event: dict[str, Any], bridge: dict[str, Any]) -> None:
    if event.get("record_schema_version") != EVENT_RECORD_SCHEMA_VERSION:
        raise EventDescriptionBridgeError("event record schema version mismatch")
    description_html = event.get("description_html")
    description_text = event.get("description_text")
    provenance = event.get("description_provenance")
    target_digest = canonical_json_sha256(_target_tuple(event.get("provenance", {})))
    entries = {canonical_json_sha256(entry["target"]): entry for entry in bridge["matches"]}
    entry = entries.get(target_digest)
    if not isinstance(description_html, str) or not isinstance(description_text, str):
        raise EventDescriptionBridgeError("event description record body shape mismatch")
    if any(_is_luma_link(link.get("url")) for link in event.get("links", [])):
        raise EventDescriptionBridgeError("event description record contains a Luma link")
    expected_provenance = (
        {
            "bridge_schema_version": BRIDGE_SCHEMA_VERSION,
            "bridge_content_sha256": bridge["content_sha256"],
            "entry_sha256": entry["entry_sha256"],
            "source_identity_sha256": entry["source_identity_sha256"],
            "source_description_sha256": entry["source_description_sha256"],
            "matching_policy_version": MATCHING_POLICY_VERSION,
            "markdown_policy_version": MARKDOWN_POLICY_VERSION,
            "link_policy_version": LINK_POLICY_VERSION,
        }
        if entry is not None
        else {}
    )
    normalization_keys = {
        "normalization_schema_version",
        "normalization_content_sha256",
        "normalization_original_description_sha256",
        "removed_speaker_bio",
        "removed_platform_boilerplate",
        "normalized_internal_links",
    }

    def normalized_provenance() -> tuple[dict[str, Any], str, str]:
        if entry is None or not isinstance(provenance, dict):
            raise EventDescriptionBridgeError("event description normalization source mismatch")
        try:
            plan = load_normalization_plan()
        except Exception as exc:
            raise EventDescriptionBridgeError(
                "event description normalization plan is unavailable"
            ) from exc
        row = next(
            (item for item in plan["events"] if item["identity_id"] == event.get("identity_id")),
            None,
        )
        if row is None or row["slug"] != event.get("slug"):
            raise EventDescriptionBridgeError("event description normalization identity mismatch")
        original_html = entry["description_html"]
        original_digest = hashlib.sha256(original_html.encode()).hexdigest()
        if original_digest != row["before_description_sha256"]:
            raise EventDescriptionBridgeError("event description normalization source drift")
        result = normalize_description_html(original_html)
        expected = {
            **expected_provenance,
            "normalization_schema_version": NORMALIZATION_SCHEMA_VERSION,
            "normalization_content_sha256": plan["content_sha256"],
            "normalization_original_description_sha256": original_digest,
            "removed_speaker_bio": result.removed_speaker_bio,
            "removed_platform_boilerplate": result.removed_platform_boilerplate,
            "normalized_internal_links": result.normalized_internal_links,
        }
        return expected, result.html, result.text

    if not description_html:
        if (
            description_text
            or entry is None
            or not isinstance(provenance, dict)
            or set(provenance) != set(expected_provenance) | normalization_keys
        ):
            if description_text or provenance is not None or entry is not None:
                raise EventDescriptionBridgeError("event description empty-record mismatch")
            return
        expected_normalized, normalized_html, normalized_text = normalized_provenance()
        if normalized_html or normalized_text or provenance != expected_normalized:
            raise EventDescriptionBridgeError("event description normalized-empty mismatch")
        return

    validate_description_html(description_html)
    if (
        description_text != description_plain_text(description_html)
        or not description_text.strip()
        or not isinstance(provenance, dict)
        or entry is None
    ):
        raise EventDescriptionBridgeError("event description populated-record mismatch")
    if set(provenance) == set(expected_provenance):
        if (
            description_html != entry["description_html"]
            or description_text != entry["description_text"]
            or provenance != expected_provenance
        ):
            raise EventDescriptionBridgeError("event description record bridge mismatch")
        return
    if set(provenance) != set(expected_provenance) | normalization_keys:
        raise EventDescriptionBridgeError("event description provenance shape mismatch")
    expected_normalized, normalized_html, normalized_text = normalized_provenance()
    if (
        description_html != normalized_html
        or description_text != normalized_text
        or provenance != expected_normalized
    ):
        raise EventDescriptionBridgeError("event description normalized record mismatch")
