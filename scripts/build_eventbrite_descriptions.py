#!/usr/bin/env python3
"""Clean scraped Eventbrite content and stage it for the description-precedence import.

Source -> staging, same three words ``_docs/runbooks/data-ingest.md`` defines
everywhere else. The two sources, both outside this repository and never
committed:

- ``~/prod/dtc-data/eventbrite-content/events/{id}.json`` -- real, scraped
  Eventbrite page content for 226 historical events (fields ``name``,
  ``summary``, ``description_html``, ``description_markdown``; see that
  directory's own ``README.md``).
- ``~/prod/dtc-data/eventbrite-event-identities.json`` -- the same numeric
  Eventbrite event ids resolved to a real canonical Event
  (``canonical_repository``/``canonical_revision``/``canonical_source_key``),
  via ``events.xlsx`` cross-referenced against the reviewed identity manifest.
  209 ids, 203 ``resolved``, 3 ``ambiguous``, 3 ``unresolved_missing_from_xlsx``
  -- only ``resolved`` entries are used here; see that file's own
  ``description`` field for the resolution method.

What one run does, per raw content file whose Eventbrite id resolves:

1. cleans ``description_markdown`` with
   :func:`events.eventbrite_content.clean_description_markdown` -- strips the
   "About the speaker/guest/host" bio section and the DataTalks.Club footer
   sentence, nothing else;
2. renders the cleaned markdown to HTML and plain text with that module's
   ``render_description_html``/``render_description_text``, the shapes
   ``EventContent.description_html``/``description_text`` already carry
   elsewhere in this codebase;
3. writes the cleaned record to two places:
   - back into the source data, as
     ``~/prod/dtc-data/eventbrite-content/cleaned/{id}.json`` (one file per
     event, so this does not need re-deriving next time);
   - into this repository's staging artifact,
     ``temporary/content/eventbrite_descriptions.json``, keyed by the
     canonical source triple that
     :func:`events.eventbrite_content.apply_eventbrite_descriptions` resolves
     against at import time.

An id with no ``resolved`` entry in the identities file, or with no raw content
file at all, is reported and skipped -- never guessed at.

**Reporting is the default and it writes nothing**, the same convention every
other staging builder in this repository uses. Re-run with ``--write`` once
the report looks right.

    uv run --frozen python scripts/build_eventbrite_descriptions.py \\
        --source-root ~/prod/dtc-data/eventbrite-content \\
        --identities ~/prod/dtc-data/eventbrite-event-identities.json

    uv run --frozen python scripts/build_eventbrite_descriptions.py \\
        --source-root ~/prod/dtc-data/eventbrite-content \\
        --identities ~/prod/dtc-data/eventbrite-event-identities.json \\
        --write
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from events.eventbrite_content import (  # noqa: E402
    DESCRIPTION_RECORD_SCHEMA_VERSION,
    clean_description_markdown,
    render_description_html,
    render_description_text,
)

STAGING_ARTIFACT_PATH = REPOSITORY_ROOT / "temporary" / "content" / "eventbrite_descriptions.json"
_EVENT_ID = re.compile(r"^[0-9]{1,20}$")


class EventbriteDescriptionBuildError(RuntimeError):
    """A refusal that carries a condition code, never source content."""


@dataclass(frozen=True, slots=True)
class RawEventbriteEvent:
    eventbrite_id: str
    name: str
    description_markdown: str
    description_html: str
    canonical_url: str
    path: Path


def _safe_json_file(path: Path) -> dict[str, Any]:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise EventbriteDescriptionBuildError("source_unavailable") from error
    metadata = resolved.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise EventbriteDescriptionBuildError("source_not_regular_file")
    try:
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EventbriteDescriptionBuildError("source_unreadable") from error


def discover_raw_events(source_root: Path) -> tuple[RawEventbriteEvent, ...]:
    """Read every ``events/{id}.json`` file under the raw Eventbrite content root."""

    events_dir = source_root / "events"
    try:
        resolved_dir = events_dir.resolve(strict=True)
    except OSError as error:
        raise EventbriteDescriptionBuildError("source_directory_unavailable") from error
    if not resolved_dir.is_dir():
        raise EventbriteDescriptionBuildError("source_not_directory")

    events: list[RawEventbriteEvent] = []
    for entry in sorted(resolved_dir.iterdir(), key=lambda item: item.name):
        if entry.suffix != ".json" or not _EVENT_ID.fullmatch(entry.stem):
            continue
        payload = _safe_json_file(entry)
        eventbrite_id = payload.get("eventbrite_id")
        if eventbrite_id != entry.stem:
            raise EventbriteDescriptionBuildError("source_event_id_mismatch")
        events.append(
            RawEventbriteEvent(
                eventbrite_id=eventbrite_id,
                name=payload.get("name") or "",
                description_markdown=payload.get("description_markdown") or "",
                description_html=payload.get("description_html") or "",
                canonical_url=payload.get("canonical_url") or "",
                path=entry,
            )
        )
    return tuple(events)


def load_resolved_identities(identities_path: Path) -> dict[str, dict[str, str]]:
    """Only the ``resolved`` entries, keyed by Eventbrite event id."""

    payload = _safe_json_file(identities_path)
    events = payload.get("events")
    if not isinstance(events, list):
        raise EventbriteDescriptionBuildError("identities_payload_invalid")
    resolved: dict[str, dict[str, str]] = {}
    for item in events:
        if not isinstance(item, dict) or item.get("status") != "resolved":
            continue
        eventbrite_id = item.get("eventbrite_event_id")
        if not isinstance(eventbrite_id, str) or not eventbrite_id:
            raise EventbriteDescriptionBuildError("identities_event_id_invalid")
        resolved[eventbrite_id] = {
            "canonical_repository": item["canonical_repository"],
            "canonical_revision": item["canonical_revision"],
            "canonical_source_key": item["canonical_source_key"],
        }
    return resolved


def build(*, source_root: Path, identities_path: Path) -> dict[str, Any]:
    raw_events = discover_raw_events(source_root)
    resolved_identities = load_resolved_identities(identities_path)

    staged_events: list[dict[str, Any]] = []
    cleaned_files: dict[str, dict[str, Any]] = {}
    no_resolved_identity: list[str] = []
    empty_after_clean = 0

    for raw in raw_events:
        identity = resolved_identities.get(raw.eventbrite_id)
        if identity is None:
            no_resolved_identity.append(raw.eventbrite_id)
            continue
        cleaned_markdown = clean_description_markdown(raw.description_markdown)
        description_html = render_description_html(cleaned_markdown)
        description_text = render_description_text(cleaned_markdown)
        if not description_text:
            empty_after_clean += 1

        staged_events.append(
            {
                "eventbrite_event_id": raw.eventbrite_id,
                "canonical_repository": identity["canonical_repository"],
                "canonical_revision": identity["canonical_revision"],
                "canonical_source_key": identity["canonical_source_key"],
                "description_html": description_html,
                "description_text": description_text,
            }
        )
        cleaned_files[raw.eventbrite_id] = {
            "eventbrite_id": raw.eventbrite_id,
            "name": raw.name,
            "canonical_url": raw.canonical_url,
            "canonical_source_key": identity["canonical_source_key"],
            "description_markdown_cleaned": cleaned_markdown,
            "description_html_cleaned": description_html,
            "description_text_cleaned": description_text,
        }

    staging_artifact = {
        "schema_version": DESCRIPTION_RECORD_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "raw_content_root": str(source_root),
            "identities_path": str(identities_path),
        },
        "events": staged_events,
    }

    return {
        "raw_events_read": len(raw_events),
        "resolved_identities_available": len(resolved_identities),
        "staged": len(staged_events),
        "empty_after_clean": empty_after_clean,
        "no_resolved_identity_total": len(no_resolved_identity),
        "no_resolved_identity": no_resolved_identity,
        "staging_artifact": staging_artifact,
        "cleaned_files": cleaned_files,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--source-root",
        required=True,
        type=Path,
        help="e.g. ~/prod/dtc-data/eventbrite-content (must contain events/{id}.json)",
    )
    parser.add_argument(
        "--identities",
        required=True,
        type=Path,
        help="e.g. ~/prod/dtc-data/eventbrite-event-identities.json",
    )
    parser.add_argument(
        "--cleaned-output-dir",
        type=Path,
        default=None,
        help="Defaults to <source-root>/cleaned/",
    )
    parser.add_argument("--staging-output", type=Path, default=STAGING_ARTIFACT_PATH)
    parser.add_argument("--write", action="store_true", help="Write both output artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source_root = args.source_root.expanduser()
    identities_path = args.identities.expanduser()
    cleaned_output_dir = (args.cleaned_output_dir or (source_root / "cleaned")).expanduser()

    try:
        result = build(source_root=source_root, identities_path=identities_path)
    except EventbriteDescriptionBuildError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    report = {
        "raw_events_read": result["raw_events_read"],
        "resolved_identities_available": result["resolved_identities_available"],
        "staged": result["staged"],
        "empty_after_clean": result["empty_after_clean"],
        "no_resolved_identity_total": result["no_resolved_identity_total"],
        "no_resolved_identity": result["no_resolved_identity"],
        "staging_output": str(args.staging_output),
        "cleaned_output_dir": str(cleaned_output_dir),
        "applied": args.write,
    }

    if args.write:
        args.staging_output.parent.mkdir(parents=True, exist_ok=True)
        args.staging_output.write_text(
            json.dumps(result["staging_artifact"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        cleaned_output_dir.mkdir(parents=True, exist_ok=True)
        for eventbrite_id, payload in result["cleaned_files"].items():
            (cleaned_output_dir / f"{eventbrite_id}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        report["cleaned_files_written"] = len(result["cleaned_files"])

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
