from __future__ import annotations

import json
from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import IntegrityError

from content.services import CreateContentSource, create_content_source
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE
from core.context import new_context_id
from core.services import ServiceContext


class Command(BaseCommand):
    help = "Register a course repository as an approved source for signed GitHub push sync."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--stable-id", required=True)
        parser.add_argument("--display-name", required=True)
        parser.add_argument("--owner", required=True)
        parser.add_argument("--repository", required=True)
        parser.add_argument("--branch", default="main")
        parser.add_argument("--secret-reference", default="")
        parser.add_argument("--enabled", action="store_true")
        parser.add_argument("--max-files", type=int, default=5_000)
        parser.add_argument("--max-bytes", type=int, default=100_000_000)

    def handle(self, *args: object, **options: object) -> None:
        del args
        stable_id = cast(str, options["stable_id"])
        display_name = cast(str, options["display_name"])
        owner = cast(str, options["owner"])
        repository = cast(str, options["repository"])
        branch = cast(str, options["branch"])
        secret_reference = cast(str, options["secret_reference"])
        enabled = cast(bool, options["enabled"])
        max_files = cast(int, options["max_files"])
        max_bytes = cast(int, options["max_bytes"])
        try:
            source = create_content_source(
                CreateContentSource(
                    stable_id=stable_id,
                    display_name=display_name,
                    repository_owner=owner,
                    repository_name=repository,
                    branch=branch,
                    path_allowlist=(
                        "course.yaml",
                        "cohorts/**",
                        "**/module.yaml",
                        "**/*.md",
                    ),
                    adapter_type=COURSE_REPOSITORY_ADAPTER_TYPE,
                    mount_path="/",
                    enabled=enabled,
                    max_files=max_files,
                    max_bytes=max_bytes,
                    secret_reference=secret_reference,
                ),
                context=ServiceContext(
                    correlation_id=new_context_id(),
                    actor_ref="management:course-sync",
                ),
            )
        except (IntegrityError, ValueError) as error:
            raise CommandError("course repository registration failed") from error
        self.stdout.write(
            json.dumps(
                {
                    "source_id": str(source.id),
                    "stable_id": source.stable_id,
                    "enabled": source.enabled,
                    "adapter_type": source.adapter_type,
                },
                sort_keys=True,
            )
        )
