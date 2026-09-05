"""Turn a Luma description export into reviewed records for events we already have.

The 421-record legacy corpus in ``temporary/content/public_projection/events.json``
is a frozen one-time export: its descriptions come from the event description
bridge, which matches entries on the legacy ``_data/events.yaml`` tuple. An event
discovered in a Luma export has no such tuple -- ``events.identity`` gives it a
title and a canonical path and nothing else -- so the bridge structurally cannot
carry its description, and a projection rebuild blanks any event it has no entry
for.

So new events get their own staging artifact rather than a bigger bridge. It is
keyed on identity id, it is never read by the projection builder, and nothing
pinned to the legacy corpus moves when it grows.

What the artifact holds is the *finished* description: rendered through the same
Markdown and link policies the bridge uses, then put through the same
``normalize_description_html`` that removed the "about the speaker" block from
the legacy corpus. The removal decision is recorded per record rather than in a
separate replay plan, because there is no rebuild step to replay it against --
this artifact is itself the reviewed result.

Two things fail closed, both deliberately:

*A link with no reviewed decision.* The link policy pins the exact destinations
that may appear in rendered HTML, and host approval alone is not enough. A new
event naming a destination nobody has reviewed refuses to render, and the
builder reports the URL so a person can review it. Approving one is an edit to
``scripts/projection_build/event_description_link_policy.py``; it is never
inferred here.

*An event we do not already have.* This writes descriptions for identities that
exist. Creating an event is ``events.identity``'s job, reached through
``scripts/prod/import_events.py --discover-new-events-only``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_PATH = REPOSITORY_ROOT / "temporary" / "content" / "luma_event_descriptions.json"
DEFAULT_DESCRIPTION_ROOT = (
    REPOSITORY_ROOT / ".local" / "migration-data" / "events" / "luma" / "descriptions"
)

ARTIFACT_SCHEMA_VERSION = 1
DESCRIPTION_SOURCE = "luma-export-v1"

#: ``2026-08-10_test-containerize-and-deploy-an-ai-assisted-app_evt-ha8kjrvm.md``
_FILE_NAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<slug>[a-z0-9-]+)_(?P<luma>evt-[a-z0-9]+)$"
)
_MAXIMUM_MARKDOWN_BYTES = 256 * 1024


class LumaDescriptionError(RuntimeError):
    """A bounded refusal that carries a condition code, never a source value."""


@dataclass(frozen=True, slots=True)
class LumaDescriptionFile:
    path: Path
    date: str
    slug: str
    luma_event_id: str
    markdown: str

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class UnreviewedLink:
    slug: str
    url: str
    reason: str


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def discover_luma_descriptions(root: Path) -> tuple[LumaDescriptionFile, ...]:
    """Read every description export under ``root``, newest name order."""

    if not root.is_dir():
        raise LumaDescriptionError("luma_description_root_unavailable")
    found: list[LumaDescriptionFile] = []
    for path in sorted(root.glob("*.md")):
        matched = _FILE_NAME.fullmatch(path.stem)
        if matched is None:
            raise LumaDescriptionError("luma_description_name_unrecognised")
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise LumaDescriptionError("luma_description_unreadable") from error
        if len(raw) > _MAXIMUM_MARKDOWN_BYTES:
            raise LumaDescriptionError("luma_description_too_large")
        try:
            markdown = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise LumaDescriptionError("luma_description_not_utf8") from error
        if not markdown.strip():
            raise LumaDescriptionError("luma_description_empty")
        found.append(
            LumaDescriptionFile(
                path=path,
                date=matched["date"],
                slug=matched["slug"],
                luma_event_id=matched["luma"],
                markdown=markdown,
            )
        )
    if not found:
        raise LumaDescriptionError("luma_description_root_empty")
    duplicates = {item.slug for item in found}
    if len(duplicates) != len(found):
        raise LumaDescriptionError("luma_description_slug_duplicated")
    return tuple(found)


def _renderer() -> Any:
    # The renderer is the bridge builder's, deliberately: a new event's
    # description must pass exactly the Markdown and link policies the reviewed
    # corpus passed, not a second implementation that could drift from them.
    from scripts.build_event_description_bridge import (
        DescriptionRenderer,
        _projection_routes_and_fragments,
    )

    paths, fragments = _projection_routes_and_fragments()
    return DescriptionRenderer(paths, fragments)


def render_and_normalize(markdown: str, *, renderer: Any | None = None) -> dict[str, Any]:
    """Render one description through the reviewed policies, then strip the bio."""

    from scripts.build_event_description_bridge import BridgeBuildError
    from scripts.projection_build.event_speaker_bio_normalization import (
        normalize_description_html,
    )

    active = renderer if renderer is not None else _renderer()
    try:
        rendered_html, _ = active.render(markdown)
    except BridgeBuildError as error:
        raise LumaDescriptionError(f"luma_description_render_refused:{error}") from error
    normalized = normalize_description_html(rendered_html)
    if not normalized.html or not normalized.text:
        raise LumaDescriptionError("luma_description_empty_after_normalization")
    return {
        "rendered_sha256": hashlib.sha256(rendered_html.encode("utf-8")).hexdigest(),
        "description_html": normalized.html,
        "description_text": normalized.text,
        "removed_speaker_bio": normalized.removed_speaker_bio,
        "removed_platform_boilerplate": normalized.removed_platform_boilerplate,
        "normalized_internal_links": normalized.normalized_internal_links,
    }


def build_record(
    source: LumaDescriptionFile, *, identity_id: str, renderer: Any | None = None
) -> dict[str, Any]:
    """One reviewed record: the finished description and how it was reached."""

    from scripts.projection_build.event_description_bridge import (
        LINK_POLICY_VERSION,
        MARKDOWN_POLICY_VERSION,
    )
    from scripts.projection_build.event_speaker_bio_normalization import (
        NORMALIZATION_SCHEMA_VERSION,
    )

    result = render_and_normalize(source.markdown, renderer=renderer)
    return {
        "identity_id": identity_id,
        "slug": source.slug,
        "description_html": result["description_html"],
        "description_text": result["description_text"],
        "description_provenance": {
            "source": DESCRIPTION_SOURCE,
            "luma_event_id": source.luma_event_id,
            "luma_event_date": source.date,
            "source_description_sha256": source.source_sha256,
            "rendered_description_sha256": result["rendered_sha256"],
            "markdown_policy_version": MARKDOWN_POLICY_VERSION,
            "link_policy_version": LINK_POLICY_VERSION,
            "normalization_schema_version": NORMALIZATION_SCHEMA_VERSION,
            "removed_speaker_bio": result["removed_speaker_bio"],
            "removed_platform_boilerplate": result["removed_platform_boilerplate"],
            "normalized_internal_links": result["normalized_internal_links"],
        },
    }


def build_artifact(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The artifact, with the counts a reader checks it by."""

    ordered = sorted(records, key=lambda record: record["identity_id"])
    if len({record["identity_id"] for record in ordered}) != len(ordered):
        raise LumaDescriptionError("luma_description_identity_duplicated")
    artifact: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "source": DESCRIPTION_SOURCE,
        "counts": {
            "events": len(ordered),
            "speaker_bio_removed": sum(
                1 for r in ordered if r["description_provenance"]["removed_speaker_bio"]
            ),
            "platform_boilerplate_blocks": sum(
                r["description_provenance"]["removed_platform_boilerplate"] for r in ordered
            ),
            "internal_links_normalized": sum(
                r["description_provenance"]["normalized_internal_links"] for r in ordered
            ),
        },
        "events": ordered,
    }
    artifact["content_sha256"] = _digest(artifact)
    return artifact


def validate_artifact(artifact: Any) -> dict[str, Any]:
    """Check a loaded artifact against its own declared counts and digest."""

    if not isinstance(artifact, dict) or set(artifact) != {
        "schema_version",
        "source",
        "counts",
        "events",
        "content_sha256",
    }:
        raise LumaDescriptionError("luma_artifact_shape_invalid")
    if artifact["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise LumaDescriptionError("luma_artifact_schema_version_invalid")
    if artifact["source"] != DESCRIPTION_SOURCE:
        raise LumaDescriptionError("luma_artifact_source_invalid")
    events = artifact["events"]
    if not isinstance(events, list):
        raise LumaDescriptionError("luma_artifact_events_invalid")
    expected = _digest({k: v for k, v in artifact.items() if k != "content_sha256"})
    if artifact["content_sha256"] != expected:
        raise LumaDescriptionError("luma_artifact_digest_mismatch")
    counts = artifact["counts"]
    if not isinstance(counts, dict) or counts.get("events") != len(events):
        raise LumaDescriptionError("luma_artifact_count_mismatch")
    return artifact


def load_artifact(path: Path | None = None) -> dict[str, Any]:
    source = path or ARTIFACT_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LumaDescriptionError("luma_artifact_unreadable") from error
    return validate_artifact(payload)


def write_artifact(artifact: dict[str, Any], path: Path | None = None) -> Path:
    """Replace the artifact atomically, after checking what is about to land."""

    validate_artifact(artifact)
    target = path or ARTIFACT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
