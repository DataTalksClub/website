"""Publish public content documents into a test database.

Public pages read their content from ``ContentDocument`` rows reached through an
*active* release of an *enabled* source. That is four models and a three-step
state machine before a single page renders, which is far too much ceremony to
repeat in every test that needs one page to exist.

:func:`publish_documents` is that ceremony, once. A test names the paths it
cares about and gets rows the public read path will actually resolve; a test
that wants the empty state simply does not call it.

This is test support, not an ingestion path. Production content arrives through
``scripts/prod`` and ``content_sync``, which drive the same services against
real upstream sources.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from content.models import ContentDocument, ContentRelease, ContentSource
from content.services import (
    ActivateContentRelease,
    MarkReleaseReady,
    TransitionContentRelease,
    activate_content_release,
    asset_manifest_checksum_for,
    begin_release_validation,
    mark_release_ready,
)
from core.services import ServiceContext


@dataclass(frozen=True, slots=True)
class PublishedPage:
    """One document to publish, described the way a page is described."""

    exact_public_path: str
    title: str
    content_kind: str = "page"
    summary: str = ""
    #: A published document must carry rendered HTML -- the release readiness
    #: check refuses one that does not -- so a page that does not care what its
    #: body says gets a minimal one rather than having to invent it.
    rendered_html: str = ""
    slug: str = ""
    edit_url: str = ""
    noindex: bool = False
    is_published: bool = True
    adapter_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def stable_key(self) -> str:
        return self.slug or self.exact_public_path.strip("/").replace("/", "-") or "root"


def publish_documents(
    pages: Iterable[PublishedPage],
    *,
    stable_id: str = "test-published-content",
) -> ContentRelease:
    """Create an enabled source whose active release publishes ``pages``.

    Calling this twice with the same ``stable_id`` supersedes the earlier
    release, exactly as a re-ingest does: only the newest activated release is
    the one the public read path resolves against.
    """

    source, _ = ContentSource.objects.get_or_create(
        stable_id=stable_id,
        defaults={
            "display_name": "Published test content",
            "repository_owner": "test-owner",
            "repository_name": stable_id,
            "branch": "main",
            "path_allowlist": ["/"],
            "adapter_type": "fixture",
            "mount_path": "/",
            "enabled": True,
        },
    )
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
        # A release is identified by the commit it was built from, and every
        # activation of this source needs its own, so the sequence supplies one.
        commit_sha=f"{sequence:040x}",
        parser_version="test-v1",
        rendering_version="test-v1",
        status=ContentRelease.Status.FETCHING,
        requested_at=timezone.now(),
        request_provenance={"kind": "test"},
    )
    for page in pages:
        ContentDocument.objects.create(
            release=release,
            content_kind=page.content_kind,
            stable_key=page.stable_key,
            source_path=f"{page.stable_key}.md",
            checksum=hashlib.sha256(page.exact_public_path.encode()).hexdigest(),
            exact_public_path=page.exact_public_path,
            slug=page.slug,
            title=page.title,
            summary=page.summary,
            rendered_html=page.rendered_html or f"<p>{page.title}</p>",
            edit_url=page.edit_url,
            noindex=page.noindex,
            is_published=page.is_published,
            adapter_metadata=dict(page.adapter_metadata),
        )
    context = ServiceContext(
        correlation_id=f"publish-{uuid.uuid4().hex}",
        actor_ref="test:published_content",
    )
    release = begin_release_validation(
        TransitionContentRelease(release_id=release.id, expected_revision=release.revision),
        context=context,
    )
    release = mark_release_ready(
        MarkReleaseReady(
            release_id=release.id,
            expected_revision=release.revision,
            asset_manifest_checksum=asset_manifest_checksum_for(release.id),
        ),
        context=context,
    )
    source.refresh_from_db()
    activate_content_release(
        ActivateContentRelease(
            source_id=source.id,
            release_id=release.id,
            expected_source_revision=source.revision,
            expected_release_revision=release.revision,
            reason="test-publish",
        ),
        context=context,
    )
    release.refresh_from_db()
    return release
