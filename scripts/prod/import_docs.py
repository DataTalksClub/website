#!/usr/bin/env python3
"""Import the reviewed documentation into a database.

One-time import.  The documentation is 106 pages and 39 images taken from a
pinned revision of ``DataTalksClub/docs``.  They used to be served straight out
of ``content/docs_projection.json``, which meant the running site read public
content from a file in its own source tree; the reviewed file is now ingest
input and lives with the other one-time inputs under ``temporary/content/``.
See ``scripts/prod/__init__.py`` for what the two sync models mean.

Everything the file claims is checked before anything is written: the schema
version, the pinned revision, the page hierarchy (through the same navigation
builder the site renders from), every page body against its recorded digest,
and every image against its recorded size and digest on disk.  A file that
fails any of those is refused whole -- a partially imported documentation tree
is worse than none, because half of it would 404 without saying so.

Replaying is safe.  Each run writes a new release and activates it, so a second
run with an unchanged file publishes the same pages and the previous release
stops being the active one.

    uv run --frozen python scripts/prod/import_docs.py \\
        --database .tmp/local.sqlite3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = True

REVIEWED_PATH = PROJECT_ROOT / "temporary" / "content" / "docs_projection.json"
#: Where the reviewed images sit. They are read to verify the digests the file
#: records; the site serves them from this same tree by source path.
ASSET_ROOT = PROJECT_ROOT / "content" / "docs_assets"

DOCS_REPOSITORY = "DataTalksClub/docs"


class DocsImportFailure(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def _asset_file(source_path: str) -> Path | None:
    """Resolve one reviewed asset path without allowing traversal or symlinks."""

    if not source_path.startswith("assets/") or "\\" in source_path:
        return None
    relative = source_path.removeprefix("assets/")
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        return None
    path = ASSET_ROOT / Path(*Path(relative).parts)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        path.resolve().relative_to(ASSET_ROOT.resolve())
    except ValueError:
        return None
    return path


def load_reviewed_docs(path: Path) -> dict[str, Any]:
    """Parse and fully validate the reviewed file without touching the database."""

    from content.docs_projection import (
        DOCS_ASSET_CONTENT_TYPES,
        DOCS_ROOT_PATH,
        DOCS_SOURCE_REVISION,
        build_docs_navigation,
    )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DocsImportFailure("reviewed_docs_unreadable") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise DocsImportFailure("reviewed_docs_schema_invalid")
    source = payload.get("source")
    if not isinstance(source, dict) or source.get("revision") != DOCS_SOURCE_REVISION:
        raise DocsImportFailure("reviewed_docs_revision_mismatch")
    if payload.get("root_path") != DOCS_ROOT_PATH:
        raise DocsImportFailure("reviewed_docs_root_path_invalid")

    pages = payload.get("pages")
    assets = payload.get("assets")
    if not isinstance(pages, list) or not pages:
        raise DocsImportFailure("reviewed_docs_no_pages")
    if not isinstance(assets, list) or not assets:
        raise DocsImportFailure("reviewed_docs_no_assets")

    seen_asset_paths: set[str] = set()
    seen_asset_sources: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise DocsImportFailure("reviewed_docs_asset_shape_invalid")
        public_path = asset.get("public_path")
        source_path = asset.get("source_path")
        content_type = asset.get("content_type")
        size = asset.get("size")
        checksum = asset.get("sha256")
        if (
            not isinstance(public_path, str)
            or not public_path.startswith("/docs/assets/")
            or "?" in public_path
            or "#" in public_path
            or public_path in seen_asset_paths
            or not isinstance(source_path, str)
            or source_path in seen_asset_sources
            or not isinstance(content_type, str)
            or content_type not in DOCS_ASSET_CONTENT_TYPES
            or not isinstance(size, int)
            or size < 1
            or not isinstance(checksum, str)
            or asset.get("source_revision") != DOCS_SOURCE_REVISION
        ):
            raise DocsImportFailure("reviewed_docs_asset_metadata_invalid")
        if public_path != f"/docs/{source_path}":
            raise DocsImportFailure("reviewed_docs_asset_public_path_inconsistent")
        file_path = _asset_file(source_path)
        if file_path is None:
            raise DocsImportFailure("reviewed_docs_asset_unavailable")
        payload_bytes = file_path.read_bytes()
        if len(payload_bytes) != size:
            raise DocsImportFailure("reviewed_docs_asset_size_mismatch")
        if hashlib.sha256(payload_bytes).hexdigest() != checksum:
            raise DocsImportFailure("reviewed_docs_asset_checksum_mismatch")
        seen_asset_paths.add(public_path)
        seen_asset_sources.add(source_path)

    # The hierarchy is checked with the builder the site renders from, so a file
    # that imports cleanly is one the navigation can actually be built from.
    try:
        build_docs_navigation(pages)
    except Exception as error:  # noqa: BLE001 - re-raised as a bounded condition code
        raise DocsImportFailure("reviewed_docs_navigation_invalid") from error

    public_paths: set[str] = set()
    source_paths: set[str] = set()
    for page in pages:
        if not isinstance(page, dict):
            raise DocsImportFailure("reviewed_docs_page_shape_invalid")
        public_path = page.get("public_path")
        source_path = page.get("source_path")
        title = page.get("title")
        body = page.get("body")
        if (
            not isinstance(public_path, str)
            or not public_path.startswith(DOCS_ROOT_PATH)
            or not public_path.endswith("/")
            or "?" in public_path
            or "#" in public_path
            or public_path in public_paths
            or not isinstance(source_path, str)
            or not source_path
            or source_path in source_paths
            or not isinstance(title, str)
            or not title.strip()
            or not isinstance(body, str)
        ):
            raise DocsImportFailure("reviewed_docs_page_metadata_invalid")
        if page.get("body_sha256") != hashlib.sha256(body.encode("utf-8")).hexdigest():
            raise DocsImportFailure("reviewed_docs_body_checksum_mismatch")
        if page.get("source_revision") != DOCS_SOURCE_REVISION:
            raise DocsImportFailure("reviewed_docs_page_revision_mismatch")
        public_paths.add(public_path)
        source_paths.add(source_path)
    if DOCS_ROOT_PATH not in public_paths:
        raise DocsImportFailure("reviewed_docs_root_page_missing")
    for page in pages:
        grand_parent_path = page.get("grand_parent_path")
        if grand_parent_path is not None and grand_parent_path not in public_paths:
            raise DocsImportFailure("reviewed_docs_grand_parent_unresolved")
    return payload


def _document_rows(pages: list[dict[str, Any]], *, release: Any) -> list[Any]:
    from content.docs_projection import DOCS_CONTENT_KIND, render_docs_markdown
    from content.models import ContentDocument

    rows = []
    for page in pages:
        rendered, _headings = render_docs_markdown(page)
        rows.append(
            ContentDocument(
                release=release,
                content_kind=DOCS_CONTENT_KIND,
                stable_key=str(page["source_path"]),
                source_path=str(page["source_path"]),
                checksum=str(page["body_sha256"]),
                exact_public_path=str(page["public_path"]),
                slug="",
                title=str(page["title"]),
                summary=str(page.get("description") or ""),
                raw_body=str(page["body"]),
                rendered_html=rendered,
                edit_url=str(page.get("edit_url") or ""),
                adapter_metadata={
                    key: page.get(key)
                    for key in (
                        "parent",
                        "parent_path",
                        "grand_parent",
                        "grand_parent_path",
                        "nav_order",
                        "has_children",
                        "has_toc",
                        "permalink",
                    )
                },
                is_published=True,
            )
        )
    return rows


def run(*, path: Path | None = None, apply: bool = True) -> dict[str, Any]:
    source_file = path or REVIEWED_PATH
    payload = load_reviewed_docs(source_file)
    pages = list(payload["pages"])
    assets = list(payload["assets"])
    if not apply:
        return {"pages": len(pages), "assets": len(assets), "applied": False}

    import uuid

    from django.db import transaction
    from django.utils import timezone

    from content.docs_projection import DOCS_SOURCE_STABLE_ID
    from content.models import (
        ContentAsset,
        ContentDocument,
        ContentRelease,
        ContentSource,
        expected_storage_prefix,
    )
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

    with transaction.atomic():
        source, _ = ContentSource.objects.get_or_create(
            stable_id=DOCS_SOURCE_STABLE_ID,
            defaults={
                "display_name": "DataTalks.Club documentation",
                "repository_owner": DOCS_REPOSITORY.split("/")[0],
                "repository_name": DOCS_REPOSITORY.split("/")[1],
                "branch": "main",
                "path_allowlist": ["/docs/"],
                "adapter_type": "reviewed-docs-v1",
                "mount_path": "/docs/",
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
            based_on_release_id=source.active_release_id,
            commit_sha=f"{sequence:040x}",
            parser_version="reviewed-docs-v1",
            rendering_version="reviewed-docs-v1",
            status=ContentRelease.Status.FETCHING,
            requested_at=timezone.now(),
            request_provenance={"kind": "import", "source_file": source_file.name},
        )
        ContentDocument.objects.bulk_create(_document_rows(pages, release=release))
        ContentAsset.objects.bulk_create(
            [
                ContentAsset(
                    release=release,
                    source_path=str(asset["source_path"]),
                    stable_public_path=str(asset["public_path"]),
                    storage_key=(
                        f"{expected_storage_prefix(DOCS_SOURCE_STABLE_ID, release.id)}"
                        f"{asset['source_path']}"
                    ),
                    content_type=str(asset["content_type"]),
                    size=int(asset["size"]),
                    checksum=str(asset["sha256"]),
                )
                for asset in assets
            ]
        )

    context = ServiceContext(
        correlation_id=f"import-docs-{uuid.uuid4().hex}",
        actor_ref="system:import_docs",
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
            reason="import-docs",
        ),
        context=context,
    )
    return {
        "pages": len(pages),
        "assets": len(assets),
        "release": str(release.id),
        "sequence": sequence,
        "applied": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--reviewed-file", type=Path, default=REVIEWED_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the reviewed file and write nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _configure(args.database.resolve())
        report = run(path=args.reviewed_file.resolve(), apply=not args.dry_run)
    except DocsImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
