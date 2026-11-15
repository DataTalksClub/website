"""Repair public SEO data explicitly, outside schema migrations."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from content.models import SyncedDocument
from content.podcast_routes import podcast_canonical_path
from content.sync_parsers.podcasts import imported_podcast_slug
from core.configuration import set_operational_setting
from core.models import OperationalSetting
from core.runtime_config import get_setting, reset_runtime_settings_cache

CANONICAL_ORIGIN = "https://datatalks.club"


class Command(BaseCommand):
    help = "Preview or apply apex canonical origin and imported podcast slug repairs"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--allow-production-write", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        apply = options["apply"]
        if apply and settings.ENVIRONMENT == "production" and not options["allow_production_write"]:
            raise CommandError("Production writes require --allow-production-write")
        key = "site.origin.canonical"
        row = OperationalSetting.objects.filter(key=key).first()
        origin_changed = row is None or row.value != CANONICAL_ORIGIN
        repaired = 0
        for document in SyncedDocument.objects.filter(content_kind="podcast", slug__endswith=".md"):
            record = dict(document.record)
            slug = imported_podcast_slug(document.slug)
            if not slug or record.get("slug") != document.slug:
                raise CommandError("Podcast import identity is inconsistent; re-sync source")
            path = podcast_canonical_path(
                season=record["season"], episode=record["episode"], slug=slug
            )
            record.update(slug=slug, public_path=path)
            if apply:
                document.slug = slug
                document.public_path = path
                document.record = record
                document.save(update_fields=["slug", "public_path", "record", "updated_at"])
            repaired += 1
        if apply and origin_changed:
            set_operational_setting(
                key=key,
                value=CANONICAL_ORIGIN,
                source="seo_cutover_command",
                expected_revision=row.revision if row else 0,
            )
        reset_runtime_settings_cache()
        self.stdout.write(
            f"mode={'apply' if apply else 'preview'} canonical_change={origin_changed} "
            f"podcast_repairs={repaired} noindex={settings.NOINDEX}"
        )
        if apply and get_setting(key) != CANONICAL_ORIGIN:
            raise CommandError("Canonical origin readback failed")
