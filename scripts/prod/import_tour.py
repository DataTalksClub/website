#!/usr/bin/env python3
"""Publish reviewed tour copy from external staging, never on a request path.

Example: uv run python scripts/prod/import_tour.py --database .tmp/local.sqlite3
The reviewed artifact lives beside the other one-time editorial imports.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = True
REVIEWED_PATH = Path.home() / "prod/dtc-data/content-staging/tour_page.json"


def load_reviewed_tour(path: Path) -> dict:
    from django.core.exceptions import ValidationError
    from django.core.validators import URLValidator

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        page = payload["page"]
        assert payload["schema_version"] == 1 and isinstance(page, dict)
        for key in (
            "hero_intro",
            "stats_intro",
            "courses_intro",
            "week_intro",
            "week_note",
            "community_intro",
            "founder_intro",
            "founder_context",
            "founder_quote",
            "founder_name",
            "founder_attribution",
            "founder_source_url",
            "more_intro",
            "close_intro",
            "testimonials_intro",
        ):
            assert isinstance(page[key], str) and page[key].strip()
        for key, size, fields in (
            ("stats", 4, ("value", "label")),
            ("week", 3, ("title", "body")),
            ("community", 3, ("title", "body")),
        ):
            assert isinstance(page[key], list) and len(page[key]) == size
            for item in page[key]:
                assert all(isinstance(item[field], str) and item[field].strip() for field in fields)
        for key in ("youtube", "slack", "events", "podcasts", "articles", "books", "wiki", "docs"):
            assert isinstance(page["channels"][key], str) and page["channels"][key].strip()
        URLValidator(schemes=["https"])(page["founder_source_url"])
        assert isinstance(page["sources"], list) and page["sources"]
        for source in page["sources"]:
            URLValidator(schemes=["https"])(source)
    except (OSError, ValueError, KeyError, TypeError, AssertionError, ValidationError) as error:
        raise ValueError("reviewed_tour_invalid") from error
    return page


def run(*, path: Path = REVIEWED_PATH, apply: bool = True) -> dict:
    page = load_reviewed_tour(path)
    if not apply:
        return {"applied": False, "validated": True}

    from django.db import transaction

    from content.models import ContentDocument
    from content.services import (
        ActivateContentRelease,
        MarkReleaseReady,
        TransitionContentRelease,
        activate_content_release,
        asset_manifest_checksum_for,
        begin_release_validation,
        mark_release_ready,
    )
    from content.tour_content import TOUR_SOURCE_ID
    from core.services import ServiceContext
    from scripts.prod.reviewed_release import canonical_digest, open_reviewed_release

    with transaction.atomic():
        source, release, created = open_reviewed_release(
            stable_id=TOUR_SOURCE_ID,
            display_name="Community tour",
            repository="DataTalksClub/website",
            path_allowlist=["tour_page.json"],
            adapter_type="reviewed-tour-v1",
            mount_path="/-/tour/",
            parser_version="reviewed-tour-v1",
            rendering_version="tour-v1",
            artifact_fingerprint=canonical_digest(page),
            artifact_description={"documents": 1},
            request_provenance={"kind": "import", "source": "tour_page.json"},
        )
        if not created:
            return {"applied": False, "replayed": True, "release": str(release.id)}
        ContentDocument.objects.create(
            release=release,
            content_kind="tour_page",
            stable_key="tour",
            source_path="tour_page.json",
            checksum=canonical_digest(page),
            exact_public_path="/-/tour/copy",
            title="Community tour",
            adapter_metadata={"record": page},
            is_published=True,
            rendered_html="<p>tour_page</p>",
        )
        context = ServiceContext(
            correlation_id=f"import-tour-{release.id}", actor_ref="system:import_tour"
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
                reason="import-tour",
            ),
            context=context,
        )
    return {"applied": True, "replayed": False, "release": str(release.id)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--reviewed-file", type=Path, default=REVIEWED_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    configure_target(parser, args)
    try:
        report = run(path=args.reviewed_file, apply=not args.dry_run)
    except ValueError as error:
        print(str(error))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
