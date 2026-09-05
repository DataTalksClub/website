"""The pinned public contract of ``/images/...`` across the media store backends.

Every test here runs under whichever backend the environment selects, so the same
assertions hold for a developer or tester with a hydrated local tree and for the
deterministic offline ``memory`` backend used by CI.  The handful of assertions that are
only meaningful against the real bytes are marked and skipped when the local projection
tree has not been hydrated.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from content import catalogue
from content.media_store import (
    MediaObject,
    MediaObjectTooLarge,
    MediaStore,
    MediaStoreUnavailable,
    MemoryMediaStore,
    local_media_root,
    media_records,
    media_store,
    record_relative_path,
)

SPACED_AUTHOR_PATH = "/images/authors/%20aashishnair.jpg"
LARGEST_OBJECT_PATH = (
    "/images/posts/2025-09-23-ai-dev-tools-zoomcamp-2025-free-course-to-master-coding-"
    "assistants-agents-and-automation/course-cover.png"
)
LARGEST_OBJECT_BYTES = 3_022_797
ORPHAN_RELATIVE_PATH = "podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.jpg"
ORPHAN_PATH = f"/images/{ORPHAN_RELATIVE_PATH}"


def _serving_real_bytes() -> bool:
    """True when the configured backend serves the real hydrated projection objects."""

    from django.conf import settings

    return (
        str(getattr(settings, "PUBLIC_MEDIA_STORE_BACKEND", "local")) == "local"
        and (local_media_root() / "authors" / " aashishnair.jpg").is_file()
    )


requires_hydrated_tree = unittest.skipUnless(
    _serving_real_bytes(),
    "the configured media store does not serve the real hydrated objects "
    "(run scripts/prod/sync_public_media_hydrate.py with the local backend)",
)


def _body(response: Any) -> bytes:
    if response.streaming:
        return b"".join(response.streaming_content)
    return response.content


class _FailingStore(MediaStore):
    name = "failing"

    def __init__(self, error: Exception) -> None:
        self._error = error

    def expected_checksum(self, record: Any) -> str:
        return "0" * 64

    def fetch(self, record: Any) -> MediaObject:
        raise self._error


class _CorruptingStore(MemoryMediaStore):
    """A fixture store whose returned bytes do not match the expected digest."""

    def fetch(self, record: Any) -> MediaObject:
        return MediaObject(payload=b"corrupted-object-bytes")


class MediaResponseContractTests(TestCase):
    """Pin the observable response contract that must not move in this issue."""

    records: dict[str, Any]

    @classmethod
    def setUpTestData(cls) -> None:
        cls.records = {record["public_path"]: record for record in catalogue.media()}

    def test_every_record_resolves_with_its_recorded_content_type_offline(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="memory"):
            for record in self.records.values():
                with self.subTest(path=record["public_path"]):
                    response = self.client.get(record["public_path"])
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["Content-Type"], record["content_type"])

    def test_the_success_header_set_is_pinned_explicitly(self) -> None:
        store = media_store()
        sampled = (
            SPACED_AUTHOR_PATH,
            LARGEST_OBJECT_PATH,
            "/images/podcast/badges/spotify.svg",
        )
        for path in sampled:
            with self.subTest(path=path):
                response = self.client.get(path)
                record = self.records[response.wsgi_request.path]
                payload = store.fetch(record).payload
                filename = record["record_key"].rsplit("/", 1)[-1]
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Content-Type"], record["content_type"])
                self.assertEqual(response.headers["Content-Length"], str(len(payload)))
                self.assertEqual(
                    response.headers["Content-Disposition"],
                    f'inline; filename="{filename}"',
                )
                # This issue adds no caching contract to a successful media response.
                self.assertNotIn("Cache-Control", response.headers)
                self.assertEqual(_body(response), payload)

    def test_the_spaced_filename_still_resolves_with_verified_bytes(self) -> None:
        store = media_store()
        path, filename = SPACED_AUTHOR_PATH, " aashishnair.jpg"
        response = self.client.get(path)
        record = self.records[response.wsgi_request.path]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertEqual(response.headers["Content-Disposition"], f'inline; filename="{filename}"')
        payload = _body(response)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), store.expected_checksum(record))

    @requires_hydrated_tree
    def test_the_spaced_filename_serves_the_exact_recorded_upstream_bytes(self) -> None:
        response = self.client.get(SPACED_AUTHOR_PATH)
        record = self.records[response.wsgi_request.path]
        self.assertEqual(
            hashlib.sha256(_body(response)).hexdigest(),
            record["provenance"]["checksum"],
        )

    def test_the_content_length_always_matches_the_served_body(self) -> None:
        response = self.client.get(LARGEST_OBJECT_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Length"], str(len(_body(response))))

    @requires_hydrated_tree
    def test_the_largest_object_reports_its_exact_length(self) -> None:
        response = self.client.get(LARGEST_OBJECT_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Length"], str(LARGEST_OBJECT_BYTES))
        self.assertEqual(len(_body(response)), LARGEST_OBJECT_BYTES)

    def test_head_matches_the_get_header_shape(self) -> None:
        for path in (SPACED_AUTHOR_PATH, LARGEST_OBJECT_PATH):
            with self.subTest(path=path):
                head = self.client.head(path)
                get = self.client.get(path)
                self.assertEqual(head.status_code, 200)
                for header in ("Content-Type", "Content-Length", "Content-Disposition"):
                    self.assertEqual(head.headers[header], get.headers[header])

    def test_unknown_traversal_and_orphan_paths_stay_ordinary_404s(self) -> None:
        for path in (
            "/images/does-not-exist.jpg",
            "/images/../../manage.py",
            "/images/logo.svg",
            ORPHAN_PATH,
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)
                self.assertContains(response, "Page not found", status_code=404)

    @requires_hydrated_tree
    def test_the_orphan_file_exists_on_disk_yet_is_never_served(self) -> None:
        """A file present on disk with no manifest record must still 404.

        This writes its own synthetic orphan directly into the hydrated local
        media tree rather than pinning to one specific real file: real podcast
        cover images are legitimately deleted as content changes, which made a
        fixed real-file fixture fragile to correct cleanup elsewhere, not to a
        regression in how orphaned files are served.
        """

        orphan_relative_path = "podcast/_test-orphan-fixture.jpg"
        orphan_path = f"/images/{orphan_relative_path}"
        disk_path = local_media_root() / orphan_relative_path
        self.assertNotIn(orphan_path, self.records)

        disk_path.write_bytes(b"synthetic orphan fixture, not a manifest record")
        self.addCleanup(disk_path.unlink, missing_ok=True)

        self.assertTrue(disk_path.is_file())
        self.assertEqual(self.client.get(orphan_path).status_code, 404)


class MediaFailureContractTests(TestCase):
    """A recorded object that cannot be served fails closed as an uncacheable 502."""

    def _assert_fail_closed(self, response: Any) -> None:
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        body = _body(response).decode()
        self.assertEqual(body, "Image temporarily unavailable.\n")
        for leak in (
            "release-assets",
            "public-projection",
            "Traceback",
            "boto",
            "aashishnair",
            "/home/",
        ):
            self.assertNotIn(leak, body)

    def test_a_corrupted_object_is_never_served(self) -> None:
        with patch("content.public_views.media_store", return_value=_CorruptingStore()):
            with patch("content.public_views.record_event") as recorded:
                response = self.client.get(SPACED_AUTHOR_PATH)
        self._assert_fail_closed(response)
        self.assertEqual(recorded.call_count, 1)
        properties = recorded.call_args.kwargs["properties"]
        self.assertEqual(properties["media_failure_reason"], "checksum-mismatch")
        self.assertEqual(properties["media_store_backend"], "memory")
        self.assertNotIn("aashishnair", str(properties))

    def test_a_store_timeout_missing_or_oversized_object_fails_closed(self) -> None:
        for error, reason in (
            (MediaStoreUnavailable("timeout"), "store-unavailable"),
            (MediaObjectTooLarge("oversized"), "object-oversized"),
        ):
            with self.subTest(reason=reason):
                store = _FailingStore(error)
                with patch("content.public_views.media_store", return_value=store):
                    with patch("content.public_views.record_event") as recorded:
                        response = self.client.get(SPACED_AUTHOR_PATH)
                self._assert_fail_closed(response)
                self.assertEqual(recorded.call_count, 1)
                self.assertEqual(
                    recorded.call_args.kwargs["properties"]["media_failure_reason"], reason
                )

    def test_an_empty_local_root_fails_closed_rather_than_404(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with override_settings(
                PUBLIC_MEDIA_STORE_BACKEND="local", PUBLIC_MEDIA_LOCAL_ROOT=Path(empty)
            ):
                self._assert_fail_closed(self.client.get(SPACED_AUTHOR_PATH))

    def test_an_oversized_object_is_bounded_by_the_configured_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "authors" / " aashishnair.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * 4096)
            with override_settings(
                PUBLIC_MEDIA_STORE_BACKEND="local",
                PUBLIC_MEDIA_LOCAL_ROOT=Path(root),
                PUBLIC_MEDIA_MAX_OBJECT_BYTES=1024,
            ):
                self._assert_fail_closed(self.client.get(SPACED_AUTHOR_PATH))


class MediaStoreSystemCheckTests(TestCase):
    def test_an_empty_local_root_warns_with_the_hydrate_command(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with override_settings(
                PUBLIC_MEDIA_STORE_BACKEND="local", PUBLIC_MEDIA_LOCAL_ROOT=Path(empty)
            ):
                stream = StringIO()
                call_command("check", stdout=stream, stderr=stream)
                self.assertIn("public_media_hydrate", stream.getvalue())
                self.assertIn("content.W001", stream.getvalue())

    def test_a_populated_root_or_offline_backend_does_not_warn(self) -> None:
        from content.apps import public_media_store_check

        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "authors").mkdir()
            (Path(root) / "authors" / "example.jpg").write_bytes(b"fixture")
            with override_settings(
                PUBLIC_MEDIA_STORE_BACKEND="local", PUBLIC_MEDIA_LOCAL_ROOT=Path(root)
            ):
                self.assertEqual(public_media_store_check(None), [])
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="memory"):
            self.assertEqual(public_media_store_check(None), [])

    def test_every_deployed_environment_requires_the_object_store_backend(self) -> None:
        from content.apps import public_media_store_check

        # The release image excludes the media tree from its build context, so both
        # deployed environments must read the published objects, not the filesystem.
        for environment in ("development", "production"):
            with self.subTest(environment=environment):
                with override_settings(ENVIRONMENT=environment, PUBLIC_MEDIA_STORE_BACKEND="local"):
                    self.assertEqual(
                        [error.id for error in public_media_store_check(None)], ["content.E004"]
                    )
                with override_settings(
                    ENVIRONMENT=environment, PUBLIC_MEDIA_STORE_BACKEND="memory"
                ):
                    self.assertEqual(
                        [error.id for error in public_media_store_check(None)], ["content.E004"]
                    )
                with override_settings(
                    ENVIRONMENT=environment,
                    PUBLIC_MEDIA_STORE_BACKEND="s3",
                    PUBLIC_MEDIA_S3_BUCKET="",
                ):
                    self.assertEqual(
                        [error.id for error in public_media_store_check(None)], ["content.E005"]
                    )
                with override_settings(
                    ENVIRONMENT=environment,
                    PUBLIC_MEDIA_STORE_BACKEND="s3",
                    PUBLIC_MEDIA_S3_BUCKET="release-assets",
                ):
                    self.assertEqual(public_media_store_check(None), [])

    def test_the_deployed_task_definition_wires_the_published_bucket(self) -> None:
        from deploy.task_definitions import FIXED_NONSECRET_ENVIRONMENT

        self.assertEqual(
            {
                name: FIXED_NONSECRET_ENVIRONMENT[name]
                for name in (
                    "PUBLIC_MEDIA_STORE_BACKEND",
                    "PUBLIC_MEDIA_S3_BUCKET",
                    "PUBLIC_MEDIA_S3_REGION",
                )
            },
            {
                "PUBLIC_MEDIA_STORE_BACKEND": "s3",
                "PUBLIC_MEDIA_S3_BUCKET": "dtc-website-media",
                "PUBLIC_MEDIA_S3_REGION": "eu-west-1",
            },
        )


class MediaToolingReachabilityTests(TestCase):
    def test_the_operator_commands_are_not_reachable_from_any_request_path(self) -> None:
        from django.urls import Resolver404, resolve

        for path in (
            "/public_media_hydrate",
            "/images/public_media_publish",
            "/studio/public_media_publish",
            "/api/v1/admin/public_media_verify",
        ):
            with self.subTest(path=path):
                try:
                    match = resolve(path)
                except Resolver404:
                    continue
                self.assertNotIn("public_media", match.func.__module__)

    def test_the_local_root_default_is_the_historic_projection_tree(self) -> None:
        from django.conf import settings

        configured = os.getenv("PUBLIC_MEDIA_LOCAL_ROOT")
        expected = (
            Path(configured)
            if configured
            else Path(settings.BASE_DIR) / "content" / "public_projection" / "media"
        )
        self.assertEqual(local_media_root(), expected)

    @requires_hydrated_tree
    def test_the_record_set_and_the_local_layout_agree(self) -> None:
        root = local_media_root()
        missing = [
            record["record_key"]
            for record in media_records()
            if not (root / record_relative_path(record)).is_file()
        ]
        self.assertEqual(missing, [])


class ImageBearingPageTests(TestCase):
    """The server-side half of the browser scenarios for image-bearing pages."""

    SCENARIO_PAGES = (
        ("/blog/ai-dev-tools-zoomcamp.html", "/images/posts/"),
        ("/people/aashishnair.html", "/images/authors/ aashishnair.jpg"),
        (
            "/books/20251006-software-development-at-rocket-speed.html",
            "/images/books/20251006-software-development-at-rocket-speed/preview.jpg",
        ),
        ("/blog", "/images/"),
        ("/podcast", "/images/"),
    )

    def test_each_scenario_page_renders_and_references_its_projection_image(self) -> None:
        for path, expected_reference in self.SCENARIO_PAGES:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(expected_reference, response.content.decode())

    def test_a_failing_store_never_breaks_the_surrounding_page(self) -> None:
        """Fault injection: the page still renders; only the image degrades."""

        store = _FailingStore(MediaStoreUnavailable("injected"))
        with patch("content.public_views.media_store", return_value=store):
            for path, _reference in self.SCENARIO_PAGES:
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    body = response.content.decode()
                    self.assertIn("</html>", body)
                    for leak in ("release-assets", "Traceback", "MediaStoreUnavailable"):
                        self.assertNotIn(leak, body)
            image = self.client.get(SPACED_AUTHOR_PATH)
            self.assertEqual(image.status_code, 502)
            self.assertEqual(image.headers["Cache-Control"], "no-store")
