#!/usr/bin/env python3
"""Import the reviewed editorial catalogue into a database.

One-time import.  Articles, podcasts and their transcripts, books, people, wiki
pages, course records, media records, the wiki graph and search index, the
podcast platform links and the editorial route manifest -- everything the built
public projection carried.

They used to be read out of ``content/public_projection/`` on the way to every
public request, which meant the running site served public content from files in
its own source tree and re-verified a 37M tree of digests to do it.  The files
are ingest input now: this script checks them once, through
``scripts/projection_build/public_projection_source``, and writes what they hold
into the database the site actually reads.

Each record becomes one published document carrying that record verbatim in its
adapter metadata, so the catalogue the pages read keeps the shape it has always
had.  The five records the projection carries exactly one of -- the manifest,
the platform links, the wiki graph, the wiki search index and the route
manifest -- are stored as a single document apiece.

    uv run --frozen python scripts/prod/import_public_content.py \\
        --database .tmp/local.sqlite3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = True

REVIEWED_ROOT = PROJECT_ROOT / "temporary" / "content" / "public_projection"
#: The Slack landing page. It sits beside the projection rather than inside it
#: because the built tree never carried it -- it came from the review projection,
#: whose only public surface this page was.
REVIEWED_SLACK_PAGE = PROJECT_ROOT / "temporary" / "content" / "slack_page.json"

PUBLIC_CONTENT_REPOSITORY = "DataTalksClub/content"


class PublicContentImportFailure(RuntimeError):
    """A safe refusal that carries a condition code, never a source value."""


def _record_digest(record: Any) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _import_path(kind: str, key: str) -> str:
    """The internal address a stored record is published under.

    These documents are not pages: a person, a wiki node and a media record all
    have their own public paths, or none at all, decided by the record itself.
    The path here only has to be unique and never collide with a real route, so
    it is namespaced under a reserved prefix the URL configuration does not
    serve.
    """

    return f"/-/public-content/{kind}/{key}"


def load_reviewed_catalogue(root: Path | None = None) -> dict[str, Any]:
    """Read and fully check the built projection files."""

    from django.core.exceptions import ImproperlyConfigured

    from scripts.projection_build.public_projection_source import load_checked_projection

    try:
        return load_checked_projection(root)
    except ImproperlyConfigured as error:
        raise PublicContentImportFailure(f"reviewed_catalogue_invalid:{error}") from error


def load_reviewed_slack_page(path: Path | None = None) -> dict[str, Any]:
    """Parse and validate the reviewed Slack page."""

    source = path or REVIEWED_SLACK_PAGE
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublicContentImportFailure("reviewed_slack_page_unreadable") from error
    page = payload.get("page") if isinstance(payload, dict) else None
    if not isinstance(page, dict) or page.get("public_path") != "/slack":
        raise PublicContentImportFailure("reviewed_slack_page_invalid")
    for field in ("title", "lead", "troubleshooting_url"):
        if not isinstance(page.get(field), str) or not page[field].strip():
            raise PublicContentImportFailure(f"reviewed_slack_page_{field}_invalid")
    channels = page.get("channels")
    if not isinstance(channels, list) or not channels:
        raise PublicContentImportFailure("reviewed_slack_page_channels_invalid")
    return page


def run(*, root: Path | None = None, apply: bool = True) -> dict[str, Any]:
    from content.catalogue import COLLECTION_NAMES

    catalogue = load_reviewed_catalogue(root or REVIEWED_ROOT)
    slack_page = load_reviewed_slack_page()
    singletons = (
        "manifest",
        "podcast_platforms",
        "wiki_graph",
        "wiki_search",
        "editorial_route_migration",
    )
    counts = {name: len(catalogue[name]) for name in COLLECTION_NAMES}
    if not apply:
        return {"collections": counts, "applied": False}

    import uuid

    from django.db import transaction
    from django.utils import timezone

    from content.catalogue import PUBLIC_CONTENT_STABLE_ID
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

    owner, name = PUBLIC_CONTENT_REPOSITORY.split("/")
    with transaction.atomic():
        source, _ = ContentSource.objects.get_or_create(
            stable_id=PUBLIC_CONTENT_STABLE_ID,
            defaults={
                "display_name": "DataTalks.Club editorial content",
                "repository_owner": owner,
                "repository_name": name,
                "branch": "main",
                "path_allowlist": ["/"],
                "adapter_type": "reviewed-public-content-v1",
                "mount_path": "/-/public-content/",
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
            parser_version="reviewed-public-content-v1",
            rendering_version="reviewed-public-content-v1",
            status=ContentRelease.Status.FETCHING,
            requested_at=timezone.now(),
            request_provenance={"kind": "import", "source": "public_projection"},
        )

        documents: list[ContentDocument] = []
        for collection in COLLECTION_NAMES:
            kind = collection.rstrip("s") or collection
            for index, record in enumerate(catalogue[collection]):
                key = str(record.get("slug") or record.get("public_path") or index)
                documents.append(
                    ContentDocument(
                        release=release,
                        content_kind=kind,
                        stable_key=key,
                        source_path=f"{collection}.json",
                        checksum=_record_digest(record),
                        exact_public_path=_import_path(kind, _safe_key(key, index)),
                        slug=str(record.get("slug") or "")[:255],
                        title=str(record.get("title") or record.get("name") or key)[:512],
                        # The record verbatim, next to the position it holds in
                        # its collection. Order is editorial -- newest first,
                        # season order, the sequence a hub lists in -- so it
                        # cannot be left to whatever the row key sorts as.
                        adapter_metadata={"record": record, "position": index},
                        rendered_html=f"<p>{kind}</p>",
                        is_published=True,
                    )
                )
        for singleton in singletons:
            value = catalogue[singleton]
            payload = (
                {"platforms": list(value)} if singleton == "podcast_platforms" else dict(value)
            )
            documents.append(
                ContentDocument(
                    release=release,
                    content_kind=singleton,
                    stable_key=singleton,
                    source_path=f"{singleton}.json",
                    checksum=_record_digest(payload),
                    exact_public_path=_import_path(singleton, singleton),
                    title=singleton,
                    adapter_metadata={"record": payload, "position": 0},
                    rendered_html=f"<p>{singleton}</p>",
                    is_published=True,
                )
            )
        documents.append(
            ContentDocument(
                release=release,
                content_kind="page",
                stable_key="slack",
                source_path=str(slack_page["source_path"]),
                checksum=_record_digest(slack_page),
                exact_public_path=str(slack_page["public_path"]),
                slug="slack",
                title=str(slack_page["title"]),
                summary=str(slack_page["lead"]),
                rendered_html=f"<h1>{slack_page['title']}</h1>",
                adapter_metadata={
                    "channels": list(slack_page["channels"]),
                    "troubleshooting_url": str(slack_page["troubleshooting_url"]),
                },
                is_published=True,
            )
        )
        ContentDocument.objects.bulk_create(documents, batch_size=500)

    context = ServiceContext(
        correlation_id=f"import-public-content-{uuid.uuid4().hex}",
        actor_ref="system:import_public_content",
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
            reason="import-public-content",
        ),
        context=context,
    )
    return {
        "collections": counts,
        "documents": len(documents),
        "release": str(release.id),
        "sequence": sequence,
        "applied": True,
    }


def _safe_key(key: str, index: int) -> str:
    """A path-safe, unique segment for one record's internal address."""

    cleaned = "".join(character if character.isalnum() else "-" for character in key).strip("-")
    return f"{cleaned or 'record'}-{index}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--projection-root", type=Path, default=REVIEWED_ROOT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check the reviewed files and write nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        configure_target(parser, args)
        report = run(root=args.projection_root.resolve(), apply=not args.dry_run)
    except PublicContentImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
