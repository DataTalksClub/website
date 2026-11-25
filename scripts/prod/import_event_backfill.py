#!/usr/bin/env python3
"""Bring existing events up to a reviewed manifest: picture, fields, sponsors.

One-time.  Local event work (pictures rendered with banner-generator,
descriptions filled from the Luma and Eventbrite exports, speaker, date and
title edits, event-sponsor links) is staged outside the repository as a
manifest plus one picture file per event, e.g.
``~/prod/dtc-data/event-backfill-2026-11-25/``:

``manifest.json``::

    {"schema_version": 1,
     "drop_retired_registration_materials": true,
     "events": [
       {"event": {"content_id": "<uuid>", "repository": "...", "source_key": "...",
                  "public_id": 216, "title": "..."},
        "picture": {"file": "pictures/<uuid>.png", "sha256": "<hex>"},
        "fields": {"description": "...", "speakers": ["slug", ...],
                   "sponsors": ["dlthub"], "type": "webinar", "title": "...",
                   "start_datetime": "<iso>", "end_datetime": "<iso>",
                   "recording_url": "..."}}]}

Each entry names the event by its content UUID, else by its source identity
(``repository`` + ``source_key``); ``public_id`` and ``title`` are for the
report only.  An event resolved by neither is reported under ``unresolved``
and never guessed.  ``fields`` holds desired values; only those that differ
from the row are sent.  ``drop_retired_registration_materials`` removes, from
every event on the target (in the manifest or not), each material that links
to a Luma, Eventbrite or retired Academy-sessions signup page
(:func:`events.content_import.is_retired_registration_url`) -- people register
only on this site.

Every write goes through the supported services: the picture through
``events.admin_services.upload_event_banner`` (skipped when the stored bytes
already have the manifest's SHA-256), then the field edits through
``update_event`` (audited).  One event is one transaction, so on PostgreSQL the
deferred completeness triggers (``event_registrants`` 0005/0007) check the
event once, with its picture and its edits together.  An event that would still
miss a type, a speaker or a description is refused there; the report lists it
under ``failed`` with the gaps (``completeness_gaps``) and the run continues.

Dry run by default: every event's writes run inside a transaction that is
rolled back, so the report says exactly what ``--apply`` would change.
Re-running is safe: equal pictures and fields are left alone, and a second
``--apply`` reports every event unchanged.  The report names events by public
id and title only.

    uv run --frozen python scripts/prod/import_event_backfill.py \\
        --database .tmp/production-prep-dataset/dataset.sqlite3 \\
        --manifest ~/prod/dtc-data/event-backfill-2026-11-25/manifest.json

    uv run --frozen python scripts/prod/import_event_backfill.py \\
        --deployment-target website-production \\
        --allow-production-write website-production \\
        --manifest ~/prod/dtc-data/event-backfill-2026-11-25/manifest.json --apply
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False

#: The manifest field names this importer understands, in the order they are sent.
FIELDS = (
    "title",
    "description",
    "type",
    "start_datetime",
    "end_datetime",
    "recording_url",
    "speakers",
    "sponsors",
)


class ManifestError(ValueError):
    """The manifest cannot be read as written; nothing is changed."""


class _Rollback(Exception):
    """Raised inside a dry run's transaction so nothing it wrote is kept."""


@dataclass
class Report:
    applied: bool
    events_total: int = 0
    pictures_replaced: int = 0
    events_changed: dict[str, list[str]] = field(default_factory=dict)
    events_unchanged: int = 0
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"manifest_unreadable: {error}") from error
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ManifestError("manifest_schema_version_unsupported")
    events = data.get("events")
    if not isinstance(events, list):
        raise ManifestError("manifest_events_missing")
    for index, entry in enumerate(events):
        if not isinstance(entry, dict) or not isinstance(entry.get("event"), dict):
            raise ManifestError(f"manifest_entry_invalid: {index}")
        unknown = set(entry.get("fields") or {}) - set(FIELDS)
        if unknown:
            raise ManifestError(f"manifest_field_unknown: {index}: {sorted(unknown)}")
        picture = entry.get("picture")
        if picture is not None:
            target = (path.parent / str(picture.get("file") or "")).resolve()
            if path.parent.resolve() not in target.parents or not target.is_file():
                raise ManifestError(f"manifest_picture_missing: {index}")
    return data


def _resolve(identity: dict[str, Any]) -> Any:
    from community_base.events.models import Event

    from content.models import EventSource

    content_id = identity.get("content_id")
    if content_id:
        event = Event.objects.filter(content_id=content_id).first()
        if event is not None:
            return event
    repository, source_key = identity.get("repository"), identity.get("source_key")
    if repository and source_key:
        source = (
            EventSource.objects.filter(repository=repository, source_key=source_key)
            .select_related("event")
            .first()
        )
        if source is not None:
            return source.event
    return None


def _stored_picture_sha256(event: Any) -> str | None:
    from event_registrants.models import EventOperatorConfig

    config = EventOperatorConfig.objects.filter(event=event).first()
    if config is None or not config.banner_image:
        return None
    return hashlib.sha256(bytes(config.banner_image)).hexdigest()


def _same_instant(current: datetime | None, wanted: Any) -> bool:
    if wanted is None:
        return current is None
    return current is not None and current == datetime.fromisoformat(str(wanted))


def _changed_fields(event: Any, wanted: dict[str, Any]) -> dict[str, Any]:
    from community_base.events.models import EventHost

    from event_registrants.models import EventSponsor
    from events.identity import decode_event_tags

    current_type = decode_event_tags(event.tags)[0]
    speakers = [
        link.host.slug
        for link in EventHost.objects.filter(event=event)
        .select_related("host")
        .order_by("position")
    ]
    sponsors = list(
        EventSponsor.objects.filter(event=event)
        .order_by("position")
        .values_list("sponsor__key", flat=True)
    )
    current: dict[str, Any] = {
        "title": event.title,
        "description": event.description,
        "type": current_type,
        "recording_url": event.recording_url,
        "speakers": speakers,
        "sponsors": sponsors,
    }
    changed: dict[str, Any] = {}
    for name in FIELDS:
        if name not in wanted:
            continue
        value = wanted[name]
        if name in ("start_datetime", "end_datetime"):
            if not _same_instant(getattr(event, name), value):
                changed[name] = value
        elif current[name] != value:
            changed[name] = value
    return changed


def _kept_materials(event: Any) -> list[dict[str, Any]] | None:
    """The event's materials minus retired registration links, or None if none go."""

    from events.content_import import is_retired_registration_url

    materials = list(event.materials or [])
    kept = [
        item
        for item in materials
        if not (isinstance(item, dict) and is_retired_registration_url(str(item.get("url") or "")))
    ]
    return kept if len(kept) != len(materials) else None


def _apply_one(
    event: Any,
    entry: dict[str, Any] | None,
    *,
    root: Path,
    drop_materials: bool,
    context: Any,
) -> list[str]:
    from events.admin_services import update_event, upload_event_banner
    from events.pictures import sniff_picture_type

    changes: list[str] = []
    picture = (entry or {}).get("picture")
    if picture is not None and _stored_picture_sha256(event) != picture.get("sha256"):
        data = (root / picture["file"]).read_bytes()
        if picture.get("sha256") and hashlib.sha256(data).hexdigest() != picture["sha256"]:
            raise ManifestError(f"picture_checksum_mismatch: {picture['file']}")
        upload_event_banner(
            {
                "image_base64": base64.b64encode(data).decode("ascii"),
                "content_type": sniff_picture_type(data) or "image/png",
            },
            event_id=str(event.content_id),
        )
        changes.append("picture")
    payload = _changed_fields(event, dict((entry or {}).get("fields") or {}))
    if drop_materials:
        kept = _kept_materials(event)
        if kept is not None:
            payload["materials"] = kept
    if payload:
        update_event(payload, event_id=str(event.content_id), context=context)
        changes.extend(payload)
    return changes


def run(*, manifest: Path, apply: bool) -> Report:
    from community_base.events.models import Event
    from community_base.kernel.services import ServiceContext
    from django.db import DatabaseError, transaction

    from events.admin_services import EventAdminError
    from events.publication import completeness_gaps

    data = load_manifest(manifest)
    root = manifest.parent
    drop_materials = bool(data.get("drop_retired_registration_materials"))
    context = ServiceContext(
        correlation_id="import-event-backfill", actor_ref="system:import_event_backfill"
    )
    report = Report(applied=apply)

    by_pk: dict[int, dict[str, Any]] = {}
    for entry in data["events"]:
        event = _resolve(entry["event"])
        if event is None:
            report.unresolved.append(
                {
                    "public_id": entry["event"].get("public_id"),
                    "title": entry["event"].get("title", ""),
                }
            )
            continue
        by_pk[event.pk] = entry

    targets = Event.objects.order_by("pk")
    if not drop_materials:
        targets = targets.filter(pk__in=by_pk)
    for event in targets:
        entry = by_pk.get(event.pk)
        label = f"{event.public_id}: {event.title[:80]}"
        report.events_total += 1
        try:
            with transaction.atomic():
                changes = _apply_one(
                    event, entry, root=root, drop_materials=drop_materials, context=context
                )
                if not apply and changes:
                    raise _Rollback(changes)
        except _Rollback as rolled_back:
            changes = rolled_back.args[0]
        except (EventAdminError, ManifestError, DatabaseError, ValueError) as error:
            event.refresh_from_db()
            report.failed.append(
                {
                    "event": label,
                    "error": getattr(error, "code", "") or type(error).__name__,
                    "message": str(error)[:300],
                    "gaps": list(completeness_gaps(event)),
                }
            )
            continue
        if changes:
            report.events_changed[label] = changes
            if "picture" in changes:
                report.pictures_replaced += 1
        else:
            report.events_unchanged += 1
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_target_arguments(parser)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="the reviewed manifest.json; picture paths resolve next to it",
    )
    parser.add_argument(
        "--apply", action="store_true", help="write; without it the run only reports"
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="also write the JSON report here (under .tmp/)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    configure_target(parser, args)
    try:
        report = run(manifest=args.manifest.expanduser(), apply=args.apply)
    except ManifestError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 2
    rendered = json.dumps(report.as_dict(), indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if report.failed or report.unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
