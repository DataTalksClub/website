"""Event identity manifest, import seam, and canonical URL builders.

The manifest is intentionally source-controlled and request-network-free.  Date/title/provider
values are never used to infer an Event identity; source identity and the reviewed UUID are the
only attachment keys.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction

from .models import Event, EventAlias
from .slugs import event_title_slug

IDENTITY_MANIFEST_PATH = Path(__file__).with_name("event_identity_manifest.json")
IDENTITY_MANIFEST_SCHEMA_VERSION = 1
_SOURCE_KEY = re.compile(r"^[^\x00]{1,512}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")


class EventIdentityError(ValueError):
    """A bounded manifest or exact-identity failure."""


class EventIdentityNotFound(LookupError):
    """An unknown UUID/source identity/legacy alias was requested."""


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    repository: str
    revision: str
    source_key: str


@dataclass(frozen=True, slots=True)
class ManifestAlias:
    source_path: str
    kind: str
    reason: str
    source: SourceIdentity


@dataclass(frozen=True, slots=True)
class ManifestEvent:
    id: uuid.UUID
    title: str
    slug: str
    path: str
    source: SourceIdentity
    source_path: str
    source_checksum: str
    aliases: tuple[ManifestAlias, ...]


@dataclass(frozen=True, slots=True)
class IdentityManifest:
    schema_version: int
    events: tuple[ManifestEvent, ...]

    @property
    def aliases(self) -> tuple[ManifestAlias, ...]:
        return tuple(alias for event in self.events for alias in event.aliases)


@dataclass(frozen=True, slots=True)
class IdentityImportReport:
    event_total: int
    alias_total: int
    events_created: int
    events_updated: int
    aliases_created: int
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


def _parse_alias(value: Any, *, event_source: SourceIdentity) -> ManifestAlias:
    if not isinstance(value, dict) or set(value) != {"source_path", "kind", "reason"}:
        raise EventIdentityError("manifest_alias_shape_invalid")
    source_path = _required_text(value.get("source_path"), field="alias_path", maximum=1_024)
    kind = _required_text(value.get("kind"), field="alias_kind", maximum=24)
    reason = _required_text(value.get("reason"), field="alias_reason", maximum=255)
    if kind not in {
        EventAlias.Kind.LEGACY_PATH,
        EventAlias.Kind.TITLE_SLUG,
        EventAlias.Kind.REVIEWED,
    }:
        raise EventIdentityError("manifest_alias_kind_invalid")
    split = urlsplit(source_path)
    if (
        not source_path.startswith("/events/")
        or source_path == "/events/"
        or split.path != source_path
        or split.query
        or split.fragment
    ):
        raise EventIdentityError("manifest_alias_path_invalid")
    return ManifestAlias(source_path, kind, reason, event_source)


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
    if not isinstance(counts, dict) or set(counts) != {"events", "aliases"}:
        raise EventIdentityError("manifest_counts_invalid")
    if not isinstance(entries, list) or counts["events"] != len(entries):
        raise EventIdentityError("manifest_event_count_invalid")
    if counts["events"] < 1 or not isinstance(counts["aliases"], int):
        raise EventIdentityError("manifest_event_count_invalid")

    parsed: list[ManifestEvent] = []
    ids: set[uuid.UUID] = set()
    sources: set[SourceIdentity] = set()
    aliases: set[str] = set()
    canonical_paths: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "title",
            "slug",
            "path",
            "source",
            "source_path",
            "source_checksum",
            "aliases",
        }:
            raise EventIdentityError("manifest_event_shape_invalid")
        event_id = _parse_uuid(raw["id"])
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
        path = _required_text(raw["path"], field="path", maximum=1_024)
        expected_path = f"/events/{event_id}/{slug}"
        if path != expected_path or path in canonical_paths:
            raise EventIdentityError("manifest_canonical_path_invalid")
        raw_aliases = raw["aliases"]
        if not isinstance(raw_aliases, list):
            raise EventIdentityError("manifest_aliases_invalid")
        event_aliases = tuple(
            _parse_alias(alias, event_source=provided_source) for alias in raw_aliases
        )
        if event_id in ids or provided_source in sources:
            raise EventIdentityError("manifest_identity_duplicate")
        ids.add(event_id)
        sources.add(provided_source)
        canonical_paths.add(path)
        for alias in event_aliases:
            if alias.source_path in aliases or alias.source_path in canonical_paths:
                raise EventIdentityError("manifest_alias_duplicate")
            aliases.add(alias.source_path)
        parsed.append(
            ManifestEvent(
                id=event_id,
                title=title,
                slug=slug,
                path=path,
                source=provided_source,
                source_path=source_path,
                source_checksum=checksum,
                aliases=event_aliases,
            )
        )
    if canonical_paths & aliases:
        raise EventIdentityError("manifest_alias_canonical_collision")
    if counts["aliases"] != len(aliases):
        raise EventIdentityError("manifest_alias_count_invalid")
    return IdentityManifest(IDENTITY_MANIFEST_SCHEMA_VERSION, tuple(parsed))


def load_identity_manifest(path: Path | None = None) -> IdentityManifest:
    manifest_path = path or IDENTITY_MANIFEST_PATH
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EventIdentityError("manifest_unreadable") from exc
    return parse_identity_manifest(payload)


def canonical_detail_path(event_id: uuid.UUID | str) -> str:
    event = resolve_uuid(event_id)
    return f"/events/{event.id}/{event.slug}"


def canonical_detail_url(event_id: uuid.UUID | str) -> str:
    return f"{settings.CANONICAL_ORIGIN.rstrip('/')}{canonical_detail_path(event_id)}"


def current_slug(event_id: uuid.UUID | str) -> str:
    return resolve_uuid(event_id).slug


def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        if value.variant != uuid.RFC_4122:
            raise EventIdentityNotFound("unknown_event")
        return value
    if not isinstance(value, str):
        raise EventIdentityNotFound("unknown_event")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise EventIdentityNotFound("unknown_event") from exc
    if str(parsed) != value or parsed.variant != uuid.RFC_4122:
        raise EventIdentityNotFound("unknown_event")
    return parsed


def resolve_uuid(event_id: uuid.UUID | str) -> Event:
    parsed = _coerce_uuid(event_id)
    try:
        return Event.objects.get(pk=parsed)
    except Event.DoesNotExist as exc:
        raise EventIdentityNotFound("unknown_event") from exc


def resolve_legacy_path(path: str) -> Event:
    if not isinstance(path, str) or not path.startswith("/events/"):
        raise EventIdentityNotFound("unknown_legacy_path")
    candidates = [path]
    if path != "/events/":
        candidates.append(path.removesuffix("/") if path.endswith("/") else f"{path}/")
    for candidate in candidates:
        try:
            return Event.objects.get(aliases__source_path=candidate)
        except Event.DoesNotExist:
            continue
        except Event.MultipleObjectsReturned as exc:
            raise EventIdentityError("legacy_alias_ambiguous") from exc
    raise EventIdentityNotFound("unknown_legacy_path")


def redirect_for_supplied_slug(event_id: uuid.UUID | str, supplied_slug: str) -> str | None:
    event = resolve_uuid(event_id)
    return None if supplied_slug == event.slug else canonical_detail_path(event.id)


def canonical_registration_path(event_id: uuid.UUID | str) -> str:
    return f"{canonical_detail_path(event_id)}/register"


def serialize_event_identity(event: Event) -> dict[str, Any]:
    """Serialize the authorized identity view without public or attendee data."""

    return {
        "id": str(event.id),
        "title": event.title,
        "slug": event.slug,
        "canonical_path": f"/events/{event.id}/{event.slug}",
        "registration_path": f"/events/{event.id}/{event.slug}/register",
        "aliases": [
            {
                "path": alias.source_path,
                "kind": alias.kind,
                "reason": alias.reason,
            }
            for alias in event.aliases.all()
        ],
        "provenance": {
            "repository": event.source_repository,
            "revision": event.source_revision,
            "source_key": event.source_key,
            "source_path": event.source_path,
            "source_checksum": event.source_checksum,
        },
    }


def list_event_identities(*, page: int = 1, page_size: int = 100) -> dict[str, Any]:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("event_identity_page_invalid")
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100:
        raise ValueError("event_identity_page_size_invalid")
    queryset = Event.objects.prefetch_related("aliases").all()
    total = queryset.count()
    offset = (page - 1) * page_size
    return {
        "items": [
            serialize_event_identity(event) for event in queryset[offset : offset + page_size]
        ],
        "page": page,
        "page_size": page_size,
        "total_count": total,
    }


def get_event_identity(event_id: uuid.UUID | str) -> dict[str, Any]:
    return serialize_event_identity(resolve_uuid(event_id))


def resolve_source_identity(*, repository: str, revision: str, source_key: str) -> Event:
    try:
        return Event.objects.get(
            source_repository=repository,
            source_revision=revision,
            source_key=source_key,
        )
    except Event.DoesNotExist as exc:
        raise EventIdentityNotFound("source_identity_unmapped") from exc
    except Event.MultipleObjectsReturned as exc:
        raise EventIdentityError("source_identity_ambiguous") from exc


@transaction.atomic
def attach_historical_mapping(mapping: Any) -> Event:
    """Attach #112's aggregate mapping by exact source identity, never slug/date matching."""

    event = resolve_source_identity(
        repository=mapping.canonical_repository,
        revision=mapping.canonical_revision,
        source_key=mapping.canonical_source_key,
    )
    current_event_id = getattr(mapping, "event_id", None)
    if current_event_id is not None and current_event_id != event.id:
        raise EventIdentityError("source_identity_retarget_forbidden")
    # Keep the relation and immutable provenance snapshot synchronized even when a
    # staged mapping carried an older/date-derived proposal slug.
    mapping.event_id = event.id
    mapping.canonical_repository = event.source_repository
    mapping.canonical_revision = event.source_revision
    mapping.canonical_source_key = event.source_key
    mapping.canonical_slug_snapshot = event.slug
    mapping.save(
        update_fields=(
            "event",
            "canonical_repository",
            "canonical_revision",
            "canonical_source_key",
            "canonical_slug_snapshot",
            "updated_at",
        )
    )
    return event


def event_projection_record(event: Event) -> dict[str, Any]:
    """Return the checked public record for an Event using exact provenance only."""

    from content.public_data import public_projection

    projection = public_projection()
    record = projection.get("events_by_source_identity", {}).get(
        (event.source_repository, event.source_revision, event.source_key)
    )
    if record is None:
        raise EventIdentityNotFound("event_projection_unavailable")
    return record


@transaction.atomic
def import_identity_manifest(
    *, path: Path | None = None, dry_run: bool = False
) -> IdentityImportReport:
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
    created = updated = aliases_created = 0
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
            event = by_id or Event(id=item.id)
            event.title = item.title
            event.slug = item.slug
            event.source_repository = item.source.repository
            event.source_revision = item.source.revision
            event.source_key = item.source.source_key
            event.source_path = item.source_path
            event.source_checksum = item.source_checksum
            event.save()
            for alias in item.aliases:
                alias_row, alias_created = EventAlias.objects.get_or_create(
                    source_path=alias.source_path,
                    defaults={
                        "event": event,
                        "kind": alias.kind,
                        "reason": alias.reason,
                        "source_repository": alias.source.repository,
                        "source_revision": alias.source.revision,
                        "source_key": alias.source.source_key,
                    },
                )
                if not alias_created and (
                    alias_row.event_id != event.id
                    or alias_row.kind != alias.kind
                    or alias_row.reason != alias.reason
                    or alias_row.source_repository != alias.source.repository
                    or alias_row.source_revision != alias.source.revision
                    or alias_row.source_key != alias.source.source_key
                ):
                    raise EventIdentityError("alias_target_conflict")
                aliases_created += int(alias_created)
    if dry_run:
        return IdentityImportReport(
            len(manifest.events),
            len(manifest.aliases),
            created,
            updated,
            0,
            created == 0 and updated == 0,
            True,
        )
    return IdentityImportReport(
        len(manifest.events),
        len(manifest.aliases),
        created,
        updated,
        aliases_created,
        created == 0 and updated == 0 and aliases_created == 0,
        False,
    )
