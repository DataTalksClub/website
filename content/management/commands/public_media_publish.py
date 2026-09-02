"""Upload the recorded public projection media objects to the configured object store.

    PUBLIC_MEDIA_STORE_BACKEND=s3 PUBLIC_MEDIA_S3_BUCKET=<bucket> \\
        uv run python manage.py public_media_publish

Only objects that have a ``media.json`` record are uploaded, each with the recorded
content type and a SHA-256 checksum.  An object already present with a matching checksum
is skipped.  A file in the source root with no record is reported as an orphan and
deliberately left unpublished, so it keeps returning 404.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from content.media_store import S3MediaStore, media_store, media_store_config
from content.media_tooling import MediaToolingError, publish_media


class Command(BaseCommand):
    help = "Publish the checked public projection media objects to the object store"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--source-root", type=Path, default=None)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would be uploaded without writing to the store",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        config = media_store_config()
        store = media_store()
        if not isinstance(store, S3MediaStore):
            raise CommandError(
                "publishing requires PUBLIC_MEDIA_STORE_BACKEND=s3 and a configured bucket"
            )
        try:
            report = publish_media(
                source_root=options["source_root"] or config.local_root,
                store=store,
                maximum_object_bytes=config.maximum_object_bytes,
                dry_run=options["dry_run"],
            )
        except MediaToolingError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(report.as_dict(), sort_keys=True))
        if report.failed:
            raise CommandError(f"{report.failed} media objects could not be published")
