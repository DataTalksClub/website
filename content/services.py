from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from functools import lru_cache
from time import sleep
from typing import Any

from bleach import Cleaner  # type: ignore[import-untyped]
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Max
from django.utils import timezone

from core.audit import AuditWriteContext, record_audit_event
from core.idempotency import JsonObject, canonical_json, canonical_json_object
from core.models import AuditEvent, RevisionConflict
from core.operations import lock_revisioned
from core.redaction import redact_value
from core.services import ServiceContext

from .inventory import content_route_contracts
from .models import (
    PUBLIC_CONTRACT_DIGEST,
    ActiveContentPath,
    ContentAsset,
    ContentDocument,
    ContentRelation,
    ContentRelease,
    ContentSource,
    active_content_path_digest,
    expected_storage_prefix,
    validate_exact_public_path,
    validate_storage_key_shape,
)
from .ownership import (
    compatible_contract_source,
    source_owns_asset_path,
    source_owns_document_kind,
)

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_OPAQUE_BUILD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RELEASE_SWAP_ATTEMPTS = 8


def _allowed_render_attribute(tag: str, name: str, value: str) -> bool:
    common = {"class", "id", "lang", "title"}
    if name in common:
        return True
    if tag == "a" and name in {"href", "rel"}:
        return True
    if tag == "img" and name == "src":
        return (
            value.startswith("/")
            and not value.startswith("//")
            and "?" not in value
            and "#" not in value
            and not any(character.isspace() or ord(character) < 0x20 for character in value)
        )
    if tag == "img" and name in {"alt", "height", "loading", "width"}:
        return True
    if tag in {"td", "th"} and name in {"colspan", "rowspan"}:
        return True
    if tag == "th" and name == "scope":
        return True
    return tag == "time" and name == "datetime"


_CONTENT_CLEANER = Cleaner(
    tags=frozenset(
        {
            "a",
            "abbr",
            "b",
            "blockquote",
            "br",
            "code",
            "dd",
            "del",
            "details",
            "div",
            "dl",
            "dt",
            "em",
            "figcaption",
            "figure",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "hr",
            "i",
            "img",
            "kbd",
            "li",
            "mark",
            "ol",
            "p",
            "pre",
            "s",
            "span",
            "strong",
            "sub",
            "summary",
            "sup",
            "table",
            "tbody",
            "td",
            "th",
            "thead",
            "time",
            "tr",
            "ul",
        }
    ),
    attributes=_allowed_render_attribute,
    protocols=frozenset({"http", "https", "mailto", "tel"}),
    strip=True,
    strip_comments=True,
)
_ALLOWED_PREPARATION_STATES = frozenset(
    {ContentRelease.Status.FETCHING, ContentRelease.Status.VALIDATING}
)


class ContentLifecycleError(RuntimeError):
    """A requested content-release operation is invalid or unsafe."""


class ContentReadinessError(ContentLifecycleError):
    """A candidate does not contain complete frozen publication evidence."""


class ContentCollisionError(ContentReadinessError):
    """Two enabled content records claim the same exact public path."""


def sanitize_rendered_html(content_kind: str, rendered_html: str) -> str:
    """Apply the deterministic common allowlist before release readiness.

    Source-specific adapters may produce different elements later, but must still hand #37 a body
    accepted by a code-owned sanitizer rather than an unverified trust marker.
    """

    _validate_version(content_kind, field_name="content_kind")
    return _CONTENT_CLEANER.clean(rendered_html)


@dataclass(frozen=True, slots=True)
class CreateContentSource:
    stable_id: str
    display_name: str
    repository_owner: str
    repository_name: str
    branch: str
    path_allowlist: tuple[str, ...]
    adapter_type: str
    mount_path: str
    enabled: bool = False
    max_files: int = 10_000
    max_bytes: int = 100_000_000
    freshness_target_minutes: int = 60
    secret_reference: str = ""


@dataclass(frozen=True, slots=True)
class CreateContentRelease:
    source_id: uuid.UUID
    expected_source_revision: int
    commit_sha: str
    parser_version: str
    rendering_version: str
    request_provenance: Mapping[str, Any]
    webhook_delivery_id: str = ""
    sync_request_id: str = ""
    public_contracts_sha256: str = PUBLIC_CONTRACT_DIGEST


@dataclass(frozen=True, slots=True)
class TransitionContentRelease:
    release_id: uuid.UUID
    expected_revision: int


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    content_kind: str
    stable_key: str
    source_path: str
    checksum: str
    title: str
    source_created_at: datetime | None = None
    source_modified_at: datetime | None = None
    exact_public_path: str | None = None
    slug: str = ""
    summary: str = ""
    canonical_url: str = ""
    seo_title: str = ""
    seo_description: str = ""
    seo_image_url: str = ""
    raw_frontmatter: Mapping[str, Any] | None = None
    raw_body: str = ""
    raw_structured_data: str = ""
    rendered_html: str = ""
    normalized_text: str = ""
    adapter_metadata: Mapping[str, Any] | None = None
    is_published: bool = False
    noindex: bool = False
    edit_url: str = ""
    contract_id: str | None = None
    contract_source_id: str | None = None
    contract_source_revision: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedRelation:
    source_document_id: uuid.UUID
    relation_type: str
    target_kind: str
    target_key: str
    order: int
    resolved_target_document_id: uuid.UUID | None = None
    resolved_public_path: str | None = None
    label: str = ""
    timestamp_seconds: int | None = None
    is_required: bool = True


@dataclass(frozen=True, slots=True)
class PreparedAsset:
    source_path: str
    stable_public_path: str
    storage_key: str
    content_type: str
    size: int
    checksum: str
    contract_id: str | None = None
    contract_source_id: str | None = None
    contract_source_revision: str | None = None


@dataclass(frozen=True, slots=True)
class PrepareDocument:
    release_id: uuid.UUID
    expected_revision: int
    document: PreparedDocument


@dataclass(frozen=True, slots=True)
class PrepareRelation:
    release_id: uuid.UUID
    expected_revision: int
    relation: PreparedRelation


@dataclass(frozen=True, slots=True)
class PrepareAsset:
    release_id: uuid.UUID
    expected_revision: int
    asset: PreparedAsset


@dataclass(frozen=True, slots=True)
class MarkReleaseReady:
    release_id: uuid.UUID
    expected_revision: int
    asset_manifest_checksum: str
    warnings: Sequence[Any] = ()
    structured_errors: Sequence[Any] = ()
    search_build_id: str | None = None
    graph_build_id: str | None = None


@dataclass(frozen=True, slots=True)
class EndReleaseWithDiagnostics:
    release_id: uuid.UUID
    expected_revision: int
    diagnostics: Sequence[Any]


@dataclass(frozen=True, slots=True)
class ActivateContentRelease:
    source_id: uuid.UUID
    release_id: uuid.UUID
    expected_source_revision: int
    expected_release_revision: int
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RollbackContentRelease:
    source_id: uuid.UUID
    release_id: uuid.UUID
    expected_source_revision: int
    expected_release_revision: int
    reason: str


@dataclass(frozen=True, slots=True)
class ReleaseSwapResult:
    source_id: uuid.UUID
    from_release_id: uuid.UUID | None
    to_release_id: uuid.UUID
    source_revision: int
    release_revision: int
    mode: str


def _audit_context(context: ServiceContext) -> AuditWriteContext:
    return AuditWriteContext.from_service_context(context)


def _safe_json_object(value: Mapping[str, Any]) -> JsonObject:
    return canonical_json_object(redact_value(value))


def _safe_json_list(value: Sequence[Any], *, field_name: str) -> list[Any]:
    normalized = canonical_json(redact_value(list(value)))
    if not isinstance(normalized, list):
        raise ValueError(f"{field_name} must be a JSON list")
    return normalized


def _safe_reason(value: str, *, required: bool) -> str:
    normalized = value.strip()
    if required and not normalized:
        raise ContentLifecycleError("rollback requires a non-empty reason")
    redacted = redact_value(normalized)
    if not isinstance(redacted, str):
        raise ValueError("reason must be text")
    return redacted[:512]


def _require_audit_actor(context: ServiceContext) -> None:
    if context.actor_ref is None:
        raise ContentLifecycleError("release swaps require an attributed actor")


def _validate_version(value: str, *, field_name: str) -> str:
    if not _SAFE_VERSION.fullmatch(value):
        raise ValueError(f"{field_name} must be a bounded opaque version")
    return value


def _validate_optional_build_id(value: str | None, *, field_name: str) -> str | None:
    if value is not None and not _OPAQUE_BUILD_ID.fullmatch(value):
        raise ValueError(f"{field_name} must be a bounded opaque identifier")
    return value


def create_content_source(
    command: CreateContentSource,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentSource:
    path_allowlist = canonical_json(list(command.path_allowlist))
    if not isinstance(path_allowlist, list) or not all(
        isinstance(path, str) and path for path in path_allowlist
    ):
        raise ValueError("path_allowlist must contain non-empty paths")
    with transaction.atomic(using=using):
        source = ContentSource(
            stable_id=command.stable_id,
            display_name=command.display_name,
            repository_owner=command.repository_owner,
            repository_name=command.repository_name,
            branch=command.branch,
            path_allowlist=path_allowlist,
            adapter_type=command.adapter_type,
            mount_path=command.mount_path,
            enabled=command.enabled,
            max_files=command.max_files,
            max_bytes=command.max_bytes,
            freshness_target_minutes=command.freshness_target_minutes,
            secret_reference=command.secret_reference,
        )
        source.full_clean()
        source.save(using=using)
        record_audit_event(
            action="content.source.create",
            target_type="content.source",
            target_id=source.id,
            target_label=source.stable_id,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={"revision": {"before": None, "after": source.revision}},
            metadata={"enabled": source.enabled, "adapter_type": source.adapter_type},
            using=using,
        )
        return source


def create_content_release(
    command: CreateContentRelease,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentRelease:
    if not _SHA1.fullmatch(command.commit_sha):
        raise ValueError("commit_sha must be a full lowercase Git SHA")
    _validate_version(command.parser_version, field_name="parser_version")
    _validate_version(command.rendering_version, field_name="rendering_version")
    if not _SHA256.fullmatch(command.public_contracts_sha256):
        raise ValueError("public_contracts_sha256 must be a lowercase SHA-256 digest")
    provenance = _safe_json_object(command.request_provenance)
    with transaction.atomic(using=using):
        source = lock_revisioned(
            ContentSource,
            object_id=command.source_id,
            expected_revision=command.expected_source_revision,
            using=using,
        )
        maximum = (
            ContentRelease.objects.using(using)
            .filter(source=source)
            .aggregate(value=Max("sequence"))["value"]
            or 0
        )
        release = ContentRelease(
            source=source,
            sequence=maximum + 1,
            based_on_release_id=source.active_release_id,
            commit_sha=command.commit_sha,
            parser_version=command.parser_version,
            rendering_version=command.rendering_version,
            requested_at=timezone.now(),
            initiator_ref=context.actor_ref or "",
            request_provenance=provenance,
            webhook_delivery_id=command.webhook_delivery_id,
            sync_request_id=command.sync_request_id,
            public_contracts_sha256=command.public_contracts_sha256,
        )
        release.full_clean()
        release.save(using=using)
        source.revision += 1
        source.save(using=using, update_fields=("revision", "updated_at"))
        record_audit_event(
            action="content.release.create",
            target_type="content.release",
            target_id=release.id,
            target_label=f"{source.stable_id}:{release.sequence}",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={"status": {"before": None, "after": release.status}},
            metadata={
                "source_id": str(source.id),
                "sequence": release.sequence,
                "based_on_release_id": (
                    str(release.based_on_release_id) if release.based_on_release_id else None
                ),
            },
            using=using,
        )
        return release


def _transition(
    command: TransitionContentRelease,
    *,
    expected_status: str,
    next_status: str,
    timestamp_field: str | None,
    action: str,
    context: ServiceContext,
    using: str,
) -> ContentRelease:
    with transaction.atomic(using=using):
        release = lock_revisioned(
            ContentRelease,
            object_id=command.release_id,
            expected_revision=command.expected_revision,
            using=using,
        )
        if release.status != expected_status:
            raise ContentLifecycleError(
                f"cannot transition a {release.status} release to {next_status}"
            )
        release.status = next_status
        if timestamp_field is not None:
            setattr(release, timestamp_field, timezone.now())
        release.revision += 1
        release.save(
            using=using,
            update_fields=("status", timestamp_field, "revision", "updated_at")
            if timestamp_field
            else ("status", "revision", "updated_at"),
        )
        record_audit_event(
            action=action,
            target_type="content.release",
            target_id=release.id,
            target_label=f"{release.source_id}:{release.sequence}",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={"status": {"before": expected_status, "after": next_status}},
            metadata={"source_id": str(release.source_id), "sequence": release.sequence},
            using=using,
        )
        return release


def begin_release_fetch(
    command: TransitionContentRelease,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentRelease:
    return _transition(
        command,
        expected_status=ContentRelease.Status.QUEUED,
        next_status=ContentRelease.Status.FETCHING,
        timestamp_field=None,
        action="content.release.begin_fetch",
        context=context,
        using=using,
    )


def begin_release_validation(
    command: TransitionContentRelease,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentRelease:
    return _transition(
        command,
        expected_status=ContentRelease.Status.FETCHING,
        next_status=ContentRelease.Status.VALIDATING,
        timestamp_field="fetched_at",
        action="content.release.begin_validation",
        context=context,
        using=using,
    )


def _lock_preparing_release(
    release_id: uuid.UUID,
    expected_revision: int,
    *,
    using: str,
) -> ContentRelease:
    release = lock_revisioned(
        ContentRelease,
        object_id=release_id,
        expected_revision=expected_revision,
        using=using,
    )
    if release.status not in _ALLOWED_PREPARATION_STATES:
        raise ContentLifecycleError(f"release {release.id} is not accepting preparation records")
    return release


def _bump_preparation_revision(release: ContentRelease, *, using: str) -> None:
    release.revision += 1
    release.save(using=using, update_fields=("revision", "updated_at"))


def prepare_document(
    command: PrepareDocument,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentDocument:
    raw_frontmatter = canonical_json_object(command.document.raw_frontmatter or {})
    adapter_metadata = canonical_json_object(command.document.adapter_metadata or {})
    with transaction.atomic(using=using):
        release = _lock_preparing_release(
            command.release_id, command.expected_revision, using=using
        )
        if not source_owns_document_kind(
            release.source.stable_id,
            command.document.content_kind,
        ):
            raise ContentReadinessError("content kind is not owned by the candidate source")
        _validate_contract_provenance(
            release,
            public_path=command.document.exact_public_path,
            contract_id=command.document.contract_id,
            source_id=command.document.contract_source_id,
            source_revision=command.document.contract_source_revision,
            expect_asset=False,
        )
        values = {
            field.name: getattr(command.document, field.name)
            for field in fields(command.document)
            if field.name not in {"raw_frontmatter", "adapter_metadata"}
        }
        document = ContentDocument(
            release=release,
            **values,
            raw_frontmatter=raw_frontmatter,
            adapter_metadata=adapter_metadata,
        )
        document.full_clean()
        document.save(using=using)
        _bump_preparation_revision(release, using=using)
        record_audit_event(
            action="content.release.prepare_document",
            target_type="content.release",
            target_id=release.id,
            target_label=f"{release.source_id}:{release.sequence}",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={"revision": {"before": command.expected_revision, "after": release.revision}},
            metadata={"content_kind": document.content_kind, "stable_key": document.stable_key},
            using=using,
        )
        return document


def prepare_relation(
    command: PrepareRelation,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentRelation:
    if (
        command.relation.is_required
        and command.relation.resolved_target_document_id is None
        and command.relation.resolved_public_path is None
    ):
        raise ContentReadinessError("required relation is unresolved")
    with transaction.atomic(using=using):
        release = _lock_preparing_release(
            command.release_id, command.expected_revision, using=using
        )
        source_document = ContentDocument.objects.using(using).get(
            pk=command.relation.source_document_id
        )
        if source_document.release_id != release.id:
            raise ContentReadinessError("relation source document belongs to another release")
        target_id = command.relation.resolved_target_document_id
        if (
            target_id is not None
            and not ContentDocument.objects.using(using)
            .filter(pk=target_id, release=release)
            .exists()
        ):
            raise ContentReadinessError("relation target document belongs to another release")
        relation = ContentRelation(
            source_document=source_document,
            relation_type=command.relation.relation_type,
            target_kind=command.relation.target_kind,
            target_key=command.relation.target_key,
            resolved_target_document_id=target_id,
            resolved_public_path=command.relation.resolved_public_path,
            label=command.relation.label,
            order=command.relation.order,
            timestamp_seconds=command.relation.timestamp_seconds,
            is_required=command.relation.is_required,
        )
        relation.full_clean()
        relation.save(using=using)
        _bump_preparation_revision(release, using=using)
        record_audit_event(
            action="content.release.prepare_relation",
            target_type="content.release",
            target_id=release.id,
            target_label=f"{release.source_id}:{release.sequence}",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={"revision": {"before": command.expected_revision, "after": release.revision}},
            metadata={
                "relation_type": relation.relation_type,
                "target_kind": relation.target_kind,
                "target_key": relation.target_key,
            },
            using=using,
        )
        return relation


def prepare_asset(
    command: PrepareAsset,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentAsset:
    with transaction.atomic(using=using):
        release = _lock_preparing_release(
            command.release_id, command.expected_revision, using=using
        )
        if not source_owns_asset_path(
            release.source.stable_id,
            command.asset.stable_public_path,
        ):
            raise ContentReadinessError("asset namespace is not owned by the candidate source")
        _validate_contract_provenance(
            release,
            public_path=command.asset.stable_public_path,
            contract_id=command.asset.contract_id,
            source_id=command.asset.contract_source_id,
            source_revision=command.asset.contract_source_revision,
            expect_asset=True,
        )
        validate_storage_key_shape(command.asset.storage_key)
        prefix = expected_storage_prefix(release.source.stable_id, release.id)
        if not command.asset.storage_key.startswith(prefix):
            raise ContentReadinessError(
                "asset storage key does not contain source/release identity"
            )
        asset = ContentAsset(
            release=release,
            **{field.name: getattr(command.asset, field.name) for field in fields(command.asset)},
        )
        asset.full_clean()
        asset.save(using=using)
        _bump_preparation_revision(release, using=using)
        record_audit_event(
            action="content.release.prepare_asset",
            target_type="content.release",
            target_id=release.id,
            target_label=f"{release.source_id}:{release.sequence}",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={"revision": {"before": command.expected_revision, "after": release.revision}},
            metadata={"stable_public_path": asset.stable_public_path},
            using=using,
        )
        return asset


def asset_manifest_checksum_for(release_id: uuid.UUID, *, using: str = "default") -> str:
    digest = hashlib.sha256()
    rows = (
        ContentAsset.objects.using(using)
        .filter(release_id=release_id)
        .order_by("stable_public_path")
        .values_list("stable_public_path", "storage_key", "size", "checksum")
    )
    for public_path, storage_key, size, checksum in rows:
        digest.update(f"{public_path}\0{storage_key}\0{size}\0{checksum}\n".encode())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _contract_index() -> dict[str, Any]:
    return {contract.contract_id: contract for contract in content_route_contracts()}


def _validate_contract_provenance(
    release: ContentRelease,
    *,
    public_path: str | None,
    contract_id: str | None,
    source_id: str | None,
    source_revision: str | None,
    expect_asset: bool,
) -> None:
    provenance = (contract_id, source_id, source_revision)
    if provenance == (None, None, None):
        return
    if any(value is None for value in provenance) or public_path is None:
        raise ContentReadinessError("route contract provenance must be complete")
    contract = _contract_index().get(str(contract_id))
    if contract is None:
        raise ContentReadinessError("route contract is not a content base-path contract")
    if (
        contract.percent_encoded_public_reference != public_path
        or contract.source_id != source_id
        or contract.source_revision != source_revision
        or not compatible_contract_source(release.source.stable_id, str(source_id))
        or (contract.contract_kind == "asset") != expect_asset
    ):
        raise ContentReadinessError("route contract provenance does not match the prepared record")


def _validate_rendered_documents(release: ContentRelease, *, using: str) -> None:
    for document in ContentDocument.objects.using(using).filter(release=release).iterator():
        if document.is_published:
            if not document.exact_public_path:
                raise ContentReadinessError("published document is missing its exact public path")
            if not document.rendered_html.strip():
                raise ContentReadinessError("published document is missing rendered HTML")
            if (
                sanitize_rendered_html(document.content_kind, document.rendered_html)
                != document.rendered_html
            ):
                raise ContentReadinessError("published document is not sanitizer-stable")


def _validate_relations(release: ContentRelease, *, using: str) -> None:
    relations = ContentRelation.objects.using(using).filter(source_document__release=release)
    unresolved = relations.filter(
        is_required=True,
        resolved_target_document__isnull=True,
        resolved_public_path__isnull=True,
    )
    cross_release = relations.filter(resolved_target_document__isnull=False).exclude(
        resolved_target_document__release=release
    )
    if unresolved.exists() or cross_release.exists():
        raise ContentReadinessError("required relation resolution is incomplete or cross-release")


def _validate_candidate_paths(release: ContentRelease, *, using: str) -> None:
    document_paths = list(
        ContentDocument.objects.using(using)
        .filter(release=release, exact_public_path__isnull=False)
        .values_list("exact_public_path", flat=True)
    )
    asset_paths = list(
        ContentAsset.objects.using(using)
        .filter(release=release)
        .values_list("stable_public_path", flat=True)
    )
    paths = [*document_paths, *asset_paths]
    if len(paths) != len(set(paths)):
        raise ContentCollisionError("candidate document and asset paths collide")


def _validate_assets(release: ContentRelease, *, using: str) -> None:
    prefix = expected_storage_prefix(release.source.stable_id, release.id)
    for asset in ContentAsset.objects.using(using).filter(release=release).iterator():
        validate_exact_public_path(asset.stable_public_path)
        validate_storage_key_shape(asset.storage_key)
        if not asset.storage_key.startswith(prefix):
            raise ContentReadinessError(
                "asset storage key does not contain source/release identity"
            )


def _readiness_counts(release: ContentRelease, *, using: str) -> tuple[int, int, int]:
    document_count = ContentDocument.objects.using(using).filter(release=release).count()
    relation_count = (
        ContentRelation.objects.using(using).filter(source_document__release=release).count()
    )
    asset_count = ContentAsset.objects.using(using).filter(release=release).count()
    if document_count + asset_count > release.source.max_files:
        raise ContentReadinessError("candidate exceeds the source file-count limit")
    total_bytes = sum(
        len(raw.encode()) + len(rendered.encode())
        for raw, rendered in ContentDocument.objects.using(using)
        .filter(release=release)
        .values_list("raw_body", "rendered_html")
    ) + sum(
        ContentAsset.objects.using(using).filter(release=release).values_list("size", flat=True)
    )
    if total_bytes > release.source.max_bytes:
        raise ContentReadinessError("candidate exceeds the source byte limit")
    return document_count, relation_count, asset_count


def _validate_frozen_readiness(release: ContentRelease, *, using: str) -> None:
    if release.public_contracts_sha256 != PUBLIC_CONTRACT_DIGEST:
        raise ContentReadinessError("release is not bound to the checked public contract artifact")
    if not release.asset_manifest_checksum or not _SHA256.fullmatch(
        release.asset_manifest_checksum
    ):
        raise ContentReadinessError("release is missing a canonical asset manifest checksum")
    if release.asset_manifest_checksum != asset_manifest_checksum_for(release.id, using=using):
        raise ContentReadinessError("asset manifest checksum does not match prepared assets")
    if release.structured_errors:
        raise ContentReadinessError("release contains structured validation errors")
    _validate_rendered_documents(release, using=using)
    _validate_relations(release, using=using)
    _validate_assets(release, using=using)
    _validate_candidate_paths(release, using=using)
    counts = _readiness_counts(release, using=using)
    if counts != (release.document_count, release.relation_count, release.asset_count):
        raise ContentReadinessError("frozen release counts do not match prepared records")


def mark_release_ready(
    command: MarkReleaseReady,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentRelease:
    warnings = _safe_json_list(command.warnings, field_name="warnings")
    errors = _safe_json_list(command.structured_errors, field_name="structured_errors")
    search_build_id = _validate_optional_build_id(
        command.search_build_id, field_name="search_build_id"
    )
    graph_build_id = _validate_optional_build_id(
        command.graph_build_id, field_name="graph_build_id"
    )
    if not _SHA256.fullmatch(command.asset_manifest_checksum):
        raise ContentReadinessError("asset manifest checksum must be a lowercase SHA-256 digest")
    with transaction.atomic(using=using):
        release = lock_revisioned(
            ContentRelease,
            object_id=command.release_id,
            expected_revision=command.expected_revision,
            using=using,
        )
        if release.status != ContentRelease.Status.VALIDATING:
            raise ContentLifecycleError(f"cannot ready a {release.status} release")
        counts = _readiness_counts(release, using=using)
        release.document_count, release.relation_count, release.asset_count = counts
        release.warnings = warnings
        release.warning_count = len(warnings)
        release.structured_errors = errors
        release.search_build_id = search_build_id
        release.graph_build_id = graph_build_id
        release.asset_manifest_checksum = command.asset_manifest_checksum
        _validate_frozen_readiness(release, using=using)
        _validate_enabled_namespace(release.source, release, using=using)
        release.status = ContentRelease.Status.READY
        release.validated_at = timezone.now()
        release.revision += 1
        release.save(
            using=using,
            update_fields=(
                "document_count",
                "relation_count",
                "asset_count",
                "warnings",
                "warning_count",
                "structured_errors",
                "search_build_id",
                "graph_build_id",
                "asset_manifest_checksum",
                "status",
                "validated_at",
                "revision",
                "updated_at",
            ),
        )
        record_audit_event(
            action="content.release.ready",
            target_type="content.release",
            target_id=release.id,
            target_label=f"{release.source_id}:{release.sequence}",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={
                "status": {"before": ContentRelease.Status.VALIDATING, "after": release.status}
            },
            metadata={
                "source_id": str(release.source_id),
                "document_count": release.document_count,
                "relation_count": release.relation_count,
                "asset_count": release.asset_count,
                "warning_count": release.warning_count,
            },
            using=using,
        )
        return release


def _end_release(
    command: EndReleaseWithDiagnostics,
    *,
    allowed_statuses: frozenset[str],
    next_status: str,
    context: ServiceContext,
    using: str,
) -> ContentRelease:
    diagnostics = _safe_json_list(command.diagnostics, field_name="diagnostics")
    if not diagnostics:
        raise ValueError("terminal release diagnostics cannot be empty")
    with transaction.atomic(using=using):
        release = lock_revisioned(
            ContentRelease,
            object_id=command.release_id,
            expected_revision=command.expected_revision,
            using=using,
        )
        if release.status not in allowed_statuses:
            raise ContentLifecycleError(f"cannot mark a {release.status} release {next_status}")
        before = release.status
        now = timezone.now()
        release.status = next_status
        release.structured_errors = diagnostics
        if next_status == ContentRelease.Status.INVALID:
            release.validated_at = now
        else:
            release.failed_at = now
        release.revision += 1
        update_fields = ["status", "structured_errors", "revision", "updated_at"]
        if next_status == ContentRelease.Status.INVALID:
            update_fields.append("validated_at")
        else:
            update_fields.append("failed_at")
        release.save(using=using, update_fields=tuple(update_fields))
        record_audit_event(
            action=f"content.release.{next_status}",
            target_type="content.release",
            target_id=release.id,
            target_label=f"{release.source_id}:{release.sequence}",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={"status": {"before": before, "after": next_status}},
            metadata={"source_id": str(release.source_id), "diagnostic_count": len(diagnostics)},
            using=using,
        )
        return release


def mark_release_invalid(
    command: EndReleaseWithDiagnostics,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentRelease:
    return _end_release(
        command,
        allowed_statuses=frozenset({ContentRelease.Status.VALIDATING}),
        next_status=ContentRelease.Status.INVALID,
        context=context,
        using=using,
    )


def mark_release_failed(
    command: EndReleaseWithDiagnostics,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ContentRelease:
    return _end_release(
        command,
        allowed_statuses=frozenset(
            {
                ContentRelease.Status.QUEUED,
                ContentRelease.Status.FETCHING,
                ContentRelease.Status.VALIDATING,
            }
        ),
        next_status=ContentRelease.Status.FAILED,
        context=context,
        using=using,
    )


def _lock_swap_releases(
    source: ContentSource,
    next_release_id: uuid.UUID,
    *,
    expected_release_revision: int,
    using: str,
) -> tuple[ContentRelease | None, ContentRelease]:
    release_ids = {next_release_id}
    if source.active_release_id is not None:
        release_ids.add(source.active_release_id)
    selected = {
        release.id: release
        for release in ContentRelease.objects.using(using).filter(pk__in=release_ids).order_by("id")
    }
    try:
        next_release = selected[next_release_id]
    except KeyError as error:
        raise ContentRelease.DoesNotExist(next_release_id) from error
    if next_release.revision != expected_release_revision:
        raise RevisionConflict(expected=expected_release_revision, actual=next_release.revision)
    current = (
        selected.get(source.active_release_id) if source.active_release_id is not None else None
    )
    return current, next_release


def _validate_enabled_namespace(
    source: ContentSource,
    next_release: ContentRelease,
    *,
    using: str,
) -> None:
    if not source.enabled:
        return
    next_paths = _release_public_paths(next_release, using=using)
    next_digests = [active_content_path_digest(path) for path in next_paths]
    claimed_collision = (
        ActiveContentPath.objects.using(using)
        .filter(path_digest__in=next_digests)
        .exclude(source=source)
        .exists()
    )
    other_document_paths = (
        ContentDocument.objects.using(using)
        .filter(
            release__source__enabled=True,
            release__status=ContentRelease.Status.ACTIVE,
            release_id=F("release__source__active_release_id"),
        )
        .exclude(release__source=source)
        .filter(exact_public_path__in=next_paths)
    )
    other_asset_paths = (
        ContentAsset.objects.using(using)
        .filter(
            release__source__enabled=True,
            release__status=ContentRelease.Status.ACTIVE,
            release_id=F("release__source__active_release_id"),
            stable_public_path__in=next_paths,
        )
        .exclude(release__source=source)
    )
    if claimed_collision or other_document_paths.exists() or other_asset_paths.exists():
        raise ContentCollisionError("active content path namespace collision")


def _release_public_paths(release: ContentRelease, *, using: str) -> tuple[str, ...]:
    document_paths = (
        ContentDocument.objects.using(using)
        .filter(release=release, exact_public_path__isnull=False)
        .values_list("exact_public_path", flat=True)
    )
    paths = {path for path in document_paths if path is not None} | set(
        ContentAsset.objects.using(using)
        .filter(release=release)
        .values_list("stable_public_path", flat=True)
    )
    return tuple(sorted(paths))


def _path_claims_for_release(
    source: ContentSource,
    release: ContentRelease,
    *,
    using: str,
) -> tuple[ActiveContentPath, ...]:
    claims = tuple(
        ActiveContentPath(
            path_digest=active_content_path_digest(path),
            exact_public_path=path,
            source=source,
            release=release,
        )
        for path in _release_public_paths(release, using=using)
    )
    if len({claim.path_digest for claim in claims}) != len(claims):
        raise ContentCollisionError("active content path namespace collision")
    return claims


def _validate_active_path_claims(
    source: ContentSource,
    current: ContentRelease | None,
    *,
    using: str,
) -> None:
    expected = (
        {
            claim.path_digest: (claim.exact_public_path, claim.release_id)
            for claim in _path_claims_for_release(source, current, using=using)
        }
        if source.enabled and current is not None
        else {}
    )
    actual = {
        digest: (path, release_id)
        for digest, path, release_id in ActiveContentPath.objects.using(using)
        .filter(source=source)
        .values_list("path_digest", "exact_public_path", "release_id")
    }
    if actual != expected:
        raise ContentLifecycleError("source active path claims are inconsistent")


def _replace_active_path_claims(
    source: ContentSource,
    release: ContentRelease,
    *,
    using: str,
) -> None:
    claims = _path_claims_for_release(source, release, using=using) if source.enabled else ()
    ActiveContentPath.objects.using(using).filter(source=source).delete()
    if not claims:
        return
    try:
        with transaction.atomic(using=using):
            ActiveContentPath.objects.using(using).bulk_create(claims)
    except IntegrityError as error:
        conflicting_digests = [claim.path_digest for claim in claims]
        collision_exists = (
            ActiveContentPath.objects.using(using)
            .filter(path_digest__in=conflicting_digests)
            .exclude(source=source)
            .exists()
        )
        if collision_exists:
            raise ContentCollisionError("active content path namespace collision") from error
        raise


def _save_release_lifecycle(
    release: ContentRelease,
    *,
    using: str,
    fields_: tuple[str, ...],
) -> None:
    release.revision += 1
    release.save(using=using, update_fields=(*fields_, "revision", "updated_at"))


def _before_release_swap() -> None:
    """Test seam for a deterministic failure/race immediately before a database swap."""


def _activation_swap_state(
    command: ActivateContentRelease,
    *,
    using: str,
) -> tuple[ContentSource, ContentRelease | None, ContentRelease]:
    source = lock_revisioned(
        ContentSource,
        object_id=command.source_id,
        expected_revision=command.expected_source_revision,
        using=using,
    )
    current, candidate = _lock_swap_releases(
        source,
        command.release_id,
        expected_release_revision=command.expected_release_revision,
        using=using,
    )
    if candidate.source_id != source.id:
        raise ContentLifecycleError("candidate belongs to another source")
    if candidate.status != ContentRelease.Status.READY:
        raise ContentLifecycleError("normal activation requires a ready release")
    if candidate.based_on_release_id != source.active_release_id:
        raise ContentLifecycleError("candidate is based on a stale active release")
    if current is None:
        if candidate.based_on_release_id is not None:
            raise ContentLifecycleError("first release must have no base")
    else:
        if current.status != ContentRelease.Status.ACTIVE:
            raise ContentLifecycleError("source active pointer is inconsistent")
        if candidate.sequence <= current.sequence:
            raise ContentLifecycleError("normal activation sequence must increase")
    _validate_frozen_readiness(candidate, using=using)
    _validate_active_path_claims(source, current, using=using)
    _validate_enabled_namespace(source, candidate, using=using)
    return source, current, candidate


def _retry_release_swap(operation: Callable[[], ReleaseSwapResult]) -> ReleaseSwapResult:
    for attempt in range(_RELEASE_SWAP_ATTEMPTS):
        try:
            return operation()
        except OperationalError:
            if attempt == _RELEASE_SWAP_ATTEMPTS - 1:
                raise
            sleep(0.01 * (2**attempt))
    raise AssertionError("release swap retry loop exhausted without returning")


def _activate_content_release_atomic(
    command: ActivateContentRelease,
    *,
    context: ServiceContext,
    reason: str,
    using: str = "default",
) -> ReleaseSwapResult:
    with transaction.atomic(using=using):
        source, current, candidate = _activation_swap_state(command, using=using)
        _replace_active_path_claims(source, candidate, using=using)
        now = timezone.now()
        if current is not None:
            current.status = ContentRelease.Status.SUPERSEDED
            current.superseded_at = now
            _save_release_lifecycle(current, using=using, fields_=("status", "superseded_at"))
        candidate.status = ContentRelease.Status.ACTIVE
        candidate.activated_at = now
        _save_release_lifecycle(candidate, using=using, fields_=("status", "activated_at"))
        previous_id = source.active_release_id
        source.active_release = candidate
        source.last_successful_commit = candidate.commit_sha
        source.revision += 1
        source.save(
            using=using,
            update_fields=("active_release", "last_successful_commit", "revision", "updated_at"),
        )
        record_audit_event(
            action="content.release.activate",
            target_type="content.source",
            target_id=source.id,
            target_label=source.stable_id,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={
                "active_release_id": {
                    "before": str(previous_id) if previous_id else None,
                    "after": str(candidate.id),
                },
                "source_revision": {
                    "before": command.expected_source_revision,
                    "after": source.revision,
                },
            },
            metadata={
                "source_id": str(source.id),
                "from_release_id": str(previous_id) if previous_id else None,
                "to_release_id": str(candidate.id),
                "mode": "activation",
                "reason": reason,
            },
            using=using,
        )
        return ReleaseSwapResult(
            source_id=source.id,
            from_release_id=previous_id,
            to_release_id=candidate.id,
            source_revision=source.revision,
            release_revision=candidate.revision,
            mode="activation",
        )


def activate_content_release(
    command: ActivateContentRelease,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ReleaseSwapResult:
    _require_audit_actor(context)
    reason = _safe_reason(command.reason, required=False)
    _activation_swap_state(command, using=using)
    _before_release_swap()
    return _retry_release_swap(
        lambda: _activate_content_release_atomic(
            command,
            context=context,
            reason=reason,
            using=using,
        )
    )


def _rollback_swap_state(
    command: RollbackContentRelease,
    *,
    using: str,
) -> tuple[ContentSource, ContentRelease, ContentRelease]:
    source = lock_revisioned(
        ContentSource,
        object_id=command.source_id,
        expected_revision=command.expected_source_revision,
        using=using,
    )
    current, retained = _lock_swap_releases(
        source,
        command.release_id,
        expected_release_revision=command.expected_release_revision,
        using=using,
    )
    if retained.source_id != source.id:
        raise ContentLifecycleError("rollback release belongs to another source")
    if current is None or current.status != ContentRelease.Status.ACTIVE:
        raise ContentLifecycleError("rollback requires one current active release")
    if retained.status != ContentRelease.Status.SUPERSEDED:
        raise ContentLifecycleError("rollback target was not a previously active release")
    if retained.activated_at is None or retained.superseded_at is None:
        raise ContentLifecycleError("rollback target lacks retained activation evidence")
    _validate_frozen_readiness(retained, using=using)
    _validate_active_path_claims(source, current, using=using)
    _validate_enabled_namespace(source, retained, using=using)
    return source, current, retained


def _rollback_content_release_atomic(
    command: RollbackContentRelease,
    *,
    context: ServiceContext,
    reason: str,
    using: str = "default",
) -> ReleaseSwapResult:
    with transaction.atomic(using=using):
        source, current, retained = _rollback_swap_state(command, using=using)
        _replace_active_path_claims(source, retained, using=using)
        now = timezone.now()
        current.status = ContentRelease.Status.SUPERSEDED
        current.superseded_at = now
        _save_release_lifecycle(current, using=using, fields_=("status", "superseded_at"))
        retained.status = ContentRelease.Status.ACTIVE
        retained.activated_at = now
        retained.superseded_at = None
        _save_release_lifecycle(
            retained,
            using=using,
            fields_=("status", "activated_at", "superseded_at"),
        )
        previous_id = source.active_release_id
        source.active_release = retained
        source.last_successful_commit = retained.commit_sha
        source.revision += 1
        source.save(
            using=using,
            update_fields=("active_release", "last_successful_commit", "revision", "updated_at"),
        )
        record_audit_event(
            action="content.release.rollback",
            target_type="content.source",
            target_id=source.id,
            target_label=source.stable_id,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context),
            changes={
                "active_release_id": {"before": str(previous_id), "after": str(retained.id)},
                "source_revision": {
                    "before": command.expected_source_revision,
                    "after": source.revision,
                },
            },
            metadata={
                "source_id": str(source.id),
                "from_release_id": str(previous_id),
                "to_release_id": str(retained.id),
                "mode": "rollback",
                "reason": reason,
            },
            using=using,
        )
        return ReleaseSwapResult(
            source_id=source.id,
            from_release_id=previous_id,
            to_release_id=retained.id,
            source_revision=source.revision,
            release_revision=retained.revision,
            mode="rollback",
        )


def rollback_content_release(
    command: RollbackContentRelease,
    *,
    context: ServiceContext,
    using: str = "default",
) -> ReleaseSwapResult:
    _require_audit_actor(context)
    reason = _safe_reason(command.reason, required=True)
    _rollback_swap_state(command, using=using)
    _before_release_swap()
    return _retry_release_swap(
        lambda: _rollback_content_release_atomic(
            command,
            context=context,
            reason=reason,
            using=using,
        )
    )
