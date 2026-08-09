from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class ActiveContentPathMigrationTests(TransactionTestCase):
    migrate_from = ("content", "0001_initial")
    migrate_to = ("content", "0002_active_content_path_claims")

    def test_existing_enabled_active_release_paths_are_backfilled(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        try:
            old_apps = executor.loader.project_state([self.migrate_from]).apps
            ContentSource = old_apps.get_model("content", "ContentSource")
            ContentRelease = old_apps.get_model("content", "ContentRelease")
            ContentDocument = old_apps.get_model("content", "ContentDocument")
            ContentAsset = old_apps.get_model("content", "ContentAsset")
            now = timezone.now()
            source = ContentSource.objects.create(
                stable_id="migration-backfill",
                display_name="Migration backfill",
                repository_owner="DataTalksClub",
                repository_name="migration-backfill",
                branch="main",
                path_allowlist=["content/"],
                adapter_type="fixture",
                mount_path="/",
                enabled=True,
                last_successful_commit="a" * 40,
            )
            release = ContentRelease.objects.create(
                source=source,
                sequence=1,
                commit_sha="a" * 40,
                parser_version="migration-parser-v1",
                rendering_version="migration-renderer-v1",
                status="active",
                requested_at=now,
                fetched_at=now,
                validated_at=now,
                activated_at=now,
                document_count=1,
                asset_count=1,
                asset_manifest_checksum="b" * 64,
            )
            ContentDocument.objects.create(
                release=release,
                content_kind="fixture",
                stable_key="migration-document",
                source_path="migration.md",
                checksum="c" * 64,
                exact_public_path="/Migration/Backfill.html",
                title="Migration backfill",
                rendered_html="<h1>Migration backfill</h1>",
                is_published=True,
            )
            ContentAsset.objects.create(
                release=release,
                source_path="migration.svg",
                stable_public_path="/assets/migration-backfill.svg",
                storage_key=f"content/{source.stable_id}/{release.id}/migration.svg",
                content_type="image/svg+xml",
                size=128,
                checksum="d" * 64,
            )
            ContentSource.objects.filter(pk=source.pk).update(active_release_id=release.pk)

            executor = MigrationExecutor(connection)
            executor.migrate([self.migrate_to])
            new_apps = executor.loader.project_state([self.migrate_to]).apps
            ActiveContentPath = new_apps.get_model("content", "ActiveContentPath")
            self.assertEqual(
                set(
                    ActiveContentPath.objects.values_list(
                        "source_id",
                        "release_id",
                        "exact_public_path",
                    )
                ),
                {
                    (source.id, release.id, "/Migration/Backfill.html"),
                    (source.id, release.id, "/assets/migration-backfill.svg"),
                },
            )
        finally:
            MigrationExecutor(connection).migrate([self.migrate_to])
