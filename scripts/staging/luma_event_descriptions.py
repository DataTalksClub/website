"""Turn a Luma description export into reviewed content records for events we hold.

The 421-record legacy corpus in ``temporary/content/public_projection/events.json``
is a frozen one-time export: its descriptions come from the event description
bridge, which matches entries on the legacy ``_data/events.yaml`` tuple. An event
discovered in a Luma export has no such tuple -- ``events.identity`` gives it a
title and a canonical path and nothing else -- so the bridge structurally cannot
carry its description, and rebuilding the corpus blanks any event it has no entry
for.

So those events get their own staging artifact rather than a bigger bridge. It is
keyed on identity id, nothing pinned to the legacy corpus moves when it grows, and
``events.content_import.import_new_event_content`` is the only thing that reads it.

What the artifact holds is the *finished* content record: the description rendered
through the same Markdown and link policies the bridge uses and then put through the
same ``normalize_description_html`` that removed the "about the speaker" block from
the legacy corpus, beside the ``type`` and ``starts_at`` an ``EventContent`` row
cannot exist without. The removal decision is recorded per record rather than in a
separate replay plan, because there is no rebuild step to replay it against -- this
artifact is itself the reviewed result.

Where ``type`` and ``starts_at`` come from
------------------------------------------

An ``EventContent`` row requires both, and a description export carries neither: its
filename has a date with no time, and nothing anywhere in a Luma export says whether
an event is a webinar, a workshop, a podcast or a conference. Neither is guessed.

*``starts_at`` is read from the export*, where it genuinely is: every description
``.md`` is paired by filename with a ``_json`` checkpoint whose ``event.start_at`` is
the instant Luma scheduled. ``ends_at`` is deliberately **not** taken from the same
checkpoint -- Luma computes ``end_at`` from a nominal ``duration_interval``, so
storing it would publish a guessed duration as a stated end, which
``events.models.EventContent`` says never to do.

*``type`` is read from a reviewed input file a person maintains*, the same shape and
the same posture as ``_docs/migration-data/local-current-registration-input.json``:
what it does not name does not land. An export with no reviewed type is reported
under ``no_reviewed_type`` and skipped, run after run, until somebody decides.

Three things fail closed, all deliberately
------------------------------------------

*A link with no reviewed decision.* The link policy pins the exact destinations that
may appear in rendered HTML, and host approval alone is not enough. An event naming a
destination nobody has reviewed is stopped and its URLs are reported, because
approving one is an edit to
``scripts/projection_build/event_description_link_policy.py`` by a person; it is
never inferred here.

*An event we do not already have.* This writes descriptions for identities that
exist. Creating an event is ``events.identity``'s job, reached through
``scripts/prod/import_events.py --discover-new-events-only``. An export whose event
has no identity yet is reported under ``no_identity_yet``.

*An event with no reviewed type.* See above.

Guest data is never decoded
---------------------------

A ``_json`` checkpoint carries the registration list beside the event fields. This
reads it through the description bridge's own span reader, which locates
``event.id``, ``event.name`` and ``event.start_at`` by position and decodes only
those three -- the ``guests`` array is never parsed, never held and never reported.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_PATH = REPOSITORY_ROOT / "temporary" / "content" / "luma_event_descriptions.json"
#: A description export root holds ``descriptions/*.md`` beside ``_json/*.json``,
#: one pair per event, named alike. Both are read; only the pair is trusted.
DEFAULT_DESCRIPTION_ROOT = Path(".local") / "migration-data" / "events" / "luma"
REVIEWED_TYPE_INPUT_PATH = (
    REPOSITORY_ROOT / "_docs" / "migration-data" / "local-event-type-input.json"
)

ARTIFACT_SCHEMA_VERSION = 1
RECORD_SCHEMA_VERSION = 1
DESCRIPTION_SOURCE = "luma-export-v1"
PROVIDER = "luma"

#: ``2026-08-10_test-containerize-and-deploy-an-ai-assisted-app_evt-ha8kjrvmcxqmmue``
_FILE_NAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<slug>[a-z0-9-]+)_(?P<event>evt-[a-z0-9]+)$"
)
_MAXIMUM_MARKDOWN_BYTES = 256 * 1024
_MAXIMUM_CHECKPOINT_BYTES = 32 * 1024 * 1024
_MAXIMUM_TYPE_INPUT_BYTES = 1024 * 1024
_EVENT_IDENTIFIER = re.compile(r"^evt-[A-Za-z0-9]{1,64}$")
#: The four ``events.models.EventContent.Type`` values, spelled out rather than
#: imported: this module must stay usable without a configured Django.
_TYPES = frozenset({"webinar", "workshop", "podcast", "conference"})
_TYPE_INPUT_FIELDS = frozenset({"description_file", "type", "reason"})


class LumaDescriptionError(RuntimeError):
    """A bounded refusal that carries a condition code, never a source value."""


@dataclass(frozen=True, slots=True)
class DescriptionExport:
    """One export pair, parsed. A transient row, not a record of anything.

    It exists between reading the export and building the content record and is
    then discarded: what lands in the artifact is an ``EventContent`` candidate
    keyed on our own identity, and this stops being an export event there.
    """

    stem: str
    path: Path
    date: str
    slug: str
    #: The provider's own event id, in the case the checkpoint spells it. This is
    #: the ``source_key`` the identity was minted under, so it is the only thing
    #: this pipeline resolves an event by -- never the title, never the slug.
    external_event_identifier: str
    title: str
    starts_at: str
    markdown: str

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReviewedEventType:
    """One person's decision about what kind of event one export describes."""

    description_file: str
    type: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReviewedEventTypes:
    """The reviewed input as a whole: its revision, and what it decided."""

    review_revision: int
    entries: dict[str, ReviewedEventType]


@dataclass(frozen=True, slots=True)
class UnreviewedLink:
    """A destination the link policy has no decision for, named so a person can look."""

    url: str
    reason: str


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _event_facts(path: Path) -> tuple[str, str, str]:
    """Read ``event.id``, ``event.name`` and ``event.start_at`` and nothing else.

    The checkpoint carries the registration list. The bridge's span reader walks
    the JSON structurally and decodes only the spans asked for, so the ``guests``
    array is never turned into Python values at all -- which is the point of
    borrowing it rather than calling ``json.loads`` here.
    """

    from scripts.build_event_description_bridge import (
        BridgeBuildError,
        _decode_selected_json_value,
        _json_object_spans,
        _read_bounded,
    )

    try:
        raw = _read_bounded(
            path, maximum=_MAXIMUM_CHECKPOINT_BYTES, error="checkpoint boundary mismatch"
        )
        text = raw.decode("utf-8")
        spans = _json_object_spans(text)
        if _decode_selected_json_value(text, spans["schema_version"]) != 1:
            raise LumaDescriptionError("luma_checkpoint_schema_version_invalid")
        event_span = spans["event"]
        event_text = text[event_span[0] : event_span[1]]
        event_spans = _json_object_spans(event_text)
        facts = {
            field: _decode_selected_json_value(event_text, event_spans[field])
            for field in ("id", "name", "start_at")
        }
    except (BridgeBuildError, UnicodeError) as error:
        raise LumaDescriptionError("luma_checkpoint_unreadable") from error
    except KeyError as error:
        raise LumaDescriptionError("luma_checkpoint_field_missing") from error

    identifier = facts["id"]
    if not isinstance(identifier, str) or _EVENT_IDENTIFIER.fullmatch(identifier) is None:
        raise LumaDescriptionError("luma_checkpoint_event_identifier_invalid")
    title = facts["name"]
    if not isinstance(title, str) or not title.strip() or len(title) > 1_000:
        raise LumaDescriptionError("luma_checkpoint_title_invalid")
    return identifier, title.strip(), _instant(facts["start_at"])


def _instant(value: Any) -> str:
    """The export's start, as an aware UTC instant, or a refusal. Never a default."""

    if not isinstance(value, str) or not value or len(value) > 64:
        raise LumaDescriptionError("luma_checkpoint_start_missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise LumaDescriptionError("luma_checkpoint_start_invalid") from error
    # A naive instant would be read back in whatever zone the server happens to
    # be in, which silently moves an event by hours.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LumaDescriptionError("luma_checkpoint_start_not_aware")
    return parsed.astimezone(UTC).isoformat()


def discover_description_exports(root: Path) -> tuple[DescriptionExport, ...]:
    """Read every description/checkpoint pair under ``root``, in file-name order."""

    descriptions = root / "descriptions"
    checkpoints = root / "_json"
    if not descriptions.is_dir() or not checkpoints.is_dir():
        raise LumaDescriptionError("luma_description_root_unavailable")

    found: list[DescriptionExport] = []
    for path in sorted(descriptions.glob("*.md")):
        matched = _FILE_NAME.fullmatch(path.stem)
        if matched is None:
            raise LumaDescriptionError("luma_description_name_unrecognised")
        checkpoint = checkpoints / f"{path.stem}.json"
        if not checkpoint.is_file():
            raise LumaDescriptionError("luma_description_checkpoint_missing")
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
        identifier, title, starts_at = _event_facts(checkpoint)
        # The file name spells the provider event id in lower case; the checkpoint
        # spells it as the provider does. Requiring them to agree case-blind is
        # what makes the pair one event rather than two files that sort alike.
        if identifier.casefold() != matched["event"]:
            raise LumaDescriptionError("luma_description_pair_mismatch")
        found.append(
            DescriptionExport(
                stem=path.stem,
                path=path,
                date=matched["date"],
                slug=matched["slug"],
                external_event_identifier=identifier,
                title=title,
                starts_at=starts_at,
                markdown=markdown,
            )
        )
    if not found:
        raise LumaDescriptionError("luma_description_root_empty")
    if len({item.external_event_identifier for item in found}) != len(found):
        raise LumaDescriptionError("luma_description_event_duplicated")
    return tuple(found)


def load_reviewed_event_types(path: Path) -> ReviewedEventTypes:
    """Read the reviewed type decisions, keyed by the description file they name.

    Nothing derives a type. This file is the only place one can come from, an
    export it does not name is skipped rather than typed, and an entry naming a
    file the export does not hold is a refusal rather than a silent no-op.
    """

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise LumaDescriptionError("luma_type_input_unreadable") from error
    if len(raw) > _MAXIMUM_TYPE_INPUT_BYTES:
        raise LumaDescriptionError("luma_type_input_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LumaDescriptionError("luma_type_input_invalid") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "review_revision",
        "events",
    }:
        raise LumaDescriptionError("luma_type_input_shape_invalid")
    if payload["schema_version"] != 1:
        raise LumaDescriptionError("luma_type_input_schema_version_invalid")
    revision = payload["review_revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise LumaDescriptionError("luma_type_input_revision_invalid")
    entries = payload["events"]
    if not isinstance(entries, list):
        raise LumaDescriptionError("luma_type_input_events_invalid")

    reviewed: dict[str, ReviewedEventType] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _TYPE_INPUT_FIELDS:
            raise LumaDescriptionError("luma_type_input_entry_shape_invalid")
        name = entry["description_file"]
        if (
            not isinstance(name, str)
            or not name.endswith(".md")
            or _FILE_NAME.fullmatch(name[: -len(".md")]) is None
        ):
            raise LumaDescriptionError("luma_type_input_description_file_invalid")
        if entry["type"] not in _TYPES:
            raise LumaDescriptionError("luma_type_input_type_invalid")
        reason = entry["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1_000:
            raise LumaDescriptionError("luma_type_input_reason_invalid")
        if name in reviewed:
            raise LumaDescriptionError("luma_type_input_description_file_duplicated")
        reviewed[name] = ReviewedEventType(
            description_file=name, type=entry["type"], reason=reason.strip()
        )
    return ReviewedEventTypes(review_revision=revision, entries=reviewed)


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


def _link_destinations(markdown: str) -> list[str]:
    """Every link and image destination the Markdown names, in document order."""

    from scripts.build_event_description_bridge import MARKDOWN

    try:
        tokens = MARKDOWN(markdown)
    except Exception as error:  # mistune raises bare exceptions on malformed input
        raise LumaDescriptionError("luma_description_markdown_unparsable") from error
    if not isinstance(tokens, list):
        raise LumaDescriptionError("luma_description_markdown_unparsable")

    found: list[str] = []
    stack: list[Any] = list(reversed(tokens))
    while stack:
        token = stack.pop()
        if not isinstance(token, dict):
            continue
        if token.get("type") in {"link", "image"}:
            destination = token.get("attrs", {}).get("url")
            if not isinstance(destination, str):
                raise LumaDescriptionError("luma_description_link_shape_invalid")
            found.append(destination)
        children = token.get("children")
        if isinstance(children, list):
            stack.extend(reversed(children))
    return found


def unreviewed_link_destinations(
    markdown: str, *, renderer: Any | None = None
) -> tuple[UnreviewedLink, ...]:
    """The destinations in one description that no person has decided about.

    The renderer refuses the whole description when it meets one of these, but its
    failure is content-free by design, so it cannot say *which* URL. This walks the
    same destinations through the same policy first, so the report can name them --
    which is the only thing that makes the gate actionable. Approving one stays an
    edit to the link policy by a person.
    """

    from scripts.build_event_description_bridge import BridgeBuildError

    active = renderer if renderer is not None else _renderer()
    unreviewed: list[UnreviewedLink] = []
    seen: set[str] = set()
    for destination in _link_destinations(markdown):
        try:
            active.classify(destination)
        except BridgeBuildError as error:
            if destination in seen:
                continue
            seen.add(destination)
            unreviewed.append(UnreviewedLink(url=destination, reason=str(error)))
    return tuple(unreviewed)


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
    export: DescriptionExport,
    *,
    identity_id: str,
    source_repository: str,
    source_revision: str,
    reviewed_type: ReviewedEventType,
    review_revision: int,
    renderer: Any | None = None,
) -> dict[str, Any]:
    """One content record: the finished description, its schedule, and how both were reached."""

    from scripts.projection_build.event_description_bridge import (
        LINK_POLICY_VERSION,
        MARKDOWN_POLICY_VERSION,
    )
    from scripts.projection_build.event_speaker_bio_normalization import (
        NORMALIZATION_SCHEMA_VERSION,
    )

    result = render_and_normalize(export.markdown, renderer=renderer)
    return {
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "identity_id": identity_id,
        "type": reviewed_type.type,
        "starts_at": export.starts_at,
        # Luma derives end_at from a nominal duration, so it is not a stated end
        # and never becomes one here.
        "ends_at": "",
        "season": None,
        "episode": None,
        "description_html": result["description_html"],
        "description_text": result["description_text"],
        "description_provenance": {
            "source": DESCRIPTION_SOURCE,
            "export_file": f"{export.stem}.md",
            "event_date": export.date,
            "source_description_sha256": export.source_sha256,
            "rendered_description_sha256": result["rendered_sha256"],
            "markdown_policy_version": MARKDOWN_POLICY_VERSION,
            "link_policy_version": LINK_POLICY_VERSION,
            "normalization_schema_version": NORMALIZATION_SCHEMA_VERSION,
            "removed_speaker_bio": result["removed_speaker_bio"],
            "removed_platform_boilerplate": result["removed_platform_boilerplate"],
            "normalized_internal_links": result["normalized_internal_links"],
        },
        "type_provenance": {
            "input": REVIEWED_TYPE_INPUT_PATH.name,
            "review_revision": review_revision,
            "reason": reviewed_type.reason,
        },
        # Nothing in a description export is a reviewed speaker list or a reviewed
        # event link: the bio block is removed, and the links inside the
        # description stay inside it. Empty is the honest value, not a gap.
        "speakers": [],
        "links": [],
        "provenance": {
            "repository": source_repository,
            "revision": source_revision,
            "source_key": export.external_event_identifier,
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
