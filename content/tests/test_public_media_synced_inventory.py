"""The media operator commands act on the served inventory: the synced media rows.

``hydrate``, ``publish`` and ``verify`` used to read a staged ``media.json``
projection, so every media row ``sync_content`` wrote after that file was staged
(podcast covers, new author avatars) was never hydrated, published or verified.
These tests seed ``SyncedDocument`` rows and hold the commands to exactly the
records ``content.catalogue.media()`` serves (issue #463).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.test import TestCase, override_settings

from content import catalogue
from content.media_store import (
    DEFAULT_MAX_OBJECT_BYTES,
    LocalMediaStore,
    MemoryMediaStore,
    S3MediaStore,
    media_records,
)
from content.media_tooling import (
    NoSyncedMediaRecords,
    hydrate_media,
    publish_media,
    verify_media,
)
from content.models import SyncedDocument
from content.tests.test_public_media_tooling import _StubS3Client
from scripts.prod.sync_public_media_hydrate import main as hydrate_main
from scripts.prod.sync_public_media_publish import main as publish_main
from scripts.prod.sync_public_media_verify import main as verify_main
from scripts.prod.target import SCRATCH_ROOT
from test_support.reference_data import add_synced_media

CONTENT_REPOSITORY = "DataTalksClub/content"
MAIN_SITE_REPOSITORY = "DataTalksClub/datatalksclub.github.io"
SCRATCH_DATABASE = str(SCRATCH_ROOT / "issue-463-media-tooling.sqlite3")

#: A podcast cover and a new author avatar: the two kinds of synced media the old
#: staged projection never carried.
NEW_PODCAST_COVER = "images/podcast/s99e01-synthetic-new-cover.jpg"
NEW_AUTHOR_AVATAR = "images/authors/synthetic-new-avatar.jpg"


def _record(key: str, payload: bytes, *, repository: str) -> dict[str, Any]:
    return {
        "content_type": "image/jpeg",
        "record_key": key,
        "public_path": f"/{key}",
        "provenance": {
            "checksum": hashlib.sha256(payload).hexdigest(),
            "repository": repository,
            "revision": "0" * 40,
            "source_path": key,
        },
    }


def _payload(key: str) -> bytes:
    return f"bytes-of-{key}".encode()


class _SyncedMediaCase(TestCase):
    """A database holding a known media inventory, and checkouts holding its bytes."""

    PUBLISHED = (
        ("images/posts/synthetic-article/cover.jpg", CONTENT_REPOSITORY),
        (NEW_PODCAST_COVER, CONTENT_REPOSITORY),
        ("images/authors/synthetic-existing.jpg", MAIN_SITE_REPOSITORY),
        (NEW_AUTHOR_AVATAR, MAIN_SITE_REPOSITORY),
    )
    UNPUBLISHED = (
        ("images/posts/synthetic-withdrawn/cover.jpg", CONTENT_REPOSITORY),
        ("images/authors/synthetic-withdrawn.jpg", MAIN_SITE_REPOSITORY),
    )

    def setUp(self) -> None:
        # The reference media the suite seeds for every test database stands in for
        # nothing here: this inventory is exact, so the counts below are exact.
        SyncedDocument.objects.filter(content_kind="media").delete()
        add_synced_media(
            [_record(key, _payload(key), repository=repo) for key, repo in self.PUBLISHED]
        )
        add_synced_media(
            [_record(key, _payload(key), repository=repo) for key, repo in self.UNPUBLISHED],
            published=False,
        )
        self.checkouts = {
            CONTENT_REPOSITORY: Path(self.enterContext(tempfile.TemporaryDirectory())),
            MAIN_SITE_REPOSITORY: Path(self.enterContext(tempfile.TemporaryDirectory())),
        }
        for key, repository in (*self.PUBLISHED, *self.UNPUBLISHED):
            target = self.checkouts[repository] / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_payload(key))
        self.destination = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # configure_target() exports the target's environment; keep it out of the
        # rest of the suite.
        self.enterContext(patch.dict(os.environ))

    def published_keys(self) -> list[str]:
        return sorted(key for key, _repository in self.PUBLISHED)

    def checkout_arguments(self) -> list[str]:
        arguments: list[str] = []
        for repository, root in self.checkouts.items():
            arguments.extend(("--checkout", f"{repository}={root}"))
        return arguments

    def run_command(self, main: Any, argv: list[str]) -> tuple[int, dict[str, Any]]:
        stream = StringIO()
        with contextlib.redirect_stdout(stream):
            status = main(argv)
        return status, json.loads(stream.getvalue())


class SyncedInventoryTests(_SyncedMediaCase):
    def test_the_records_are_exactly_the_served_published_media(self) -> None:
        keys = sorted(record["record_key"] for record in media_records())
        self.assertEqual(keys, self.published_keys())
        self.assertEqual(keys, sorted(record["record_key"] for record in catalogue.media()))
        # People pictures and editorial images are one merged inventory.
        self.assertIn(NEW_AUTHOR_AVATAR, keys)
        self.assertIn(NEW_PODCAST_COVER, keys)

    def test_hydrate_from_checkouts_processes_exactly_the_published_set(self) -> None:
        report = hydrate_media(
            destination_root=self.destination,
            source="checkout",
            checkouts=self.checkouts,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        self.assertEqual((report.total, report.written, report.failed), (4, 4, 0))
        self.assertEqual(
            (self.destination / NEW_PODCAST_COVER.removeprefix("images/")).read_bytes(),
            _payload(NEW_PODCAST_COVER),
        )
        self.assertFalse((self.destination / "posts" / "synthetic-withdrawn").exists())

    def test_publish_dry_run_processes_exactly_the_published_set(self) -> None:
        hydrate_media(
            destination_root=self.destination,
            source="checkout",
            checkouts=self.checkouts,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        client = _StubS3Client()
        store = S3MediaStore(
            bucket="release-assets",
            prefix="",
            region="",
            endpoint_url="",
            timeout_seconds=1.0,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        store._cached_client = client
        report = publish_media(
            source_root=self.destination,
            store=store,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
            dry_run=True,
        )
        self.assertEqual((report.total, report.added, report.failed), (4, 4, 0))
        self.assertEqual(report.orphan, 0)
        self.assertEqual(client.puts, [])

    def test_verify_reports_the_set_and_an_object_with_no_synced_row_as_extra(self) -> None:
        hydrate_media(
            destination_root=self.destination,
            source="checkout",
            checkouts=self.checkouts,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        store = LocalMediaStore(
            root=self.destination, maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES
        )
        clean = verify_media(store=store)
        self.assertTrue(clean.clean)
        self.assertEqual((clean.total, clean.matched), (4, 4))

        (self.destination / "podcast" / "no-synced-row.jpg").write_bytes(b"orphan")
        follow_up = verify_media(store=store)
        self.assertEqual(follow_up.extra, ["images/podcast/no-synced-row.jpg"])
        self.assertFalse(follow_up.clean)

    def test_the_offline_store_verifies_the_whole_synced_set(self) -> None:
        report = verify_media(store=MemoryMediaStore())
        self.assertTrue(report.clean)
        self.assertEqual(report.matched, 4)

    def test_an_empty_database_is_refused_rather_than_verified_vacuously(self) -> None:
        SyncedDocument.objects.filter(content_kind="media").delete()
        for operation in (
            lambda: hydrate_media(
                destination_root=self.destination,
                source="checkout",
                checkouts=self.checkouts,
                maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
            ),
            lambda: publish_media(
                source_root=self.destination,
                store=MemoryMediaStore(),
                maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
                dry_run=True,
            ),
            lambda: verify_media(store=MemoryMediaStore()),
        ):
            with self.subTest(operation=operation), self.assertRaises(NoSyncedMediaRecords):
                operation()


class SyncedInventoryCommandTests(_SyncedMediaCase):
    def test_hydrate_command_writes_every_published_record(self) -> None:
        with override_settings(PUBLIC_MEDIA_LOCAL_ROOT=self.destination):
            status, report = self.run_command(
                hydrate_main,
                [
                    "--database",
                    SCRATCH_DATABASE,
                    "--source",
                    "checkout",
                    *self.checkout_arguments(),
                ],
            )
        self.assertEqual(status, 0)
        self.assertEqual((report["total"], report["written"]), (4, 4))

    def test_hydrate_command_fails_on_any_failed_record(self) -> None:
        missing = self.checkouts[MAIN_SITE_REPOSITORY] / NEW_AUTHOR_AVATAR
        missing.unlink()
        with override_settings(PUBLIC_MEDIA_LOCAL_ROOT=self.destination):
            status, report = self.run_command(
                hydrate_main,
                [
                    "--database",
                    SCRATCH_DATABASE,
                    "--source",
                    "checkout",
                    *self.checkout_arguments(),
                ],
            )
        self.assertEqual(status, 1)
        self.assertEqual(report["failures"], [NEW_AUTHOR_AVATAR])

    def test_publish_command_dry_run_counts_the_published_set(self) -> None:
        hydrate_media(
            destination_root=self.destination,
            source="checkout",
            checkouts=self.checkouts,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        client = _StubS3Client()
        store = S3MediaStore(
            bucket="release-assets",
            prefix="",
            region="",
            endpoint_url="",
            timeout_seconds=1.0,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        store._cached_client = client
        with patch("content.media_store.media_store", return_value=store):
            status, report = self.run_command(
                publish_main,
                [
                    "--database",
                    SCRATCH_DATABASE,
                    "--source-root",
                    str(self.destination),
                    "--dry-run",
                ],
            )
        self.assertEqual(status, 0)
        self.assertEqual((report["total"], report["added"]), (4, 4))
        self.assertEqual(client.puts, [])

    def test_publish_command_refuses_a_non_object_store_backend(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="local"):
            status, report = self.run_command(publish_main, ["--database", SCRATCH_DATABASE])
        self.assertEqual(status, 1)
        self.assertIn("PUBLIC_MEDIA_STORE_BACKEND=s3", report["error"])

    def test_verify_command_exits_non_zero_on_any_difference(self) -> None:
        with override_settings(
            PUBLIC_MEDIA_STORE_BACKEND="local", PUBLIC_MEDIA_LOCAL_ROOT=self.destination
        ):
            status, report = self.run_command(verify_main, ["--database", SCRATCH_DATABASE])
        self.assertEqual(status, 1)
        self.assertEqual(report["missing_count"], 4)

    def test_verify_command_over_the_offline_store_is_clean(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="memory"):
            status, report = self.run_command(verify_main, ["--database", SCRATCH_DATABASE])
        self.assertEqual(status, 0)
        self.assertEqual((report["total"], report["matched"], report["extra_count"]), (4, 4, 0))


class EmptyDatabaseRefusalTests(_SyncedMediaCase):
    """Zero synced media rows: exit 2, nothing written, no staged file read."""

    def setUp(self) -> None:
        super().setUp()
        SyncedDocument.objects.filter(content_kind="media").delete()
        prod_root = Path.home() / "prod"
        real_read_bytes = Path.read_bytes
        real_read_text = Path.read_text

        def guarded(reader: Any) -> Any:
            def read(path: Path, *args: Any, **kwargs: Any) -> Any:
                if Path(path).resolve().is_relative_to(prod_root):
                    raise AssertionError("a media command read a file under ~/prod")
                return reader(path, *args, **kwargs)

            return read

        self.enterContext(patch.object(Path, "read_bytes", guarded(real_read_bytes)))
        self.enterContext(patch.object(Path, "read_text", guarded(real_read_text)))

    def test_each_command_exits_two_with_the_refusal_code(self) -> None:
        commands = (
            (
                hydrate_main,
                [
                    "--database",
                    SCRATCH_DATABASE,
                    "--source",
                    "checkout",
                    *self.checkout_arguments(),
                ],
            ),
            (publish_main, ["--database", SCRATCH_DATABASE, "--dry-run"]),
            (verify_main, ["--database", SCRATCH_DATABASE]),
        )
        with override_settings(
            PUBLIC_MEDIA_STORE_BACKEND="local", PUBLIC_MEDIA_LOCAL_ROOT=self.destination
        ):
            for main, argv in commands:
                with self.subTest(command=main.__module__):
                    status, report = self.run_command(main, argv)
                    self.assertEqual(status, 2)
                    self.assertEqual(report, {"error": "no_synced_media_records"})
        self.assertEqual(list(self.destination.iterdir()), [])


class MediaCommandTargetTests(_SyncedMediaCase):
    """The shared target selection, plus the scratch-root rule for a local file."""

    COMMANDS: tuple[tuple[Callable[[list[str] | None], int], list[str]], ...] = (
        (hydrate_main, ["--source", "checkout"]),
        (publish_main, ["--dry-run"]),
        (verify_main, []),
    )

    def test_no_target_is_refused(self) -> None:
        for main, argv in self.COMMANDS:
            with self.subTest(command=main.__module__):
                with (
                    contextlib.redirect_stderr(StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    main(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_a_database_outside_the_scratch_root_is_refused(self) -> None:
        outside = Path(self.enterContext(tempfile.TemporaryDirectory())) / "elsewhere.sqlite3"
        for main, argv in self.COMMANDS:
            with self.subTest(command=main.__module__):
                stderr = StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                    main([*argv, "--database", str(outside)])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn(".tmp/", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())

    def test_a_production_target_without_the_opt_in_is_refused(self) -> None:
        for main, argv in self.COMMANDS:
            with self.subTest(command=main.__module__):
                stderr = StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                    main([*argv, "--deployment-target", "website-production"])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("--allow-production-write website-production", stderr.getvalue())

    def test_a_scratch_database_selects_the_local_target(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="memory"):
            status, _report = self.run_command(verify_main, ["--database", SCRATCH_DATABASE])
        self.assertEqual(status, 0)
        self.assertEqual(os.environ["DTC_ENVIRONMENT"], "local")
        self.assertEqual(os.environ["DTC_SQLITE_PATH"], SCRATCH_DATABASE)
