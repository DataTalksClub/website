#!/usr/bin/env python3
"""Process a Luma description export into staging, for events we already have.

Source -> staging.  The Luma export is the source; the artifact this writes,
``temporary/content/luma_event_descriptions.json``, is staging;
``scripts/prod/import_events.py`` moves it into the production database.  See
``_docs/runbooks/data-ingest.md`` for what those three words mean here.

Why this exists alongside the 421-record legacy corpus: that corpus is a frozen
one-time export whose descriptions come from the event description bridge, and
the bridge matches entries on the legacy ``_data/events.yaml`` tuple.  An event
discovered in a Luma export has no such tuple, so the bridge cannot carry its
description and a rebuild would blank it.  Those events therefore get their own
staging artifact, and nothing pinned to the legacy corpus moves when it grows.

What a run does, per description file:

1. pairs it with its ``_json`` checkpoint and resolves the event by the
   provider's own event id -- the exact ``source_key`` the identity was minted
   under.  It never creates an event and never matches on a title or a slug, so
   run ``import_events.py --discover-new-events-only`` first for an event this
   database has not seen;
2. takes ``starts_at`` from that checkpoint and ``type`` from the reviewed input
   file at ``_docs/migration-data/local-event-type-input.json``.  An export the
   reviewed file does not name is reported and skipped -- a type is never
   inferred from a title, a duration or anything else;
3. renders the Markdown through the bridge's own Markdown and link policies;
4. removes the "about the speaker" block and the platform footer with the same
   ``normalize_description_html`` that cleaned the legacy corpus.

**Reporting is the default and it writes nothing.**  A link nobody has reviewed
stops that event and is reported by URL, because approving a destination is a
person's decision: it is an edit to
``scripts/projection_build/event_description_link_policy.py``, never something
inferred here.  Re-run with ``--write`` once the report is clean.

    uv run --frozen python scripts/build_luma_event_descriptions.py \\
        --database .tmp/local.sqlite3
    uv run --frozen python scripts/build_luma_event_descriptions.py \\
        --database .tmp/local.sqlite3 --write
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.staging.luma_event_descriptions import (  # noqa: E402
    ARTIFACT_PATH,
    DEFAULT_DESCRIPTION_ROOT,
    PROVIDER,
    REVIEWED_TYPE_INPUT_PATH,
    LumaDescriptionError,
    build_artifact,
    build_record,
    discover_description_exports,
    load_reviewed_event_types,
    unreviewed_link_destinations,
    write_artifact,
)


def _main_checkout_root() -> Path:
    """The main checkout, where the protected export lives. A worktree has no copy."""

    try:
        common_dir = subprocess.run(
            ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"),
            check=True,
            capture_output=True,
            text=True,
            cwd=REPOSITORY_ROOT,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise LumaDescriptionError("git_common_directory_unavailable") from error
    return Path(common_dir).resolve().parent


def _configure(database: Path) -> None:
    os.environ["DTC_ENVIRONMENT"] = "local"
    os.environ["DTC_SQLITE_PATH"] = str(database)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")

    import django

    django.setup()


def run(*, source_root: Path, event_types: Path, artifact: Path, apply: bool = False) -> dict:
    from events.identity import (
        EventIdentityNotFound,
        provider_source_identity,
        resolve_source_identity,
    )

    exports = discover_description_exports(source_root)
    reviewed = load_reviewed_event_types(event_types)
    # An entry naming a description the export does not hold is a stale review,
    # not a no-op: silently ignoring it would let a decision drift out of the
    # export it was made against without anybody noticing.
    unmatched = sorted(set(reviewed.entries) - {f"{export.stem}.md" for export in exports})
    if unmatched:
        raise LumaDescriptionError("luma_type_input_description_file_unknown")

    # One renderer for the whole run: building it reads the route registry the
    # link policy resolves internal paths against.
    from scripts.staging.luma_event_descriptions import _renderer

    renderer = _renderer()

    records: list[dict[str, Any]] = []
    no_identity_yet: list[str] = []
    no_reviewed_type: list[str] = []
    needs_link_review: list[dict[str, Any]] = []
    for export in exports:
        source = provider_source_identity(
            provider=PROVIDER, external_event_identifier=export.external_event_identifier
        )
        try:
            event = resolve_source_identity(
                repository=source.repository,
                revision=source.revision,
                source_key=source.source_key,
            )
        except EventIdentityNotFound:
            no_identity_yet.append(f"{export.stem}.md")
            continue
        # Both remaining gates are a person's, and they are independent work, so
        # each export is measured against both rather than dropped at the first.
        reviewed_type = reviewed.entries.get(f"{export.stem}.md")
        if reviewed_type is None:
            no_reviewed_type.append(f"{export.stem}.md")
        unreviewed = unreviewed_link_destinations(export.markdown, renderer=renderer)
        if unreviewed:
            needs_link_review.append(
                {
                    "export_file": f"{export.stem}.md",
                    "urls": [{"url": link.url, "reason": link.reason} for link in unreviewed],
                }
            )
        if reviewed_type is None or unreviewed:
            continue
        records.append(
            build_record(
                export,
                identity_id=str(event.id),
                source_repository=source.repository,
                source_revision=source.revision,
                reviewed_type=reviewed_type,
                review_revision=reviewed.review_revision,
                renderer=renderer,
            )
        )

    built = build_artifact(records)
    report: dict[str, Any] = {
        "exports_read": len(exports),
        "reviewed_types": {
            "input": str(event_types),
            "review_revision": reviewed.review_revision,
            "entries": len(reviewed.entries),
        },
        "prepared": len(records),
        # Each of the three below is a different person's next action, so they
        # are never summed into one "skipped" number.
        "no_identity_yet": no_identity_yet,
        "no_reviewed_type": no_reviewed_type,
        "needs_link_review": needs_link_review,
        "counts": built["counts"],
        "applied": apply,
    }
    if apply:
        report["written_to"] = str(write_artifact(built, artifact))
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help=(
            "Export root holding descriptions/ beside _json/. Defaults to "
            f"{DEFAULT_DESCRIPTION_ROOT} in the main checkout."
        ),
    )
    parser.add_argument("--event-types", type=Path, default=REVIEWED_TYPE_INPUT_PATH)
    parser.add_argument("--artifact", type=Path, default=ARTIFACT_PATH)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Replace the staging artifact. Without it the run reports and writes nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _configure(args.database.resolve())
        source_root = (
            args.source_root.resolve()
            if args.source_root is not None
            else _main_checkout_root() / DEFAULT_DESCRIPTION_ROOT
        )
        report = run(
            source_root=source_root,
            event_types=args.event_types.resolve(),
            artifact=args.artifact.resolve(),
            apply=args.write,
        )
    except LumaDescriptionError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    waiting = (
        report["no_identity_yet"] or report["no_reviewed_type"] or report["needs_link_review"]
    )
    # A distinct exit code, so an operator scripting this can tell "nothing to do"
    # from "somebody has to decide something".
    return 2 if waiting else 0


if __name__ == "__main__":
    raise SystemExit(main())
