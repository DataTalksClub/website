from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser

from courses.services.development_content_import import (
    DevelopmentContentImportError,
    import_development_course_content,
)
from courses.services.development_content_transport import (
    delete_s3_artifact_version,
    downloaded_s3_artifact,
)


class Command(BaseCommand):
    help = "Import the one approved sanitized CMP public-content artifact into development"

    def add_arguments(self, parser: CommandParser) -> None:
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--artifact", type=Path)
        source.add_argument("--s3-bucket")
        parser.add_argument("--s3-key")
        parser.add_argument("--s3-version-id")
        parser.add_argument("--expected-bucket-owner")
        parser.add_argument("--expected-kms-key-arn")

    def handle(self, *args: object, **options: object) -> None:
        del args
        using_s3 = bool(options["s3_bucket"])
        s3_names = (
            "s3_key",
            "s3_version_id",
            "expected_bucket_owner",
            "expected_kms_key_arn",
        )
        if using_s3 and not all(options[name] for name in s3_names):
            raise CommandError("all version-pinned encrypted S3 transport arguments are required")
        if not using_s3 and any(options[name] for name in s3_names):
            raise CommandError("S3 transport arguments require --s3-bucket")

        context = (
            downloaded_s3_artifact(
                bucket=options["s3_bucket"],
                key=options["s3_key"],
                version_id=options["s3_version_id"],
                expected_bucket_owner=options["expected_bucket_owner"],
                expected_kms_key_arn=options["expected_kms_key_arn"],
            )
            if using_s3
            else nullcontext(options["artifact"])
        )
        try:
            with context as artifact:
                outcome = import_development_course_content(artifact)
            if using_s3:
                delete_s3_artifact_version(
                    bucket=options["s3_bucket"],
                    key=options["s3_key"],
                    version_id=options["s3_version_id"],
                    expected_bucket_owner=options["expected_bucket_owner"],
                )
        except DevelopmentContentImportError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            json.dumps(
                {
                    "counts": outcome.counts,
                    "imported": outcome.imported,
                    "logical_checksum": outcome.logical_checksum,
                    "relationships": outcome.relationships,
                    "replayed": outcome.replayed,
                    "sensitive_tables_preserved": outcome.sensitive_tables_preserved,
                    "source_sha256": outcome.source_sha256,
                    "transport_deleted": using_s3,
                },
                sort_keys=True,
            )
        )
