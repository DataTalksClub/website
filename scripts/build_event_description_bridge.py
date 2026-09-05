#!/usr/bin/env python3
"""Build the reviewed public-safe event-description bridge from a local exporter snapshot."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import mistune

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.projection_build.event_description_bridge import (  # noqa: E402
    BASELINE_EVENTS_SHA256,
    BRIDGE_PATH,
    BRIDGE_PUBLIC_PATH,
    BRIDGE_SCHEMA_PATH,
    BRIDGE_SCHEMA_PUBLIC_PATH,
    BRIDGE_SCHEMA_VERSION,
    DESCRIPTION_MANIFEST_SHA256,
    EXPECTED_DISTINCT_URLS,
    EXPECTED_EVENT_COUNT,
    EXPECTED_GAP_COUNT,
    EXPECTED_MATCH_COUNT,
    EXPECTED_PAIR_COUNT,
    EXPECTED_REMOTE_IMAGES_OMITTED,
    EXPECTED_UNDESCRIBED_COUNT,
    EXPECTED_URL_OCCURRENCES,
    EXPORTER_REVISION,
    LEGACY_REPOSITORY,
    LEGACY_REVISION,
    LEGACY_SOURCE_CHECKSUM,
    LEGACY_SOURCE_PATH,
    LINK_POLICY_VERSION,
    LINK_REVIEW_INVENTORY_SHA256,
    MARKDOWN_POLICY_VERSION,
    MATCHING_POLICY_VERSION,
    PUBLIC_EVENT_ALLOWLIST_SHA256,
    SAFE_HTML_CLEANER,
    SAFE_SOURCE_SHA256,
    canonical_json_sha256,
    description_plain_text,
    validate_description_html,
)
from scripts.projection_build.event_description_link_policy import (  # noqa: E402
    EXPECTED_LINK_DECISION_COUNTS,
    EventDescriptionLinkPolicyError,
    classify_rendered_url,
    classify_source_url,
    projection_routes_and_fragments,
)

PROJECTION_EVENTS_PATH = (
    REPOSITORY_ROOT / "temporary" / "content" / "public_projection" / "events.json"
)
BASELINE_WEBSITE_REVISION = "4cad269d576217679ac6c9ce02286e7939d8b043"
MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
MAX_DESCRIPTION_BYTES = 8 * 1024
EXPECTED_DESCRIPTION_BYTES = 275_761
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
URL_LITERAL = re.compile(r"https?://[^\s)>\]]+")
MARKDOWN_TABLE = re.compile(r"(?m)^\s*\|?.+\|.+\n\s*\|?\s*:?-{3,}(?:\s*\|\s*:?-{3,})+")
UNSAFE_MARKDOWN_DESTINATION = re.compile(
    r"\]\(\s*(?:(?:javascript|data|vbscript):|//)",
    re.IGNORECASE,
)
ACTION_WORDS = re.compile(
    r"\b(?:apply|attend|book|join|register|registration|reserve|rsvp|sign[ -]?up)\b",
    re.IGNORECASE,
)
STANDALONE_ACTION_START = re.compile(
    r"^(?:please\s+)?(?:apply|book|register|reserve|rsvp|sign[ -]?up)\b",
    re.IGNORECASE,
)
STANDALONE_JOIN_ACTION = re.compile(
    r"^(?:(?:bring|share)\b[^.!?]{0,160}\band\s+)?join\s+"
    r"(?:our\s+slack\s+community|us\s+(?:live|online|now|today))\b",
    re.IGNORECASE,
)
REMOVED_FORM_LABEL = re.compile(r"^form[.!?]?$", re.IGNORECASE)
EXPECTED_CHECKPOINT_FIELDS = frozenset({"event", "fetched_at", "guests", "schema_version"})
EXPECTED_EVENT_FIELDS = frozenset(
    {
        "access",
        "calendar_id",
        "coordinate",
        "cover_url",
        "created_at",
        "description",
        "description_md",
        "display_price",
        "duration_interval",
        "end_at",
        "feedback_email",
        "geo_address_json",
        "guest_counts",
        "hosts",
        "id",
        "location_type",
        "location_visibility",
        "meeting_url",
        "name",
        "platform",
        "registration_open",
        "registration_questions",
        "require_approval",
        "spots_remaining",
        "start_at",
        "timezone",
        "url",
        "user_id",
        "visibility",
        "waitlist_status",
    }
)
EXPECTED_EVENT_FIELD_SETS = frozenset(
    {
        EXPECTED_EVENT_FIELDS,
        EXPECTED_EVENT_FIELDS
        | {
            "api_id",
            "calendar_api_id",
            "geo_latitude",
            "geo_longitude",
            "tags",
            "user_api_id",
            "zoom_meeting_url",
        },
    }
)
MARKDOWN = mistune.create_markdown(renderer="ast", plugins=["url"])


class BridgeBuildError(RuntimeError):
    """A bounded, content-free reconciliation failure."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _run_git(arguments: list[str], *, root: Path) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        },
    )
    if result.returncode:
        raise BridgeBuildError("exporter revision validation failed")
    return result.stdout.strip()


def _validate_exporter(exporter_root: Path, source_root: Path) -> None:
    if exporter_root.is_symlink() or source_root.is_symlink():
        raise BridgeBuildError("exporter filesystem boundary mismatch")
    if not exporter_root.is_dir() or not source_root.is_dir():
        raise BridgeBuildError("exporter source is unavailable")
    if source_root.parent != exporter_root or source_root.name != "luma-events":
        raise BridgeBuildError("exporter source boundary mismatch")
    if _run_git(["rev-parse", "HEAD"], root=exporter_root) != EXPORTER_REVISION:
        raise BridgeBuildError("exporter revision mismatch")
    if _run_git(["status", "--porcelain"], root=exporter_root):
        raise BridgeBuildError("exporter checkout is not clean")


def _directory_files(root: Path, suffix: str) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        raise BridgeBuildError("exporter pair directory mismatch")
    files: dict[str, Path] = {}
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise BridgeBuildError("exporter pair directory is unreadable") from exc
    for entry in entries:
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
            raise BridgeBuildError("exporter pair filesystem entry mismatch")
        if entry.suffix != suffix or SAFE_FILENAME.fullmatch(entry.name) is None:
            raise BridgeBuildError("exporter pair filename mismatch")
        if entry.stem in files:
            raise BridgeBuildError("exporter pair identity collision")
        files[entry.stem] = entry
    return files


def _read_bounded(path: Path, *, maximum: int, error: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise BridgeBuildError(error)
        size = path.stat().st_size
        if size <= 0 or size > maximum:
            raise BridgeBuildError(error)
        return path.read_bytes()
    except BridgeBuildError:
        raise
    except OSError as exc:
        raise BridgeBuildError(error) from exc


def _skip_json_whitespace(payload: str, index: int) -> int:
    while index < len(payload) and payload[index] in " \t\r\n":
        index += 1
    return index


def _skip_json_string(payload: str, index: int) -> int:
    if index >= len(payload) or payload[index] != '"':
        raise BridgeBuildError("checkpoint JSON is invalid")
    index += 1
    while index < len(payload):
        character = payload[index]
        if character == '"':
            return index + 1
        if ord(character) < 0x20:
            raise BridgeBuildError("checkpoint JSON is invalid")
        if character != "\\":
            index += 1
            continue
        index += 1
        if index >= len(payload):
            raise BridgeBuildError("checkpoint JSON is invalid")
        escaped = payload[index]
        if escaped == "u":
            digits = payload[index + 1 : index + 5]
            if len(digits) != 4 or any(value not in "0123456789abcdefABCDEF" for value in digits):
                raise BridgeBuildError("checkpoint JSON is invalid")
            index += 5
            continue
        if escaped not in '"\\/bfnrt':
            raise BridgeBuildError("checkpoint JSON is invalid")
        index += 1
    raise BridgeBuildError("checkpoint JSON is invalid")


def _skip_json_value(payload: str, index: int, *, depth: int = 0) -> int:
    if depth > 100:
        raise BridgeBuildError("checkpoint JSON nesting is invalid")
    index = _skip_json_whitespace(payload, index)
    if index >= len(payload):
        raise BridgeBuildError("checkpoint JSON is invalid")
    character = payload[index]
    if character == '"':
        return _skip_json_string(payload, index)
    if character == "[":
        index = _skip_json_whitespace(payload, index + 1)
        if index < len(payload) and payload[index] == "]":
            return index + 1
        while True:
            index = _skip_json_value(payload, index, depth=depth + 1)
            index = _skip_json_whitespace(payload, index)
            if index < len(payload) and payload[index] == "]":
                return index + 1
            if index >= len(payload) or payload[index] != ",":
                raise BridgeBuildError("checkpoint JSON is invalid")
            index += 1
    if character == "{":
        index = _skip_json_whitespace(payload, index + 1)
        if index < len(payload) and payload[index] == "}":
            return index + 1
        while True:
            key_end = _skip_json_string(payload, index)
            index = _skip_json_whitespace(payload, key_end)
            if index >= len(payload) or payload[index] != ":":
                raise BridgeBuildError("checkpoint JSON is invalid")
            index = _skip_json_value(payload, index + 1, depth=depth + 1)
            index = _skip_json_whitespace(payload, index)
            if index < len(payload) and payload[index] == "}":
                return index + 1
            if index >= len(payload) or payload[index] != ",":
                raise BridgeBuildError("checkpoint JSON is invalid")
            index = _skip_json_whitespace(payload, index + 1)
    for literal in ("true", "false", "null"):
        if payload.startswith(literal, index):
            return index + len(literal)
    match = JSON_NUMBER.match(payload, index)
    if match is not None:
        return match.end()
    raise BridgeBuildError("checkpoint JSON is invalid")


def _json_object_spans(payload: str) -> dict[str, tuple[int, int]]:
    index = _skip_json_whitespace(payload, 0)
    if index >= len(payload) or payload[index] != "{":
        raise BridgeBuildError("checkpoint JSON is invalid")
    index = _skip_json_whitespace(payload, index + 1)
    values: dict[str, tuple[int, int]] = {}
    if index < len(payload) and payload[index] == "}":
        end = _skip_json_whitespace(payload, index + 1)
        if end != len(payload):
            raise BridgeBuildError("checkpoint JSON is invalid")
        return values
    while True:
        key_start = index
        key_end = _skip_json_string(payload, key_start)
        try:
            key = json.loads(payload[key_start:key_end])
        except json.JSONDecodeError as exc:
            raise BridgeBuildError("checkpoint JSON is invalid") from exc
        if not isinstance(key, str) or key in values:
            raise BridgeBuildError("checkpoint JSON object key is invalid")
        index = _skip_json_whitespace(payload, key_end)
        if index >= len(payload) or payload[index] != ":":
            raise BridgeBuildError("checkpoint JSON is invalid")
        value_start = _skip_json_whitespace(payload, index + 1)
        value_end = _skip_json_value(payload, value_start)
        values[key] = (value_start, value_end)
        index = _skip_json_whitespace(payload, value_end)
        if index < len(payload) and payload[index] == "}":
            index = _skip_json_whitespace(payload, index + 1)
            if index != len(payload):
                raise BridgeBuildError("checkpoint JSON is invalid")
            return values
        if index >= len(payload) or payload[index] != ",":
            raise BridgeBuildError("checkpoint JSON is invalid")
        index = _skip_json_whitespace(payload, index + 1)


def _decode_selected_json_value(payload: str, span: tuple[int, int]) -> Any:
    try:
        return json.loads(payload[span[0] : span[1]])
    except json.JSONDecodeError as exc:
        raise BridgeBuildError("checkpoint JSON is invalid") from exc


def _normalized_provider_identity(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise BridgeBuildError("provider event identity is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise BridgeBuildError("provider event identity is invalid") from exc
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or hostname not in {"luma.com", "lu.ma"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in {None, 80, 443}
    ):
        raise BridgeBuildError("provider event identity is invalid")
    path = parsed.path.removesuffix("/")
    if not path.startswith("/") or not path[1:] or "/" in path[1:]:
        raise BridgeBuildError("provider event identity path is invalid")
    return f"https://luma.com{path}"


def _normalize_public_title(value: Any) -> str:
    if not isinstance(value, str):
        raise BridgeBuildError("source event title is invalid")
    normalized = " ".join(unicodedata.normalize("NFC", html.unescape(value)).split())
    if not normalized or len(normalized) > 1_000:
        raise BridgeBuildError("source event title is invalid")
    return normalized.casefold()


def _normalize_utc_start(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 100:
        raise BridgeBuildError("source event instant is invalid")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BridgeBuildError("source event instant is invalid") from exc
    if instant.tzinfo is None:
        raise BridgeBuildError("source event instant is invalid")
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_projection_events() -> list[dict[str, Any]]:
    try:
        payload = PROJECTION_EVENTS_PATH.read_bytes()
    except OSError as exc:
        raise BridgeBuildError("accepted event projection is unavailable") from exc
    if _sha256_bytes(payload) != BASELINE_EVENTS_SHA256:
        result = subprocess.run(
            [
                "git",
                "show",
                f"{BASELINE_WEBSITE_REVISION}:content/public_projection/events.json",
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=False,
            env={
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
            },
        )
        if result.returncode:
            raise BridgeBuildError("accepted event projection is unavailable")
        payload = result.stdout
    if _sha256_bytes(payload) != BASELINE_EVENTS_SHA256:
        raise BridgeBuildError("accepted event projection digest mismatch")
    try:
        events = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeBuildError("accepted event projection is unavailable") from exc
    if not isinstance(events, list) or len(events) != EXPECTED_EVENT_COUNT:
        raise BridgeBuildError("accepted event projection count mismatch")
    return events


def _projection_routes_and_fragments() -> tuple[set[str], dict[str, set[str]]]:
    try:
        return projection_routes_and_fragments()
    except EventDescriptionLinkPolicyError as exc:
        raise BridgeBuildError(str(exc)) from exc


def _classify_url(
    value: str,
    *,
    public_paths: set[str],
    fragments: dict[str, set[str]],
) -> tuple[str, str]:
    try:
        decision, final = classify_source_url(
            value,
            public_paths=public_paths,
            fragments=fragments,
        )
        if final:
            rendered_decision = classify_rendered_url(
                final,
                public_paths=public_paths,
                fragments=fragments,
            )
            if rendered_decision != decision:
                raise EventDescriptionLinkPolicyError("description rendered link decision mismatch")
        return decision, final
    except EventDescriptionLinkPolicyError as exc:
        raise BridgeBuildError(str(exc)) from exc


def _plain_inline(tokens: list[dict[str, Any]]) -> str:
    values: list[str] = []
    stack = list(reversed(tokens))
    while stack:
        token = stack.pop()
        token_type = token.get("type")
        if token_type in {"text", "codespan"}:
            values.append(str(token.get("raw", "")))
        else:
            stack.extend(reversed(token.get("children", [])))
    return " ".join("".join(values).split())


def _strip_markup(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _action_only(value: str) -> bool:
    normalized = " ".join(value.split())
    return len(normalized) <= 240 and bool(ACTION_WORDS.search(normalized))


def _strip_standalone_action_sentences(value: str) -> tuple[str, bool]:
    value = re.sub(r"\*{2,}", "", value.replace("\u200b", "").replace("\ufeff", ""))
    parts = re.split(r"(?<=[.!?])(\s+)", value)
    removed = False
    kept: list[str] = []
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        normalized = " ".join(sentence.split()).strip("\u200b")
        action = bool(
            normalized
            and len(normalized) <= 240
            and (
                STANDALONE_ACTION_START.search(normalized)
                or STANDALONE_JOIN_ACTION.search(normalized)
            )
        )
        if action:
            removed = True
            continue
        if kept and index > 0:
            kept.append(parts[index - 1])
        kept.append(sentence)
    return "".join(kept), removed


def _clean_removed_action_context(value: str) -> str:
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r",\s*(?:and\s+)?(?=,?\s*and\b)", ", ", value, flags=re.IGNORECASE)
    value = re.sub(
        r"(?:\s|&nbsp;)*(?:if\b[^.!?]{0,160}\byou\s+can|you\s+can)\s*[.!?]?\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    if re.search(r"\bcourse contents!\s*$", value, flags=re.IGNORECASE) is None:
        value = re.sub(r"(?:\s|\u200b)*[!|·:*_-]+(?:\s|\u200b)*$", "", value)
    value = re.sub(
        r"(?:\s+|^)use\s+(?:this|the)\s+link\s+to\s+submit\s+them\s+in\s+advance[.!?]?$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"(\bcourse contents!)\.", r"\1", value, flags=re.IGNORECASE)
    return value.rstrip()


class DescriptionRenderer:
    def __init__(self, public_paths: set[str], fragments: dict[str, set[str]]) -> None:
        self.public_paths = public_paths
        self.fragments = fragments
        self.remote_images_omitted = 0

    def classify(self, value: str) -> tuple[str, str]:
        return _classify_url(
            value,
            public_paths=self.public_paths,
            fragments=self.fragments,
        )

    def inline(self, tokens: list[dict[str, Any]]) -> tuple[str, bool]:
        parts: list[str] = []
        removed_action = False
        for token in tokens:
            token_type = token.get("type")
            if token_type == "text":
                raw = str(token.get("raw", ""))
                cleaned, removed = _strip_standalone_action_sentences(raw)
                parts.append(html.escape(cleaned))
                removed_action = removed_action or removed
            elif token_type == "codespan":
                parts.append(f"<code>{html.escape(str(token.get('raw', '')))}</code>")
            elif token_type in {"emphasis", "strong"}:
                body, removed = self.inline(token.get("children", []))
                tag = "em" if token_type == "emphasis" else "strong"
                if _strip_markup(body):
                    parts.append(f"<{tag}>{body}</{tag}>")
                else:
                    removed = True
                removed_action = removed_action or removed
            elif token_type == "linebreak":
                parts.append(" ")
            elif token_type == "link":
                destination = token.get("attrs", {}).get("url")
                if not isinstance(destination, str):
                    raise BridgeBuildError("description link shape is invalid")
                decision, final = self.classify(destination)
                label_tokens = token.get("children", [])
                label_text = _plain_inline(label_tokens)
                label_urls = [value.rstrip(".,;:") for value in URL_LITERAL.findall(label_text)]
                label_has_removed_url = any(not self.classify(value)[1] for value in label_urls)
                if not final:
                    removed_action = True
                    if (
                        label_has_removed_url
                        or _action_only(label_text)
                        or REMOVED_FORM_LABEL.fullmatch(label_text) is not None
                    ):
                        if parts:
                            parts[-1] = re.sub(
                                r",\s*[^,;.!?]{0,120}\b"
                                r"(?:at|from|in|on|through|to|via|with)\s+(?:the\s+)?$",
                                "",
                                parts[-1],
                                flags=re.IGNORECASE,
                            )
                        continue
                    label_html, nested_removed = self.inline(label_tokens)
                    parts.append(label_html)
                    removed_action = removed_action or nested_removed
                    continue
                label_html, nested_removed = self.inline(label_tokens)
                if not _strip_markup(label_html):
                    removed_action = True
                    continue
                escaped_url = html.escape(final, quote=True)
                if decision == "internal_rewritten":
                    parts.append(f'<a class="app-link" href="{escaped_url}">{label_html}</a>')
                elif decision == "external_resource_kept":
                    parts.append(
                        f'<a class="app-link" href="{escaped_url}" target="_blank" '
                        'rel="noopener noreferrer">'
                        f'{label_html}<span class="sr-only"> (opens in a new tab)</span></a>'
                    )
                else:
                    raise BridgeBuildError("description link decision is invalid")
                removed_action = removed_action or nested_removed
            elif token_type == "image":
                destination = token.get("attrs", {}).get("url")
                if not isinstance(destination, str):
                    raise BridgeBuildError("description image shape is invalid")
                decision, _ = self.classify(destination)
                if decision != "remote_image_removed":
                    raise BridgeBuildError("description image has no omission decision")
                self.remote_images_omitted += 1
                removed_action = True
            else:
                raise BridgeBuildError("description Markdown inline construct is unsupported")
        return "".join(parts), removed_action

    def blocks(self, tokens: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for token in tokens:
            token_type = token.get("type")
            if token_type == "blank_line" or token_type == "thematic_break":
                continue
            if token_type in {"paragraph", "block_text"}:
                body, removed = self.inline(token.get("children", []))
                text = _strip_markup(body)
                if not text or (
                    removed
                    and (_action_only(text) or ("<a " not in body and text.rstrip().endswith(":")))
                ):
                    continue
                if removed:
                    body = _clean_removed_action_context(body)
                    text = _strip_markup(body)
                    if not text:
                        continue
                if token_type == "paragraph":
                    parts.append(f'<p class="mt-4 leading-7">{body}</p>')
                else:
                    parts.append(body)
                continue
            if token_type == "heading":
                body, removed = self.inline(token.get("children", []))
                text = _strip_markup(body)
                if not text or (removed and _action_only(text)):
                    continue
                level = token.get("attrs", {}).get("level")
                if (
                    isinstance(level, bool)
                    or not isinstance(level, int)
                    or level not in range(1, 7)
                ):
                    raise BridgeBuildError("description heading level is invalid")
                normalized_level = max(2, level)
                classes = (
                    "mt-8 text-xl font-semibold"
                    if normalized_level == 2
                    else ("mt-6 text-lg font-semibold")
                )
                parts.append(f'<h{normalized_level} class="{classes}">{body}</h{normalized_level}>')
                continue
            if token_type == "block_quote":
                body = self.blocks(token.get("children", []))
                if _strip_markup(body):
                    parts.append(f'<blockquote class="mt-6 border-l-4 pl-4">{body}</blockquote>')
                continue
            if token_type == "list":
                ordered = token.get("attrs", {}).get("ordered") is True
                tag = "ol" if ordered else "ul"
                list_class = "mt-4 list-decimal pl-6" if ordered else "mt-4 list-disc pl-6"
                items: list[str] = []
                for child in token.get("children", []):
                    if child.get("type") != "list_item":
                        raise BridgeBuildError("description list shape is invalid")
                    body = self.blocks(child.get("children", []))
                    if _strip_markup(body):
                        items.append(f"<li>{body}</li>")
                if items:
                    parts.append(f'<{tag} class="{list_class}">{"".join(items)}</{tag}>')
                continue
            raise BridgeBuildError("description Markdown block construct is unsupported")
        return "".join(parts)

    def render(self, markdown: str) -> tuple[str, str]:
        if MARKDOWN_TABLE.search(markdown):
            raise BridgeBuildError("description Markdown table is unsupported")
        if UNSAFE_MARKDOWN_DESTINATION.search(markdown):
            raise BridgeBuildError("description Markdown destination is unsafe")
        try:
            tokens = MARKDOWN(markdown)
        except Exception as exc:
            raise BridgeBuildError("description Markdown parsing failed") from exc
        if not isinstance(tokens, list):
            raise BridgeBuildError("description Markdown parse shape is invalid")
        rendered = self.blocks(tokens)
        sanitized = SAFE_HTML_CLEANER.clean(rendered)
        if sanitized != rendered:
            raise BridgeBuildError("description sanitizer changed generated HTML")
        try:
            validate_description_html(sanitized)
        except Exception as exc:
            raise BridgeBuildError("description generated HTML validation failed") from exc
        text = description_plain_text(sanitized)
        if not text:
            raise BridgeBuildError("description becomes empty after safe rendering")
        return sanitized, text


def _load_source_pairs(source_root: Path) -> list[dict[str, Any]]:
    checkpoints = _directory_files(source_root / "_json", ".json")
    descriptions = _directory_files(source_root / "descriptions", ".md")
    if (
        len(checkpoints) != EXPECTED_PAIR_COUNT
        or len(descriptions) != EXPECTED_PAIR_COUNT
        or set(checkpoints) != set(descriptions)
    ):
        raise BridgeBuildError("exporter pair baseline mismatch")
    records: list[dict[str, Any]] = []
    total_description_bytes = 0
    for key in sorted(checkpoints):
        checkpoint_bytes = _read_bounded(
            checkpoints[key],
            maximum=MAX_CHECKPOINT_BYTES,
            error="checkpoint boundary mismatch",
        )
        try:
            checkpoint_text = checkpoint_bytes.decode("utf-8")
        except UnicodeError as exc:
            raise BridgeBuildError("checkpoint JSON encoding is invalid") from exc
        checkpoint_spans = _json_object_spans(checkpoint_text)
        if set(checkpoint_spans) != EXPECTED_CHECKPOINT_FIELDS:
            raise BridgeBuildError("checkpoint schema mismatch")
        if _decode_selected_json_value(checkpoint_text, checkpoint_spans["schema_version"]) != 1:
            raise BridgeBuildError("checkpoint version mismatch")
        event_span = checkpoint_spans["event"]
        event_text = checkpoint_text[event_span[0] : event_span[1]]
        event_spans = _json_object_spans(event_text)
        if frozenset(event_spans) not in EXPECTED_EVENT_FIELD_SETS:
            raise BridgeBuildError("checkpoint event schema mismatch")
        event = {
            field: _decode_selected_json_value(event_text, event_spans[field])
            for field in ("url", "name", "start_at")
        }
        description_bytes = _read_bounded(
            descriptions[key],
            maximum=MAX_DESCRIPTION_BYTES,
            error="description boundary mismatch",
        )
        total_description_bytes += len(description_bytes)
        try:
            markdown = description_bytes.decode("utf-8")
        except UnicodeError as exc:
            raise BridgeBuildError("description encoding is invalid") from exc
        if not markdown.strip():
            raise BridgeBuildError("description is empty")
        provider_identity = _normalized_provider_identity(event.get("url"))
        records.append(
            {
                "provider_identity": provider_identity,
                "source_identity_sha256": _sha256_bytes(provider_identity.encode()),
                "source_description_sha256": _sha256_bytes(description_bytes),
                "normalized_title": _normalize_public_title(event.get("name")),
                "utc_start": _normalize_utc_start(event.get("start_at")),
                "markdown": markdown,
            }
        )
    if total_description_bytes != EXPECTED_DESCRIPTION_BYTES:
        raise BridgeBuildError("description byte baseline mismatch")
    identities = [record["provider_identity"] for record in records]
    if len(identities) != len(set(identities)):
        raise BridgeBuildError("source provider identity collision")
    return records


def _projection_targets(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    luma_links = 0
    for event in events:
        for link in event.get("links", []):
            url = link.get("url")
            if not isinstance(url, str):
                raise BridgeBuildError("accepted event link shape mismatch")
            hostname = (urlsplit(url).hostname or "").casefold()
            if hostname not in {"luma.com", "lu.ma"}:
                continue
            luma_links += 1
            identity = _normalized_provider_identity(url)
            if identity in targets:
                raise BridgeBuildError("projection provider identity collision")
            targets[identity] = event
    if luma_links != EXPECTED_PAIR_COUNT or len(targets) != EXPECTED_PAIR_COUNT:
        raise BridgeBuildError("projection provider identity baseline mismatch")
    return targets


def _target_tuple(event: dict[str, Any]) -> dict[str, str]:
    provenance = event.get("provenance", {})
    target = {
        "repository": provenance.get("repository", ""),
        "revision": provenance.get("revision", ""),
        "source_path": provenance.get("source_path", ""),
        "source_key": provenance.get("source_key", ""),
        "checksum": provenance.get("checksum", ""),
    }
    if (
        target["repository"] != LEGACY_REPOSITORY
        or target["revision"] != LEGACY_REVISION
        or target["source_path"] != LEGACY_SOURCE_PATH
        or target["checksum"] != LEGACY_SOURCE_CHECKSUM
        or not target["source_key"]
    ):
        raise BridgeBuildError("projection canonical provenance mismatch")
    return target


def _link_review(
    matched: list[dict[str, Any]],
    *,
    public_paths: set[str],
    fragments: dict[str, set[str]],
) -> dict[str, Any]:
    occurrences: Counter[str] = Counter()
    for record in matched:
        for value in URL_LITERAL.findall(record["markdown"]):
            occurrences[value.rstrip(".,;:")] += 1
    if sum(occurrences.values()) != EXPECTED_URL_OCCURRENCES:
        raise BridgeBuildError("description URL occurrence baseline mismatch")
    if len(occurrences) != EXPECTED_DISTINCT_URLS:
        raise BridgeBuildError("description URL literal baseline mismatch")
    decisions: list[dict[str, Any]] = []
    decision_counts: Counter[str] = Counter()
    for value, count in sorted(occurrences.items()):
        decision, final = _classify_url(
            value,
            public_paths=public_paths,
            fragments=fragments,
        )
        decision_counts[decision] += count
        decisions.append(
            {
                "url_sha256": _sha256_bytes(value.encode()),
                "occurrences": count,
                "decision": decision,
                "final_url_sha256": _sha256_bytes(final.encode()) if final else None,
            }
        )
    if dict(sorted(decision_counts.items())) != dict(EXPECTED_LINK_DECISION_COUNTS):
        raise BridgeBuildError("description link decision counts mismatch")
    return {
        "url_occurrences": sum(occurrences.values()),
        "distinct_url_literals": len(occurrences),
        "decision_counts": dict(sorted(decision_counts.items())),
        "decision_inventory_sha256": canonical_json_sha256(decisions),
        "remote_images_omitted": EXPECTED_REMOTE_IMAGES_OMITTED,
    }


def _safe_source_sha256(records: list[dict[str, Any]]) -> str:
    inventory = sorted(
        (
            {
                "source_identity_sha256": record["source_identity_sha256"],
                "source_description_sha256": record["source_description_sha256"],
                "normalized_public_title": record["normalized_title"],
                "utc_start": record["utc_start"],
            }
            for record in records
        ),
        key=lambda item: item["source_identity_sha256"],
    )
    return canonical_json_sha256(inventory)


def build_bridge(exporter_root: Path, source_root: Path) -> dict[str, Any]:
    exporter_root = exporter_root.absolute()
    source_root = source_root.absolute()
    _validate_exporter(exporter_root, source_root)
    events = _load_projection_events()
    source_records = _load_source_pairs(source_root)
    targets = _projection_targets(events)
    matched = [record for record in source_records if record["provider_identity"] in targets]
    gaps = [record for record in source_records if record["provider_identity"] not in targets]
    if len(matched) != EXPECTED_MATCH_COUNT or len(gaps) != EXPECTED_GAP_COUNT:
        raise BridgeBuildError("event description reconciliation baseline mismatch")
    public_paths, fragments = _projection_routes_and_fragments()
    link_review = _link_review(matched, public_paths=public_paths, fragments=fragments)
    if link_review["decision_inventory_sha256"] != LINK_REVIEW_INVENTORY_SHA256:
        raise BridgeBuildError("description reviewed-link inventory drift")
    renderer = DescriptionRenderer(public_paths, fragments)
    matches: list[dict[str, Any]] = []
    diagnostics: Counter[str] = Counter()
    for record in matched:
        event = targets[record["provider_identity"]]
        title_equal = record["normalized_title"] == _normalize_public_title(event.get("title"))
        instant_equal = record["utc_start"] == _normalize_utc_start(event.get("starts_at"))
        if title_equal and instant_equal:
            diagnostics["title_and_instant_equal"] += 1
        elif title_equal:
            diagnostics["title_equal_instant_different"] += 1
        elif instant_equal:
            diagnostics["title_different_instant_equal"] += 1
        else:
            diagnostics["title_and_instant_different"] += 1
        description_html, description_text = renderer.render(record["markdown"])
        entry = {
            "target": _target_tuple(event),
            "source_identity_sha256": record["source_identity_sha256"],
            "source_description_sha256": record["source_description_sha256"],
            "match_basis": "exact_normalized_provider_path",
            "description_html": description_html,
            "description_text": description_text,
        }
        entry["entry_sha256"] = canonical_json_sha256(entry)
        matches.append(entry)
    matches.sort(key=lambda item: item["target"]["source_key"])
    gap_entries = sorted(
        (
            {
                "source_identity_sha256": record["source_identity_sha256"],
                "reason": "no_exact_projection_luma_path",
            }
            for record in gaps
        ),
        key=lambda item: item["source_identity_sha256"],
    )
    expected_diagnostics = {
        "title_and_instant_equal": 36,
        "title_equal_instant_different": 113,
        "title_different_instant_equal": 2,
        "title_and_instant_different": 8,
    }
    if dict(diagnostics) != expected_diagnostics:
        raise BridgeBuildError("event description diagnostic baseline mismatch")
    if renderer.remote_images_omitted != EXPECTED_REMOTE_IMAGES_OMITTED:
        raise BridgeBuildError("event description image baseline mismatch")
    schema_sha256 = _sha256_bytes(
        _read_bounded(
            BRIDGE_SCHEMA_PATH,
            maximum=256 * 1024,
            error="event description bridge schema is unavailable",
        )
    )
    safe_source_sha256 = _safe_source_sha256(source_records)
    if safe_source_sha256 != SAFE_SOURCE_SHA256:
        raise BridgeBuildError("event description safe-source baseline drift")
    bridge: dict[str, Any] = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "schema": {"path": BRIDGE_SCHEMA_PUBLIC_PATH, "sha256": schema_sha256},
        "source": {
            "exporter_revision": EXPORTER_REVISION,
            "public_event_allowlist_sha256": PUBLIC_EVENT_ALLOWLIST_SHA256,
            "description_manifest_sha256": DESCRIPTION_MANIFEST_SHA256,
            "safe_source_sha256": safe_source_sha256,
        },
        "projection": {
            "events_sha256": BASELINE_EVENTS_SHA256,
            "event_count": EXPECTED_EVENT_COUNT,
            "repository": LEGACY_REPOSITORY,
            "revision": LEGACY_REVISION,
            "source_path": LEGACY_SOURCE_PATH,
            "source_checksum": LEGACY_SOURCE_CHECKSUM,
        },
        "policies": {
            "matching": MATCHING_POLICY_VERSION,
            "markdown": MARKDOWN_POLICY_VERSION,
            "links": LINK_POLICY_VERSION,
        },
        "counts": {
            "source_pairs": EXPECTED_PAIR_COUNT,
            "matches": EXPECTED_MATCH_COUNT,
            "gaps": EXPECTED_GAP_COUNT,
            "described_events": EXPECTED_MATCH_COUNT,
            "undescribed_events": EXPECTED_UNDESCRIBED_COUNT,
            **expected_diagnostics,
        },
        "matches": matches,
        "gaps": gap_entries,
        "link_review": link_review,
    }
    bridge["content_sha256"] = canonical_json_sha256(bridge)
    return bridge


def _safe_report(bridge: dict[str, Any], *, wrote: bool) -> dict[str, Any]:
    return {
        "schema_version": bridge["schema_version"],
        "content_sha256": bridge["content_sha256"],
        "safe_source_sha256": bridge["source"]["safe_source_sha256"],
        "counts": bridge["counts"],
        "link_review": bridge["link_review"],
        "wrote_artifact": wrote,
    }


def _write_bridge(output: Path, bridge: dict[str, Any]) -> None:
    output = output.absolute()
    expected_output = BRIDGE_PATH.absolute()
    if (
        output != expected_output
        or output.is_symlink()
        or not output.is_relative_to(REPOSITORY_ROOT)
    ):
        raise BridgeBuildError("event description bridge output boundary mismatch")
    payload = (json.dumps(bridge, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    descriptor = -1
    staging: Path | None = None
    try:
        descriptor, staging_name = tempfile.mkstemp(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".building",
        )
        staging = Path(staging_name)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, output)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if staging is not None and staging.exists() and not staging.is_symlink():
            staging.unlink()
        raise BridgeBuildError("event description bridge write failed") from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--exporter-root", type=Path, required=True)
    result.add_argument("--source-root", type=Path, required=True)
    result.add_argument("--output", type=Path, default=BRIDGE_PATH)
    result.add_argument(
        "--write",
        action="store_true",
        help=f"atomically replace the reviewed artifact at {BRIDGE_PUBLIC_PATH}",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    bridge = build_bridge(args.exporter_root, args.source_root)
    if args.write:
        _write_bridge(args.output, bridge)
    print(json.dumps(_safe_report(bridge, wrote=args.write), sort_keys=True))


if __name__ == "__main__":
    main()
