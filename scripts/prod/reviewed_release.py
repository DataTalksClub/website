"""Shared reviewed-artifact release creation for the editorial importers.

``import_public_content``, ``import_faq`` and ``import_docs`` all turn one
reviewed artifact into one ``ContentRelease``.  They used to inline the same
~40 lines, and that inline copy had two defects (audit REL-18): the release
sequence was ``max(sequence)+1`` read without any lock, so two concurrent
imports of one source could allocate the same sequence and crash on the
database constraint; and ``commit_sha`` was synthesized from that counter --
a value indistinguishable in shape from a Git revision that identified no
reviewed source commit, and one that made every unchanged rehearsal build
another full release of duplicate rows.

This module owns the shared half; family-specific validation and document
construction stay in the scripts:

* the release identity is a digest of the reviewed artifact's own content
  plus the parser/rendering versions, recorded as such in
  ``request_provenance`` -- never a Git SHA synthesized from a counter.  A
  re-import of identical inputs resolves to the existing release and returns
  a replay receipt instead of accumulating duplicate rows;
* the source row is locked before the sequence is read, so concurrent
  allocations serialize instead of racing; and
* callers receive ``(source, release, created)`` -- ``created=False`` means
  ``release`` is the pre-existing one for these exact inputs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone


def canonical_digest(value: Any) -> str:
    """The stable sha256 of one JSON-serializable artifact."""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def open_reviewed_release(
    *,
    stable_id: str,
    display_name: str,
    repository: str,
    path_allowlist: list[str],
    adapter_type: str,
    mount_path: str,
    parser_version: str,
    rendering_version: str,
    artifact_fingerprint: str,
    artifact_description: dict[str, Any],
    request_provenance: dict[str, Any],
) -> tuple[Any, Any, bool]:
    """Return ``(source, release, created)`` for one reviewed artifact.

    ``created=False`` is the replay receipt: an existing release for exactly
    these inputs (artifact digest + parser + rendering) was found, and the
    caller must not build another set of rows for it.  The release's
    ``commit_sha`` column carries the artifact digest -- the schema demands a
    40-hex SHA-1 there, so this is the reviewed content's own digest, and
    ``request_provenance`` says so explicitly instead of letting it pass as a
    source-control revision.
    """

    from content.models import ContentRelease, ContentSource

    owner, name = repository.split("/", 1)
    commit_sha = hashlib.sha1(
        "\x00".join((parser_version, rendering_version, artifact_fingerprint)).encode("utf-8")
    ).hexdigest()

    try:
        with transaction.atomic():
            source, _ = ContentSource.objects.get_or_create(
                stable_id=stable_id,
                defaults={
                    "display_name": display_name,
                    "repository_owner": owner,
                    "repository_name": name,
                    "branch": "main",
                    "path_allowlist": path_allowlist,
                    "adapter_type": adapter_type,
                    "mount_path": mount_path,
                    "enabled": True,
                },
            )
            # Serializing on the source row is what makes the max+1 sequence
            # allocation below safe: two concurrent imports of one source now
            # queue behind this row lock instead of both reading the same
            # maximum and both inserting it.  PostgreSQL holds the lock to
            # commit; SQLite serializes the whole write transaction.
            source = ContentSource.objects.select_for_update().get(pk=source.pk)

            existing = (
                ContentRelease.objects.filter(
                    source=source,
                    commit_sha=commit_sha,
                    parser_version=parser_version,
                    rendering_version=rendering_version,
                )
                .order_by("sequence")
                .first()
            )
            if existing is not None:
                return source, existing, False

            sequence = (
                ContentRelease.objects.filter(source=source)
                .order_by("-sequence")
                .values_list("sequence", flat=True)
                .first()
                or 0
            ) + 1
            release = ContentRelease.objects.create(
                source=source,
                sequence=sequence,
                based_on_release_id=source.active_release_id,
                commit_sha=commit_sha,
                parser_version=parser_version,
                rendering_version=rendering_version,
                status=ContentRelease.Status.FETCHING,
                requested_at=timezone.now(),
                request_provenance={
                    **request_provenance,
                    # Explicit provenance for the digest above: what it
                    # digests, and that it is not a source-control revision.
                    "commit_sha_origin": "reviewed-artifact-digest",
                    "artifact_fingerprint": artifact_fingerprint,
                    "artifact_description": artifact_description,
                },
            )
    except IntegrityError:
        # A concurrent import of the same artifact created the release between
        # our existence check and our insert; the unique constraint on
        # (source, commit_sha, parser_version, rendering_version) is the
        # backstop.  Its release is this artifact's release.
        existing = ContentRelease.objects.filter(
            source__stable_id=stable_id,
            commit_sha=commit_sha,
            parser_version=parser_version,
            rendering_version=rendering_version,
        ).first()
        if existing is None:
            raise
        source = ContentSource.objects.get(stable_id=stable_id)
        return source, existing, False

    return source, release, True


__all__ = ["canonical_digest", "open_reviewed_release"]
