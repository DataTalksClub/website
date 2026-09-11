"""Parse and apply the reviewed Event identity manifest.

Moved out of ``events/`` (formerly ``events.identity``): this is one-time,
production-ingest domain logic -- ``scripts/prod/import_events.py`` and test
fixture seeding (``test_support/reference_data.py``) are its only callers, no
live view, serializer, or API touches it -- so it lives under ``scripts/prod``,
the same way ``scripts/prod/legacy_zoomcamp/identity.py`` is that ingestion's
own identity domain logic rather than living inside an app package.
``events/models.py`` keeps ``create_event_identity`` -- the shared,
allocator-safe primitive this module calls to apply the manifest -- and
everything a live route, Studio, or the admin API resolves an Event by.

The manifest is intentionally source-controlled and request-network-free.
Date/title/provider values are never used to infer an Event identity; source
identity and the reviewed UUID are the only attachment keys.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.db import transaction

from events.models import (
    Event,
    EventIdentityError,
    ensure_public_id_sequence,
    insert_event_with_public_id,
)
from events.slugs import event_title_slug

#: Bumped from 1 (schema history was collapsed) to 3 to 4. Version 2 required every
#: event to carry exactly two ``legacy_uuid`` and two ``legacy_date_path`` aliases;
#: version 3 dropped that requirement but kept the (by then always-empty) ``aliases``
#: field. ``EventAlias`` -- model, table, and every alias kind -- is retired outright
#: (an Event is addressed only by its id, and no other path is retained for
#: provenance either), so version 4 drops ``aliases`` from the manifest schema itself:
#: an event entry no longer has that key at all, and ``counts`` no longer reports it.
IDENTITY_MANIFEST_SCHEMA_VERSION = 4
_SOURCE_KEY = re.compile(r"^[^\x00]{1,512}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    repository: str
    revision: str
    source_key: str


@dataclass(frozen=True, slots=True)
class ManifestEvent:
    id: uuid.UUID
    public_id: int
    title: str
    slug: str
    canonical_path: str
    source: SourceIdentity
    source_path: str
    source_checksum: str


@dataclass(frozen=True, slots=True)
class IdentityManifest:
    schema_version: int
    events: tuple[ManifestEvent, ...]


@dataclass(frozen=True, slots=True)
class IdentityImportReport:
    event_total: int
    events_created: int
    events_updated: int
    replayed: bool
    dry_run: bool


def _required_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise EventIdentityError(f"manifest_{field}_invalid")
    return value


def _source(value: Any) -> SourceIdentity:
    if not isinstance(value, dict) or set(value) - {"repository", "revision", "source_key"}:
        raise EventIdentityError("manifest_source_shape_invalid")
    repository = _required_text(value.get("repository"), field="repository", maximum=255)
    revision = _required_text(value.get("revision"), field="revision", maximum=64)
    source_key = _required_text(value.get("source_key"), field="source_key", maximum=512)
    if _REPOSITORY.fullmatch(repository) is None or _REVISION.fullmatch(revision) is None:
        raise EventIdentityError("manifest_source_identity_invalid")
    if _SOURCE_KEY.fullmatch(source_key) is None:
        raise EventIdentityError("manifest_source_key_invalid")
    return SourceIdentity(repository, revision, source_key)


def _parse_uuid(value: Any) -> uuid.UUID:
    if not isinstance(value, str):
        raise EventIdentityError("manifest_uuid_invalid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise EventIdentityError("manifest_uuid_invalid") from exc
    if str(parsed) != value or parsed.variant != uuid.RFC_4122:
        raise EventIdentityError("manifest_uuid_invalid")
    return parsed


def parse_identity_manifest(payload: Any) -> IdentityManifest:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "source",
        "counts",
        "events",
    }:
        raise EventIdentityError("manifest_shape_invalid")
    if payload["schema_version"] != IDENTITY_MANIFEST_SCHEMA_VERSION:
        raise EventIdentityError("manifest_schema_version_invalid")
    source = _source(payload["source"])
    counts = payload["counts"]
    entries = payload["events"]
    if not isinstance(counts, dict) or set(counts) != {"events"}:
        raise EventIdentityError("manifest_counts_invalid")
    if not isinstance(entries, list) or counts["events"] != len(entries):
        raise EventIdentityError("manifest_event_count_invalid")
    if counts["events"] < 1:
        raise EventIdentityError("manifest_event_count_invalid")

    parsed: list[ManifestEvent] = []
    ids: set[uuid.UUID] = set()
    sources: set[SourceIdentity] = set()
    canonical_paths: set[str] = set()
    public_ids: set[int] = set()
    for raw in entries:
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "public_id",
            "title",
            "slug",
            "canonical_path",
            "source",
            "source_path",
            "source_checksum",
        }:
            raise EventIdentityError("manifest_event_shape_invalid")
        event_id = _parse_uuid(raw["id"])
        public_id = raw["public_id"]
        if isinstance(public_id, bool) or not isinstance(public_id, int) or public_id < 1:
            raise EventIdentityError("manifest_public_id_invalid")
        title = _required_text(raw["title"], field="title", maximum=1_000).strip()
        try:
            slug = event_title_slug(title)
        except ValueError as exc:
            raise EventIdentityError("manifest_title_invalid") from exc
        provided_slug = _required_text(raw["slug"], field="slug", maximum=255)
        if provided_slug != slug:
            raise EventIdentityError("manifest_slug_not_title_derived")
        provided_source = _source(raw["source"])
        if provided_source != source and (
            provided_source.repository != source.repository
            or provided_source.revision != source.revision
        ):
            raise EventIdentityError("manifest_source_revision_mismatch")
        source_path = _required_text(raw["source_path"], field="source_path", maximum=512)
        checksum = raw["source_checksum"]
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise EventIdentityError("manifest_source_checksum_invalid")
        canonical_path = _required_text(
            raw["canonical_path"], field="canonical_path", maximum=1_024
        )
        expected_path = f"/events/{public_id}/{slug}"
        if canonical_path != expected_path or canonical_path in canonical_paths:
            raise EventIdentityError("manifest_canonical_path_invalid")
        if event_id in ids or public_id in public_ids or provided_source in sources:
            raise EventIdentityError("manifest_identity_duplicate")
        ids.add(event_id)
        public_ids.add(public_id)
        sources.add(provided_source)
        canonical_paths.add(canonical_path)
        parsed.append(
            ManifestEvent(
                id=event_id,
                public_id=public_id,
                title=title,
                slug=slug,
                canonical_path=canonical_path,
                source=provided_source,
                source_path=source_path,
                source_checksum=checksum,
            )
        )
    return IdentityManifest(IDENTITY_MANIFEST_SCHEMA_VERSION, tuple(parsed))


def load_identity_manifest(path: Path) -> IdentityManifest:
    """Parse the reviewed manifest at ``path``.

    The caller supplies the location. Where the reviewed manifest sits is a fact
    about the one-time ingest (and about test fixtures), not about the event
    domain, which resolves public identity from ``Event`` rows.
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EventIdentityError("manifest_unreadable") from exc
    return parse_identity_manifest(payload)


@transaction.atomic
def import_identity_manifest(*, path: Path, dry_run: bool = False) -> IdentityImportReport:
    """Validate and atomically apply the reviewed manifest; replay is an idempotent no-op."""

    manifest = load_identity_manifest(path)
    existing = {
        event.id: event
        for event in Event.objects.filter(id__in=[item.id for item in manifest.events])
    }
    existing_by_source = {
        (event.source_repository, event.source_revision, event.source_key): event
        for event in Event.objects.all()
    }
    existing_by_public_id = {
        event.public_id: event for event in Event.objects.exclude(public_id=None)
    }
    created = updated = 0
    # Preflight the complete candidate before changing one row.  This keeps a missing,
    # duplicated, renumbered, or retargeted mapping from partially activating.
    for item in manifest.events:
        source_key = (item.source.repository, item.source.revision, item.source.source_key)
        by_source = existing_by_source.get(source_key)
        by_id = existing.get(item.id)
        if by_source is not None and by_source.id != item.id:
            raise EventIdentityError("source_identity_uuid_conflict")
        if by_id is not None and (
            by_id.source_repository != item.source.repository
            or by_id.source_revision != item.source.revision
            or by_id.source_key != item.source.source_key
        ):
            raise EventIdentityError("uuid_source_identity_conflict")
        by_public_id = existing_by_public_id.get(item.public_id)
        if by_public_id is not None and by_public_id.id != item.id:
            raise EventIdentityError("public_id_uuid_conflict")
        if by_id is not None and by_id.public_id != item.public_id:
            raise EventIdentityError("public_id_renumber_forbidden")
        if by_id is None:
            created += 1
        elif (
            by_id.title != item.title
            or by_id.slug != item.slug
            or by_id.source_path != item.source_path
            or by_id.source_checksum != item.source_checksum
        ):
            updated += 1
        if not dry_run:
            if by_id is None:
                event = insert_event_with_public_id(
                    id=item.id,
                    public_id=item.public_id,
                    title=item.title,
                    source_repository=item.source.repository,
                    source_revision=item.source.revision,
                    source_key=item.source.source_key,
                    source_path=item.source_path,
                    source_checksum=item.source_checksum,
                )
            else:
                event = by_id
                event.title = item.title
                event.slug = item.slug
                event.source_path = item.source_path
                event.source_checksum = item.source_checksum
                event.save()
            # Replayed imports also repair Events created before Q&A existed;
            # the same idempotent service is used by the bounded backfill.
            from events.qna.services import ensure_event_qna

            ensure_event_qna(event.id)
    if dry_run:
        return IdentityImportReport(
            len(manifest.events),
            created,
            updated,
            created == 0 and updated == 0,
            True,
        )
    # An import writes public IDs the allocator did not hand out, so it owes the
    # allocator the new high-water mark before the next service-created event.
    ensure_public_id_sequence()
    return IdentityImportReport(
        len(manifest.events),
        created,
        updated,
        created == 0 and updated == 0,
        False,
    )
