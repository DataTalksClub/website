from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction

from content.models import ContentDocument, ContentRelease, ContentSource, expected_storage_prefix
from content.services import (
    CreateContentRelease,
    MarkReleaseReady,
    PrepareAsset,
    PreparedAsset,
    PrepareDocument,
    PreparedRelation,
    PrepareRelation,
    TransitionContentRelease,
    asset_manifest_checksum_for,
    begin_release_fetch,
    begin_release_validation,
    create_content_release,
    mark_release_ready,
    prepare_asset,
    prepare_document,
    prepare_relation,
)
from core.idempotency import canonical_json_object
from core.models import RevisionConflict
from core.redaction import redact_value
from core.services import ServiceContext

from .adapter import (
    CandidateBundle,
    CandidateRelation,
    DtcContentValidationError,
)
from .contract import ACCEPTED_CONTENT_COMMIT, DTC_CONTENT_CONTRACT, DtcContentAdapterContract
from .repository import VerifiedCheckout

type PersonResolver = Callable[[str], str | None]
type PreparationProbe = Callable[[str, int], None]


@dataclass(frozen=True, slots=True)
class PreparedCandidateResult:
    release: ContentRelease
    bundle: CandidateBundle
    replayed: bool


def _resolve_person_relations(
    bundle: CandidateBundle,
    *,
    resolver: PersonResolver,
) -> dict[tuple[str, str, str, int], str]:
    resolved: dict[tuple[str, str, str, int], str] = {}
    source_paths = {
        (document.content_kind, document.stable_key): document.source_path
        for document in bundle.documents
    }
    for relation in bundle.relations:
        if relation.target_kind != "person":
            continue
        target = resolver(relation.target_key)
        if not target:
            if relation.is_required:
                raise DtcContentValidationError(
                    "required_person_unresolved",
                    source_paths[(relation.source_kind, relation.source_key)],
                )
            continue
        resolved[
            (
                relation.source_kind,
                relation.source_key,
                relation.relation_type,
                relation.order,
            )
        ] = target
    return resolved


def _existing_release(
    *,
    source: ContentSource,
    bundle: CandidateBundle,
    request_provenance: dict[str, Any],
    using: str,
) -> ContentRelease | None:
    release = (
        ContentRelease.objects.using(using)
        .filter(
            source=source,
            commit_sha=bundle.commit_sha,
            parser_version=bundle.parser_version,
            rendering_version=bundle.rendering_version,
        )
        .first()
    )
    if release is None:
        return None
    if release.status not in {
        ContentRelease.Status.READY,
        ContentRelease.Status.ACTIVE,
        ContentRelease.Status.SUPERSEDED,
    }:
        raise DtcContentValidationError("existing_release_status_mismatch")
    if release.request_provenance != request_provenance:
        raise DtcContentValidationError("existing_release_provenance_mismatch")
    if (
        release.document_count != len(bundle.documents)
        or release.relation_count != len(bundle.relations)
        or release.asset_count != len(bundle.assets)
    ):
        raise DtcContentValidationError("existing_release_count_mismatch")
    return release


def _request_provenance(verified_checkout: VerifiedCheckout) -> dict[str, Any]:
    bundle = verified_checkout.bundle
    provenance = {
        "adapter_schema_version": bundle.schema_version,
        "adapter_type": bundle.adapter_type,
        "branch": bundle.branch,
        "bundle_sha256": bundle.bundle_sha256,
        "repository": bundle.repository,
        "source_counts": dict(bundle.counts),
        "source_tree_sha": bundle.source_tree_sha,
        "verified_origin": verified_checkout.origin,
        "provenance_layers": {
            "original_migration": {
                "commit": bundle.original_migration_commit,
                "manifest": bundle.migration,
                "manifest_sha256": bundle.migration_sha256,
            },
            "repaired_baseline": {
                "commit": bundle.repaired_baseline_commit,
                "tree": bundle.repaired_baseline_tree,
                "ci_run": bundle.repaired_baseline_ci_run,
                "repair_manifest_path": bundle.repair_manifest_path,
                "repair_manifest_sha256": bundle.repair_manifest_sha256,
                "replacement_attestation_sha256": bundle.replacement_attestation_sha256,
                "completion_reference": bundle.repair_completion_reference,
            },
            "editorial_source": {
                "commit": bundle.commit_sha,
                "tree": bundle.source_tree_sha,
                "ci_run": bundle.source_ci_run,
                "editorial_overlay_path": bundle.editorial_overlay_path,
                "editorial_overlay_sha256": bundle.editorial_overlay_sha256,
                "editorial_overlay_issue": bundle.editorial_overlay_issue,
            },
            "projection_parity": (
                verified_checkout.projection_parity.as_dict()
                if verified_checkout.projection_parity is not None
                else None
            ),
        },
    }
    # Match the canonical, redacted representation persisted by the shared
    # release service so an exact replay compares like-for-like.
    return canonical_json_object(redact_value(provenance))


def _prepared_relation(
    relation: CandidateRelation,
    *,
    document_ids: dict[tuple[str, str], uuid.UUID],
    resolved_people: dict[tuple[str, str, str, int], str],
) -> PreparedRelation:
    source_id = document_ids[(relation.source_kind, relation.source_key)]
    target_document_id = None
    resolved_public_path = None
    if relation.target_kind == "person":
        resolved_public_path = resolved_people.get(
            (
                relation.source_kind,
                relation.source_key,
                relation.relation_type,
                relation.order,
            )
        )
    else:
        target_document_id = document_ids[(relation.target_kind, relation.target_key)]
    return PreparedRelation(
        source_document_id=source_id,
        relation_type=relation.relation_type,
        target_kind=relation.target_kind,
        target_key=relation.target_key,
        order=relation.order,
        resolved_target_document_id=target_document_id,
        resolved_public_path=resolved_public_path,
        is_required=relation.is_required,
    )


def prepare_dtc_content_candidate(
    *,
    source_id: uuid.UUID,
    expected_source_revision: int,
    verified_checkout: VerifiedCheckout,
    commit_sha: str,
    person_resolver: PersonResolver,
    context: ServiceContext,
    using: str = "default",
    contract: DtcContentAdapterContract = DTC_CONTENT_CONTRACT,
    preparation_probe: PreparationProbe | None = None,
) -> PreparedCandidateResult:
    """Prepare one complete release using the portable #37 transaction boundary.

    Parsing and identity resolution finish before any database write. The outer transaction makes
    every nested #37 document, relation, asset, revision, and audit write one all-or-nothing unit.
    """

    bundle = verified_checkout.bundle
    if (
        verified_checkout.commit_sha != commit_sha
        or bundle.commit_sha != commit_sha
        or verified_checkout.tree_sha != bundle.source_tree_sha
        or bundle.source_stable_id != contract.stable_id
        or bundle.adapter_type != contract.adapter_type
        or bundle.parser_version != contract.parser_version
        or bundle.rendering_version != contract.rendering_version
        or bundle.schema_version != contract.schema_version
    ):
        raise DtcContentValidationError("verified_checkout_evidence_mismatch")
    if commit_sha == ACCEPTED_CONTENT_COMMIT and (
        verified_checkout.projection_parity is None
        or verified_checkout.projection_parity.status != "PASS"
    ):
        raise DtcContentValidationError("projection_parity_required")
    request_provenance = _request_provenance(verified_checkout)
    resolved_people = _resolve_person_relations(bundle, resolver=person_resolver)
    with transaction.atomic(using=using):
        source = ContentSource.objects.using(using).get(pk=source_id)
        contract.validate_source(source)
        if source.revision != expected_source_revision:
            raise RevisionConflict(
                expected=expected_source_revision,
                actual=source.revision,
            )
        existing = _existing_release(
            source=source,
            bundle=bundle,
            request_provenance=request_provenance,
            using=using,
        )
        if existing is not None:
            return PreparedCandidateResult(release=existing, bundle=bundle, replayed=True)

        try:
            release = create_content_release(
                CreateContentRelease(
                    source_id=source.id,
                    expected_source_revision=source.revision,
                    commit_sha=bundle.commit_sha,
                    parser_version=bundle.parser_version,
                    rendering_version=bundle.rendering_version,
                    request_provenance=request_provenance,
                    public_contracts_sha256=bundle.public_contracts_sha256,
                ),
                context=context,
                using=using,
            )
        except IntegrityError:
            contender = _existing_release(
                source=source,
                bundle=bundle,
                request_provenance=request_provenance,
                using=using,
            )
            if contender is None:
                raise DtcContentValidationError("content_release_concurrency_conflict") from None
            return PreparedCandidateResult(release=contender, bundle=bundle, replayed=True)
        release = begin_release_fetch(
            TransitionContentRelease(release.id, release.revision),
            context=context,
            using=using,
        )
        release = begin_release_validation(
            TransitionContentRelease(release.id, release.revision),
            context=context,
            using=using,
        )
        if preparation_probe is not None:
            preparation_probe("begin", 0)

        document_ids: dict[tuple[str, str], uuid.UUID] = {}
        for index, document in enumerate(bundle.documents):
            persisted = prepare_document(
                PrepareDocument(release.id, release.revision, document),
                context=context,
                using=using,
            )
            document_ids[(document.content_kind, document.stable_key)] = persisted.id
            release.refresh_from_db(using=using)
            if preparation_probe is not None:
                preparation_probe("document", index)

        for index, relation in enumerate(bundle.relations):
            prepared = _prepared_relation(
                relation,
                document_ids=document_ids,
                resolved_people=resolved_people,
            )
            prepare_relation(
                PrepareRelation(release.id, release.revision, prepared),
                context=context,
                using=using,
            )
            release.refresh_from_db(using=using)
            if preparation_probe is not None:
                preparation_probe("relation", index)

        storage_prefix = expected_storage_prefix(source.stable_id, release.id)
        for index, asset in enumerate(bundle.assets):
            prepare_asset(
                PrepareAsset(
                    release.id,
                    release.revision,
                    PreparedAsset(
                        source_path=asset.source_path,
                        stable_public_path=asset.stable_public_path,
                        storage_key=f"{storage_prefix}{asset.source_path}",
                        content_type=asset.content_type,
                        size=asset.size,
                        checksum=asset.checksum,
                        contract_id=asset.contract_id,
                        contract_source_id=asset.contract_source_id,
                        contract_source_revision=asset.contract_source_revision,
                    ),
                ),
                context=context,
                using=using,
            )
            release.refresh_from_db(using=using)
            if preparation_probe is not None:
                preparation_probe("asset", index)

        release = mark_release_ready(
            MarkReleaseReady(
                release_id=release.id,
                expected_revision=release.revision,
                asset_manifest_checksum=asset_manifest_checksum_for(
                    release.id,
                    using=using,
                ),
            ),
            context=context,
            using=using,
        )
        if preparation_probe is not None:
            preparation_probe("ready", 0)
        return PreparedCandidateResult(release=release, bundle=bundle, replayed=False)


def release_document_ids(
    release_id: uuid.UUID,
    *,
    using: str = "default",
) -> tuple[uuid.UUID, ...]:
    """Bounded test/diagnostic helper that never exposes raw source content."""

    return tuple(
        ContentDocument.objects.using(using)
        .filter(release_id=release_id)
        .order_by("content_kind", "stable_key")
        .values_list("id", flat=True)
    )
