"""Materialise the public projection media objects into a local root.

Examples::

    # fresh clone, from the pinned upstream revisions recorded in each record
    uv run python manage.py public_media_hydrate

    # fully offline, from local checkouts of the pinned upstream repositories
    uv run python manage.py public_media_hydrate --source checkout \\
        --checkout DataTalksClub/content=/path/to/content \\
        --checkout DataTalksClub/datatalksclub.github.io=/path/to/legacy

    # from the configured object store, once the bucket is populated
    uv run python manage.py public_media_hydrate --source store

No AWS credential is required for the ``github`` or ``checkout`` sources.  Every object
is verified against its record's ``provenance.checksum`` and a mismatching object is
never written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from content.media_store import media_store, media_store_config
from content.media_tooling import (
    DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    MediaToolingError,
    hydrate_media,
    parse_checkout_arguments,
)


class Command(BaseCommand):
    help = "Materialise the checked public projection media objects into a local root"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--source",
            choices=("github", "checkout", "store"),
            default="github",
            help="where the bytes come from (default: the pinned upstream revisions)",
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
            default=DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
            help="per-object download timeout for the github source",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="re-fetch and rewrite objects that already match their record",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        config = media_store_config()
        destination = options["destination"] or config.local_root
        try:
            checkouts = parse_checkout_arguments(options["checkout"])
            report = hydrate_media(
                destination_root=destination,
                source=options["source"],
                checkouts=checkouts,
                store=media_store() if options["source"] == "store" else None,
                maximum_object_bytes=config.maximum_object_bytes,
                timeout_seconds=options["timeout"],
                force=options["force"],
            )
        except MediaToolingError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(report.as_dict(), sort_keys=True))
        if report.failed:
            raise CommandError(f"{report.failed} media objects could not be hydrated")
