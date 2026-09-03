#!/usr/bin/env python3
"""Synchronize the site content -- wiki, podcast, articles, people, books.

Git-synchronized.  ``DataTalksClub/content`` keeps changing and this is re-run
against each reviewed commit.  See ``scripts/prod/__init__.py`` for what the two
sync models mean.

This is the entry point the database-backed content pipeline never had.  Every
piece of it already existed and was reachable only from tests:

    verify_dtc_content_checkout   content_sync/dtc_content/repository.py
    prepare_dtc_content_candidate content_sync/dtc_content/preparation.py
    create/prepare/activate       content/services.py

Nothing here reimplements any of that.  The script verifies one immutable
checkout, prepares a release through the owning application service, activates
it, and reports counts.

One source, and it is not the legacy site
-----------------------------------------

Content comes from the ``DataTalksClub/content`` repository and nowhere else.
``DataTalksClub/datatalksclub.github.io`` is not a content source: this
repository must function without it.  That is not a convention here, it is
enforced upstream -- ``verify_dtc_content_checkout`` refuses any checkout whose
``origin`` is not ``DataTalksClub/content``, so this script cannot be pointed at
the legacy site even by mistake.

Relationship to the checked projection
--------------------------------------

``content/public_projection/*.json`` still serves the live pages, and
``scripts/build_public_projection.py`` still builds it.  This script fills the
database in parallel; it does not cut the pages over.  Until a page reads from
``ContentDocument``, a release activated here changes nothing a visitor sees,
which is exactly what makes it safe to run now.

Re-running
----------

Preparing the same commit twice returns the existing release and reports
``replayed``: no second release, no second document, no second audit event.
Activating an already-active release is likewise a no-op.

    # the checkout must be clean and at the commit you name
    uv run --frozen python scripts/prod/sync_content.py \\
        --database .tmp/local.sqlite3 \\
        --checkout ~/git/content \\
        --commit $(git -C ~/git/content rev-parse HEAD)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYNC_MODEL = "git-synchronized"
# It creates its own ContentSource and release, so it needs nothing already
# present -- but it populates the content domain, never the course catalogue.
BOOTSTRAPS_EMPTY_DATABASE = True

CONTENT_REPOSITORY = "DataTalksClub/content"


class ContentSyncError(RuntimeError):
    """A safe refusal that carries a condition code and the content path at fault.

    The path is a file in a public content repository, not a source *value*, and
    without it an operator cannot tell which of several thousand documents to look
    at.  Nothing from inside a document is ever rendered.
    """

    def __init__(self, code: str, source_path: str = "") -> None:
        self.code = code
        self.source_path = source_path
        super().__init__(f"{code}: {source_path}" if source_path else code)


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def _service_context(correlation_id: str):
    from core.services import ServiceContext

    return ServiceContext(
        request_id=correlation_id,
        correlation_id=correlation_id,
        actor_ref=f"system:{correlation_id}",
    )


def ensure_content_source(*, context, using: str = "default"):
    """Return the one registered content source, creating it on a fresh database."""

    from content.models import ContentSource
    from content_sync.dtc_content.contract import DTC_CONTENT_CONTRACT
    from content.services import create_content_source

    existing = (
        ContentSource.objects.using(using)
        .filter(stable_id=DTC_CONTENT_CONTRACT.stable_id)
        .first()
    )
    if existing is not None:
        # An operator who changed a registered source meant it; never rewrite one.
        DTC_CONTENT_CONTRACT.validate_source(existing)
        return existing, False
    created = create_content_source(
        DTC_CONTENT_CONTRACT.create_source_command(enabled=True),
        context=context,
        using=using,
    )
    return created, True


def person_resolver_for(bundle):
    """Resolve person relations from the bundle's own person documents.

    The people are in the same content repository as the articles and podcasts
    that reference them, so the mapping is read from the bundle rather than from
    the checked projection.  That keeps this script's only input the one
    repository it is allowed to read.
    """

    people = {
        document.stable_key: document.exact_public_path
        for document in bundle.documents
        if document.content_kind == "person" and document.exact_public_path
    }
    return people.get


def _document_counts(bundle) -> dict[str, int]:
    return dict(sorted(Counter(document.content_kind for document in bundle.documents).items()))


def sync(
    *,
    checkout: Path,
    commit: str,
    activate: bool = True,
    correlation_id: str = "prod-sync-content",
    using: str = "default",
) -> dict[str, Any]:
    from content.services import ActivateContentRelease, activate_content_release
    from content_sync.dtc_content import DtcContentValidationError
    from content_sync.dtc_content.preparation import prepare_dtc_content_candidate
    from content_sync.dtc_content.repository import (
        DtcContentCheckoutError,
        verify_dtc_content_checkout,
    )

    context = _service_context(correlation_id)
    try:
        # Refuses a dirty checkout, the wrong commit, and -- structurally -- any
        # origin other than DataTalksClub/content.
        verified = verify_dtc_content_checkout(checkout, expected_commit=commit)
    except DtcContentCheckoutError as error:
        raise ContentSyncError(error.code) from error
    except DtcContentValidationError as error:
        diagnostic = error.diagnostics[0]
        raise ContentSyncError(diagnostic.code, diagnostic.source_path) from error

    source, source_created = ensure_content_source(context=context, using=using)
    try:
        prepared = prepare_dtc_content_candidate(
            source_id=source.id,
            expected_source_revision=source.revision,
            verified_checkout=verified,
            commit_sha=commit,
            person_resolver=person_resolver_for(verified.bundle),
            context=context,
            using=using,
        )
    except DtcContentValidationError as error:
        diagnostic = error.diagnostics[0]
        raise ContentSyncError(diagnostic.code, diagnostic.source_path) from error

    release = prepared.release
    report: dict[str, Any] = {
        "source": CONTENT_REPOSITORY,
        "commit": commit,
        "content_source_created": source_created,
        "release_id": str(release.id),
        "release_status": release.status,
        "replayed": prepared.replayed,
        "documents": release.document_count,
        "documents_by_kind": _document_counts(verified.bundle),
        "relations": release.relation_count,
        "assets": release.asset_count,
        "activated": False,
    }
    if not activate:
        return report

    source.refresh_from_db(using=using)
    release.refresh_from_db(using=using)
    if source.active_release_id == release.id:
        report["activated"] = True
        report["activation"] = "already_active"
        return report
    swap = activate_content_release(
        ActivateContentRelease(
            source_id=source.id,
            release_id=release.id,
            expected_source_revision=source.revision,
            expected_release_revision=release.revision,
            reason="content_sync",
        ),
        context=context,
        using=using,
    )
    report["activated"] = True
    report["activation"] = "swapped"
    report["previous_release_id"] = (
        str(swap.from_release_id) if swap.from_release_id is not None else None
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--checkout",
        required=True,
        type=Path,
        help=f"Clean local checkout of {CONTENT_REPOSITORY}.",
    )
    parser.add_argument(
        "--commit",
        required=True,
        help="The exact commit the checkout is at. Verified, never inferred.",
    )
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="Prepare and validate the release, but leave the active one in place.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure(args.database.resolve())

    try:
        report = sync(
            checkout=args.checkout.expanduser().resolve(),
            commit=args.commit,
            activate=not args.no_activate,
        )
    except ContentSyncError as error:
        # A condition code and the content path at fault; never a source value.
        payload = {"error": error.code}
        if error.source_path:
            payload["source_path"] = error.source_path
        print(json.dumps(payload, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
