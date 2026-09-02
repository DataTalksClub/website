"""Focused tests for the pluggable public media store backends."""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from content.media_store import (
    DEFAULT_MAX_OBJECT_BYTES,
    LocalMediaStore,
    MediaChecksumMismatch,
    MediaObjectMissing,
    MediaObjectTooLarge,
    MediaRecordInvalid,
    MediaStoreUnavailable,
    MemoryMediaStore,
    S3MediaStore,
    deterministic_fixture,
    media_records,
    media_store,
    object_key,
    read_media_object,
    record_filename,
    record_key,
    record_relative_path,
)

SPACED_AUTHOR_KEY = "images/authors/ aashishnair.jpg"
SPACED_PODCAST_KEY = (
    "images/podcast/production-ml-search-vector-search-embeddings-hybrid search.jpg"
)


def _record(key: str = "images/authors/example.jpg", *, payload: bytes = b"fixture") -> dict:
    return {
        "content_type": "image/jpeg",
        "record_key": key,
        "public_path": f"/{key}",
        "slug": key,
        "provenance": {"checksum": hashlib.sha256(payload).hexdigest()},
    }


class RecordDerivationTests(SimpleTestCase):
    def test_the_object_key_is_path_mirrored_below_the_prefix(self) -> None:
        self.assertEqual(
            object_key(_record(SPACED_AUTHOR_KEY), prefix="public-projection"),
            "public-projection/images/authors/ aashishnair.jpg",
        )
        self.assertEqual(
            object_key(_record(SPACED_PODCAST_KEY), prefix="public-projection/"),
            f"public-projection/{SPACED_PODCAST_KEY}",
        )

    def test_the_key_ignores_any_request_supplied_path(self) -> None:
        """A crafted request path cannot influence the derived key."""

        store = S3MediaStore(
            bucket="bucket",
            prefix="public-projection",
            region="",
            endpoint_url="",
            timeout_seconds=1.0,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        record = _record("images/authors/example.jpg")
        for crafted in (
            "../../manage.py",
            "..%2f..%2fmanage.py",
            "/etc/passwd",
            "authors/example.jpg?x=1",
        ):
            with self.subTest(crafted=crafted):
                self.assertEqual(
                    store.key_for({**record, "requested_path": crafted}),
                    "public-projection/images/authors/example.jpg",
                )

    def test_unsafe_or_absent_record_keys_fail_closed(self) -> None:
        for key in (
            "authors/example.jpg",
            "images/../manage.py",
            "images//example.jpg",
            "/images/example.jpg",
            "images\\example.jpg",
            "",
        ):
            with self.subTest(key=key), self.assertRaises(MediaRecordInvalid):
                record_key({"record_key": key})
        with self.assertRaises(MediaRecordInvalid):
            record_key({})

    def test_the_disposition_filename_keeps_the_literal_space(self) -> None:
        self.assertEqual(record_filename(_record(SPACED_AUTHOR_KEY)), " aashishnair.jpg")
        self.assertEqual(
            record_filename(_record(SPACED_PODCAST_KEY)),
            "production-ml-search-vector-search-embeddings-hybrid search.jpg",
        )

    def test_the_local_relative_path_drops_the_images_segment(self) -> None:
        self.assertEqual(
            record_relative_path(_record(SPACED_AUTHOR_KEY)), "authors/ aashishnair.jpg"
        )


class LocalMediaStoreTests(SimpleTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _store(self, maximum: int = DEFAULT_MAX_OBJECT_BYTES) -> LocalMediaStore:
        return LocalMediaStore(root=self.root, maximum_object_bytes=maximum)

    def _write(self, relative: str, payload: bytes) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def test_a_present_object_is_returned_and_verified(self) -> None:
        self._write("authors/ aashishnair.jpg", b"fixture")
        record = _record(SPACED_AUTHOR_KEY)
        self.assertEqual(read_media_object(self._store(), record), b"fixture")

    def test_an_absent_object_is_reported_as_missing(self) -> None:
        with self.assertRaises(MediaObjectMissing):
            self._store().fetch(_record())

    def test_a_symlinked_object_fails_closed(self) -> None:
        self._write("authors/real.jpg", b"fixture")
        link = self.root / "authors" / "example.jpg"
        link.symlink_to(self.root / "authors" / "real.jpg")
        with self.assertRaises(MediaStoreUnavailable):
            self._store().fetch(_record())

    def test_an_oversized_object_fails_closed(self) -> None:
        self._write("authors/example.jpg", b"x" * 64)
        with self.assertRaises(MediaObjectTooLarge):
            self._store(maximum=8).fetch(_record())

    def test_a_corrupted_object_fails_the_checksum(self) -> None:
        self._write("authors/example.jpg", b"tampered")
        with self.assertRaises(MediaChecksumMismatch):
            read_media_object(self._store(), _record())

    def test_existing_keys_are_reported_with_the_record_namespace(self) -> None:
        self._write("authors/example.jpg", b"fixture")
        self._write("podcast/other.png", b"fixture")
        self.assertEqual(
            self._store().existing_keys(),
            ("images/authors/example.jpg", "images/podcast/other.png"),
        )


class MemoryMediaStoreTests(SimpleTestCase):
    def test_every_checked_record_resolves_to_a_verified_fixture(self) -> None:
        store = MemoryMediaStore()
        records = media_records()
        self.assertEqual(len(records), 1_253)
        for record in records:
            payload = read_media_object(store, record)
            self.assertTrue(payload)

    def test_fixtures_are_valid_images_of_the_recorded_content_type(self) -> None:
        signatures = {
            "image/jpeg": b"\xff\xd8\xff",
            "image/png": b"\x89PNG\r\n\x1a\n",
            "image/gif": b"GIF89a",
            "image/svg+xml": b"<svg",
        }
        seen = set()
        for record in media_records():
            content_type = record["content_type"]
            seen.add(content_type)
            if content_type in signatures and content_type not in ("image/jpeg", "image/png"):
                self.assertTrue(deterministic_fixture(record).startswith(signatures[content_type]))
        for content_type in ("image/jpeg", "image/png"):
            sample = next(r for r in media_records() if r["content_type"] == content_type)
            self.assertTrue(deterministic_fixture(sample).startswith(signatures[content_type]))
        self.assertEqual(seen, set(signatures))

    def test_the_jpeg_fixture_keeps_a_valid_marker_chain(self) -> None:
        """The comment segment must sit on a marker boundary after the JFIF APP0.

        A mis-sliced base64 literal or a wrong insertion offset produces bytes that a
        decoder still half-accepts, so walk the marker chain explicitly.
        """

        from content.media_store import _BASE_JPEG, _JPEG_COMMENT_OFFSET

        self.assertEqual(len(_BASE_JPEG), 160)
        self.assertEqual(_BASE_JPEG[:2], b"\xff\xd8")
        self.assertEqual(_BASE_JPEG[2:4], b"\xff\xe0")
        self.assertEqual(_JPEG_COMMENT_OFFSET, 4 + int.from_bytes(_BASE_JPEG[4:6], "big"))

        sample = next(r for r in media_records() if r["content_type"] == "image/jpeg")
        payload = deterministic_fixture(sample)
        markers = []
        index = 2
        while index < len(payload):
            self.assertEqual(payload[index], 0xFF, f"marker chain broke at {index}")
            marker = payload[index + 1]
            markers.append(marker)
            if marker == 0xDA:  # start of scan; the rest is entropy coded
                break
            index += 2 + int.from_bytes(payload[index + 2 : index + 4], "big")
        self.assertIn(0xFE, markers)
        self.assertEqual(markers[0], 0xE0)
        self.assertEqual(markers[1], 0xFE)
        self.assertEqual(markers[-1], 0xDA)
        self.assertEqual(payload[-2:], b"\xff\xd9")

    def test_fixtures_are_deterministic_and_record_specific(self) -> None:
        first = media_records()[0]
        second = next(
            record for record in media_records() if record["record_key"] != first["record_key"]
        )
        self.assertEqual(deterministic_fixture(first), deterministic_fixture(first))
        self.assertNotEqual(deterministic_fixture(first), deterministic_fixture(second))

    def test_the_offline_store_refuses_to_serve_production(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="memory", ENVIRONMENT="production"):
            with self.assertRaises(ImproperlyConfigured):
                media_store()


class _StubBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.closed = False

    def read(self, size: int | None = None) -> bytes:
        if size is None:
            return self._payload
        return self._payload[:size]

    def close(self) -> None:
        self.closed = True


class _StubClientError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class S3MediaStoreTests(SimpleTestCase):
    def _store(self, **overrides: Any) -> S3MediaStore:
        options: dict[str, Any] = {
            "bucket": "release-assets",
            "prefix": "public-projection",
            "region": "eu-west-1",
            "endpoint_url": "",
            "timeout_seconds": 2.0,
            "maximum_object_bytes": DEFAULT_MAX_OBJECT_BYTES,
        }
        options.update(overrides)
        return S3MediaStore(**options)

    def test_an_unset_bucket_is_refused(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            self._store(bucket="")

    @patch("boto3.client")
    def test_a_stored_object_is_read_verified_and_keyed_from_the_record(
        self, boto3_client: Mock
    ) -> None:
        payload = b"fixture"
        client = Mock()
        client.get_object.return_value = {
            "Body": _StubBody(payload),
            "ContentLength": len(payload),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(payload).digest()).decode(),
        }
        boto3_client.return_value = client
        store = self._store()
        self.assertEqual(read_media_object(store, _record(SPACED_AUTHOR_KEY)), payload)
        client.get_object.assert_called_once_with(
            Bucket="release-assets",
            Key="public-projection/images/authors/ aashishnair.jpg",
            ChecksumMode="ENABLED",
        )
        configuration = boto3_client.call_args.kwargs["config"]
        self.assertEqual(configuration.connect_timeout, 2.0)
        self.assertEqual(configuration.read_timeout, 2.0)
        # One initial attempt plus at most one bounded retry.
        self.assertEqual(configuration.retries["max_attempts"], 2)

    @patch("boto3.client")
    def test_an_absent_object_maps_to_missing(self, boto3_client: Mock) -> None:
        client = Mock()
        client.get_object.side_effect = _StubClientError("NoSuchKey", 404)
        boto3_client.return_value = client
        with self.assertRaises(MediaObjectMissing):
            self._store().fetch(_record())

    @patch("boto3.client")
    def test_a_timeout_maps_to_an_unavailable_store(self, boto3_client: Mock) -> None:
        client = Mock()
        client.get_object.side_effect = TimeoutError("read timeout")
        boto3_client.return_value = client
        with self.assertRaises(MediaStoreUnavailable):
            self._store().fetch(_record())

    @patch("boto3.client")
    def test_a_client_error_maps_to_an_unavailable_store(self, boto3_client: Mock) -> None:
        client = Mock()
        client.get_object.side_effect = _StubClientError("AccessDenied", 403)
        boto3_client.return_value = client
        with self.assertRaises(MediaStoreUnavailable):
            self._store().fetch(_record())

    @patch("boto3.client")
    def test_a_declared_oversize_object_is_never_downloaded(self, boto3_client: Mock) -> None:
        body = _StubBody(b"x" * 4096)
        client = Mock()
        client.get_object.return_value = {"Body": body, "ContentLength": 4096}
        boto3_client.return_value = client
        with self.assertRaises(MediaObjectTooLarge):
            self._store(maximum_object_bytes=64).fetch(_record())
        self.assertTrue(body.closed)

    @patch("boto3.client")
    def test_an_undeclared_oversize_body_is_bounded(self, boto3_client: Mock) -> None:
        client = Mock()
        client.get_object.return_value = {"Body": _StubBody(b"x" * 4096)}
        boto3_client.return_value = client
        with self.assertRaises(MediaObjectTooLarge):
            self._store(maximum_object_bytes=64).fetch(_record())

    @patch("boto3.client")
    def test_a_store_attested_digest_must_match_the_returned_bytes(
        self, boto3_client: Mock
    ) -> None:
        payload = b"fixture"
        client = Mock()
        client.get_object.return_value = {
            "Body": _StubBody(payload),
            "ContentLength": len(payload),
            "ChecksumSHA256": base64.b64encode(b"\x00" * 32).decode(),
        }
        boto3_client.return_value = client
        with self.assertRaises(MediaChecksumMismatch):
            read_media_object(self._store(), _record(payload=payload))

    @patch("boto3.client")
    def test_errors_never_render_the_bucket_key_or_endpoint(self, boto3_client: Mock) -> None:
        client = Mock()
        client.get_object.side_effect = _StubClientError("AccessDenied", 403)
        boto3_client.return_value = client
        try:
            self._store().fetch(_record(SPACED_AUTHOR_KEY))
        except MediaStoreUnavailable as error:
            message = f"{error}{error.__cause__}{error.__context__}"
            self.assertNotIn("release-assets", message)
            self.assertNotIn("public-projection", message)
            self.assertNotIn("aashishnair", message)
        else:  # pragma: no cover - defensive
            self.fail("expected the store to fail closed")


class MediaStoreSelectionTests(SimpleTestCase):
    def test_the_default_backend_is_the_credential_free_local_store(self) -> None:
        """``base`` defaults to ``local``; only the environment can select another one."""

        self.assertEqual(
            settings.PUBLIC_MEDIA_STORE_BACKEND,
            os.getenv("PUBLIC_MEDIA_STORE_BACKEND", "local").strip().lower(),
        )
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="local"):
            self.assertIsInstance(media_store(), LocalMediaStore)

    def test_neither_credential_free_backend_constructs_an_aws_client(self) -> None:
        with patch("boto3.client", side_effect=AssertionError("no client")) as client:
            for backend in ("local", "memory"):
                with override_settings(PUBLIC_MEDIA_STORE_BACKEND=backend):
                    media_store()
        client.assert_not_called()

    def test_an_unknown_backend_is_refused(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="ftp"):
            with self.assertRaises(ImproperlyConfigured):
                media_store()

    def test_each_supported_backend_is_selectable(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="memory"):
            self.assertIsInstance(media_store(), MemoryMediaStore)
        with override_settings(
            PUBLIC_MEDIA_STORE_BACKEND="s3", PUBLIC_MEDIA_S3_BUCKET="release-assets"
        ):
            self.assertIsInstance(media_store(), S3MediaStore)

    def test_the_s3_backend_requires_a_bucket(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="s3", PUBLIC_MEDIA_S3_BUCKET=""):
            with self.assertRaises(ImproperlyConfigured):
                media_store()
