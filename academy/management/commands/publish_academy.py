"""Publish an externally built Academy source tree into the database atomically."""

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from academy.models import PublishedResource


class Command(BaseCommand):
    help = "Publish a reviewed Academy build outside the repo; never touches registrations"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("directory", type=Path)
        parser.add_argument("--source-commit", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        directory = options["directory"].resolve()
        commit = options["source_commit"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise CommandError("source-commit must be a complete commit SHA")
        if directory.is_relative_to(Path(settings.BASE_DIR).resolve()):
            raise CommandError("Academy content must be built outside the repository")
        if not directory.is_dir():
            raise CommandError("Build directory does not exist")
        resources: dict[str, tuple[bytes, str]] = {}
        for file in sorted(directory.rglob("*")):
            if file.is_symlink():
                raise CommandError("Build must not contain symlinks")
            if not file.is_file() or file.name.startswith("."):
                continue
            relative = file.relative_to(directory).as_posix()
            if relative == "404.html":
                continue
            key = relative.removesuffix("index.html") if file.name == "index.html" else relative
            body = file.read_bytes()
            mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if file.suffix == ".js":
                mime = "text/javascript"
            if mime.startswith("text/") or mime in {"application/xml", "image/svg+xml"}:
                mime += "; charset=utf-8"
            resources[key] = (body, mime)
        if "sitemap.xml" not in resources or "" not in resources:
            raise CommandError("Build must contain the home page and sitemap.xml")
        try:
            sitemap = ElementTree.fromstring(resources["sitemap.xml"][0])
        except ElementTree.ParseError as exc:
            raise CommandError("Invalid sitemap") from exc
        locations = sitemap.findall("{*}url/{*}loc")
        if not locations:
            raise CommandError("Sitemap must list pages")
        for location in locations:
            url = location.text or ""
            prefix = "https://datatalks.club/academy/"
            if not url.startswith(prefix) or url[len(prefix) :] not in resources:
                raise CommandError("Sitemap contains an unpublished or noncanonical page")
        # Validation completes before the transaction. Old assets stay available to
        # open tabs; imports never delete anything, including registrant records.
        with transaction.atomic():
            for key, (body, mime) in resources.items():
                PublishedResource.objects.update_or_create(
                    path=key,
                    defaults={
                        "content": body,
                        "content_type": mime,
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "source_commit": commit,
                    },
                )
        self.stdout.write(f"Published {len(resources)} Academy resources from {commit}")
