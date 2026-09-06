#!/usr/bin/env python3
"""Materialise the public projection media objects into a local root.

Git-synchronized -- see ``scripts/prod/__init__.py`` for what the two sync
models mean. The set of recorded objects (``temporary/content/public_projection/media.json``)
grows as new content ships, so this is re-run whenever it does, not run once.
Touches no database: every record and every byte lives in files or the
configured object store, never in a model row.

``--source`` has no default. No one source is reachable from every machine, and the
one that used to be the default reads 438 of the records straight out of
``DataTalksClub/datatalksclub.github.io``, the retired repository this repository must
be able to work without. Naming the source is therefore the operator's call.

Examples::

    # fully offline, from local checkouts of the pinned upstream repositories
    uv run --frozen python scripts/prod/sync_public_media_hydrate.py --source checkout \\
        --checkout DataTalksClub/content=/path/to/content \\
        --checkout DataTalksClub/datatalksclub.github.io=/path/to/legacy

    # from the configured object store, once the bucket is populated
    uv run --frozen python scripts/prod/sync_public_media_hydrate.py --source store

    # last resort: the pinned upstream revisions, over the network
    uv run --frozen python scripts/prod/sync_public_media_hydrate.py --source github

No AWS credential is required for the ``github`` or ``checkout`` sources.  Every object
is verified against its record's ``provenance.checksum`` and a mismatching object is
never written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import configure_ambient_settings  # noqa: E402

SYNC_MODEL = "git-synchronized"
# Writes files (or object-store keys), never a database row -- there is no
# database domain for this to bootstrap.
BOOTSTRAPS_EMPTY_DATABASE = False

#: Printed when no ``--source`` is named.  Guessing one is worse than refusing: the
#: historic default, ``github``, pulls 438 of the records from the retired
#: ``DataTalksClub/datatalksclub.github.io``, and no other source is reachable from
#: every machine either.  Say what each one needs so the choice is a decision, not a
#: retry loop.
NO_SOURCE_GUIDANCE = (
    "--source is required; nothing is hydrated without it.\n"
    "  --source checkout  local clones of the pinned repositories, passed with"
    " --checkout REPOSITORY=PATH (offline, no credential)\n"
    "  --source store     the store selected by PUBLIC_MEDIA_STORE_BACKEND: another"
    " hydrated checkout under `local`, or the bucket under `s3`\n"
    "  --source github    the pinned upstream revisions over the network; 438 of the"
    " records still name the retired DataTalksClub/datatalksclub.github.io, so prefer"
    " one of the two above"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("github", "checkout", "store"),
        default=None,
        help="where the bytes come from; required, because no source suits every machine",
    )
    parser.add_argument(
        "--checkout",
        action="append",
        default=[],
        metavar="REPOSITORY=PATH",
        help="offline source checkout for one pinned repository (repeatable)",
    )
    parser.add_argument("--destination", type=Path, default=None)
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="per-object download timeout for the github source",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-fetch and rewrite objects that already match their record",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.source is None:
        parser.error(NO_SOURCE_GUIDANCE)
    configure_ambient_settings()

    from content.media_store import media_store, media_store_config
    from content.media_tooling import (
        DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
        MediaToolingError,
        hydrate_media,
        parse_checkout_arguments,
    )

    config = media_store_config()
    destination = args.destination or config.local_root
    try:
        checkouts = parse_checkout_arguments(args.checkout)
        report = hydrate_media(
            destination_root=destination,
            source=args.source,
            checkouts=checkouts,
            store=media_store() if args.source == "store" else None,
            maximum_object_bytes=config.maximum_object_bytes,
            timeout_seconds=args.timeout or DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
            force=args.force,
        )
    except MediaToolingError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
