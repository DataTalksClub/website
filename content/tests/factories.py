from __future__ import annotations

import itertools

from content.models import ContentRelease, ContentSource, expected_storage_prefix
from content.services import (
    ActivateContentRelease,
    CreateContentRelease,
    CreateContentSource,
    MarkReleaseReady,
    PrepareAsset,
    PreparedAsset,
    PreparedDocument,
    PrepareDocument,
    TransitionContentRelease,
    activate_content_release,
    asset_manifest_checksum_for,
    begin_release_fetch,
    begin_release_validation,
    create_content_release,
    create_content_source,
    mark_release_ready,
    prepare_asset,
    prepare_document,
)
from core.services import ServiceContext

SHA256_A = "a" * 64
SHA256_B = "b" * 64
CONTEXT = ServiceContext(
    request_id="request-content-tests",
    correlation_id="correlation-content-tests",
    actor_ref="user:content-tests",
)
_counter = itertools.count(1)


def make_source(*, stable_id: str | None = None, enabled: bool = True) -> ContentSource:
    number = next(_counter)
    stable_id = stable_id or f"fixture-source-{number}"
    return create_content_source(
        CreateContentSource(
            stable_id=stable_id,
            display_name=f"Fixture source {number}",
            repository_owner="DataTalksClub",
            repository_name=f"fixture-{number}",
            branch="main",
            path_allowlist=("content/",),
            adapter_type="fixture",
            mount_path="/",
            enabled=enabled,
        ),
        context=CONTEXT,
    )


def make_ready_release(
    source: ContentSource,
    *,
    commit_character: str,
    public_path: str = "/Fixture/Exact.html",
    asset_path: str = "/assets/Fixture-Logo.svg",
    heading: str = "Fixture release v1",
    marker: str = "commit-v1",
    noindex: bool = False,
    is_published: bool = True,
    parser_version: str = "fixture-parser-v1",
    rendering_version: str = "fixture-renderer-v1",
) -> ContentRelease:
    source.refresh_from_db()
    release = create_content_release(
        CreateContentRelease(
            source_id=source.id,
            expected_source_revision=source.revision,
            commit_sha=commit_character * 40,
            parser_version=parser_version,
            rendering_version=rendering_version,
            request_provenance={"mode": "fixture"},
        ),
        context=CONTEXT,
    )
    release = begin_release_fetch(
        TransitionContentRelease(release.id, release.revision), context=CONTEXT
    )
    release = begin_release_validation(
        TransitionContentRelease(release.id, release.revision), context=CONTEXT
    )
    document = PreparedDocument(
        content_kind="fixture",
        stable_key="fixture-exact",
        source_path="fixtures/exact.md",
        checksum=commit_character * 64,
        exact_public_path=public_path,
        slug="fixture-exact",
        title=heading,
        summary="A frozen content release fixture.",
        canonical_url=f"https://datatalks.club{public_path}",
        seo_title=heading,
        seo_description="A frozen content release fixture.",
        raw_frontmatter={"private_build_note": f"raw-{marker}"},
        raw_body=f"# raw {marker}",
        rendered_html=(
            f'<h1>{heading}</h1><p class="release-marker">{marker}</p>'
            f'<img src="{asset_path}" alt="Fixture logo">'
        ),
        adapter_metadata={"diagnostic": "not-public"},
        is_published=is_published,
        noindex=noindex,
        edit_url="https://github.com/DataTalksClub/fixture/edit/main/fixtures/exact.md",
    )
    prepare_document(
        PrepareDocument(release.id, release.revision, document),
        context=CONTEXT,
    )
    release.refresh_from_db()
    storage_key = f"{expected_storage_prefix(source.stable_id, release.id)}logo.svg"
    prepare_asset(
        PrepareAsset(
            release.id,
            release.revision,
            PreparedAsset(
                source_path="fixtures/logo.svg",
                stable_public_path=asset_path,
                storage_key=storage_key,
                content_type="image/svg+xml",
                size=128,
                checksum=commit_character * 64,
            ),
        ),
        context=CONTEXT,
    )
    release.refresh_from_db()
    release = mark_release_ready(
        MarkReleaseReady(
            release_id=release.id,
            expected_revision=release.revision,
            asset_manifest_checksum=asset_manifest_checksum_for(release.id),
        ),
        context=CONTEXT,
    )
    return release


def activate(source: ContentSource, release: ContentRelease) -> ContentRelease:
    source.refresh_from_db()
    release.refresh_from_db()
    activate_content_release(
        ActivateContentRelease(
            source_id=source.id,
            release_id=release.id,
            expected_source_revision=source.revision,
            expected_release_revision=release.revision,
            reason="fixture activation",
        ),
        context=CONTEXT,
    )
    release.refresh_from_db()
    return release
