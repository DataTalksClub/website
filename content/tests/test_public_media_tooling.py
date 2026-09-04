"""Focused tests for the hydrate / publish / verify operator commands."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from content.media_store import (
    DEFAULT_MAX_OBJECT_BYTES,
    LocalMediaStore,
    MediaObjectMissing,
    MediaObjectStat,
    S3MediaStore,
)
from content.media_tooling import (
    MediaToolingError,
    hydrate_media,
    parse_checkout_arguments,
    publish_media,
    verify_media,
)
from scripts.prod.sync_public_media_hydrate import main as hydrate_main
from scripts.prod.sync_public_media_publish import main as publish_main
from scripts.prod.sync_public_media_verify import main as verify_main

CONTENT_REPOSITORY = "DataTalksClub/content"
LEGACY_REPOSITORY = "DataTalksClub/datatalksclub.github.io"


def _record(
    key: str,
    payload: bytes,
    *,
    repository: str = CONTENT_REPOSITORY,
    source_path: str | None = None,
) -> dict[str, Any]:
    return {
        "content_type": "image/jpeg",
        "record_key": key,
        "public_path": f"/{key}",
        "slug": key,
        "provenance": {
            "checksum": hashlib.sha256(payload).hexdigest(),
            "repository": repository,
            "revision": "0" * 40,
            "source_path": source_path or key,
        },
    }


class CheckoutArgumentTests(SimpleTestCase):
    def test_pairs_are_parsed(self) -> None:
        parsed = parse_checkout_arguments([f"{CONTENT_REPOSITORY}=/tmp/content"])
        self.assertEqual(parsed, {CONTENT_REPOSITORY: Path("/tmp/content")})

    def test_a_malformed_pair_is_refused(self) -> None:
        for value in ("no-separator", "=/tmp/content", f"{CONTENT_REPOSITORY}="):
            with self.subTest(value=value), self.assertRaises(MediaToolingError):
                parse_checkout_arguments([value])


class HydrateTests(SimpleTestCase):
    def setUp(self) -> None:
        self.destination = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.checkout = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _seed_checkout(self, key: str, payload: bytes) -> None:
        target = self.checkout / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def _hydrate(self, records: list[dict[str, Any]], **overrides: Any):
        options: dict[str, Any] = {
            "destination_root": self.destination,
            "source": "checkout",
            "checkouts": {CONTENT_REPOSITORY: self.checkout},
            "maximum_object_bytes": DEFAULT_MAX_OBJECT_BYTES,
            "records": records,
        }
        options.update(overrides)
        return hydrate_media(**options)

    def test_objects_are_materialised_and_verified(self) -> None:
        self._seed_checkout("images/authors/ aashishnair.jpg", b"portrait")
        record = _record("images/authors/ aashishnair.jpg", b"portrait")
        report = self._hydrate([record])
        self.assertEqual((report.total, report.written, report.failed), (1, 1, 0))
        self.assertEqual(
            (self.destination / "authors" / " aashishnair.jpg").read_bytes(), b"portrait"
        )

    def test_hydration_is_idempotent_and_resumable(self) -> None:
        self._seed_checkout("images/a.jpg", b"one")
        self._seed_checkout("images/b.jpg", b"two")
        records = [_record("images/a.jpg", b"one"), _record("images/b.jpg", b"two")]
        first = self._hydrate(records)
        self.assertEqual((first.written, first.skipped), (2, 0))
        second = self._hydrate(records)
        self.assertEqual((second.written, second.skipped), (0, 2))
        (self.destination / "b.jpg").unlink()
        third = self._hydrate(records)
        self.assertEqual((third.written, third.skipped), (1, 1))

    def test_a_mismatching_object_is_never_written(self) -> None:
        self._seed_checkout("images/a.jpg", b"tampered")
        report = self._hydrate([_record("images/a.jpg", b"one")])
        self.assertEqual((report.written, report.failed), (0, 1))
        self.assertFalse((self.destination / "a.jpg").exists())
        self.assertEqual(report.failures, ["images/a.jpg"])

    def test_a_missing_checkout_is_reported_not_silently_skipped(self) -> None:
        record = _record("images/a.jpg", b"one", repository=LEGACY_REPOSITORY)
        report = self._hydrate([record])
        self.assertEqual((report.written, report.failed), (0, 1))

    def test_an_oversized_source_object_is_refused(self) -> None:
        self._seed_checkout("images/a.jpg", b"x" * 4096)
        report = self._hydrate([_record("images/a.jpg", b"x" * 4096)], maximum_object_bytes=64)
        self.assertEqual((report.written, report.failed), (0, 1))

    def test_a_source_path_escaping_the_checkout_is_refused(self) -> None:
        record = _record("images/a.jpg", b"one", source_path="../outside.jpg")
        report = self._hydrate([record])
        self.assertEqual((report.written, report.failed), (0, 1))

    def test_hydration_from_a_peer_store_needs_no_network(self) -> None:
        """A second checkout hydrates from an already-hydrated peer root."""

        peer = Path(self.enterContext(tempfile.TemporaryDirectory()))
        records = []
        for index in range(5):
            payload = f"object-{index}".encode()
            (peer / f"a{index}.jpg").write_bytes(payload)
            records.append(_record(f"images/a{index}.jpg", payload))
        store = LocalMediaStore(root=peer, maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES)
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            report = hydrate_media(
                destination_root=self.destination,
                source="store",
                store=store,
                maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
                records=records,
            )
        self.assertEqual((report.total, report.written, report.failed), (5, 5, 0))
        self.assertEqual((self.destination / "a3.jpg").read_bytes(), b"object-3")

    def test_the_command_reports_its_counts_and_fails_on_any_failure(self) -> None:
        self._seed_checkout("images/a.jpg", b"one")
        stream = StringIO()
        with override_settings(PUBLIC_MEDIA_LOCAL_ROOT=self.destination):
            with patch(
                "content.media_tooling.media_records",
                return_value=(_record("images/a.jpg", b"one"),),
            ):
                with contextlib.redirect_stdout(stream):
                    exit_code = hydrate_main(
                        [
                            "--source",
                            "checkout",
                            "--checkout",
                            f"{CONTENT_REPOSITORY}={self.checkout}",
                        ]
                    )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stream.getvalue())["written"], 1)
        with override_settings(PUBLIC_MEDIA_LOCAL_ROOT=self.destination):
            with patch(
                "content.media_tooling.media_records",
                return_value=(_record("images/missing.jpg", b"one"),),
            ):
                with contextlib.redirect_stdout(StringIO()):
                    exit_code = hydrate_main(
                        [
                            "--source",
                            "checkout",
                            "--checkout",
                            f"{CONTENT_REPOSITORY}={self.checkout}",
                        ]
                    )
        self.assertEqual(exit_code, 1)

    def test_the_command_never_reaches_the_network_for_offline_sources(self) -> None:
        self._seed_checkout("images/a.jpg", b"one")
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")) as opened:
            self._hydrate([_record("images/a.jpg", b"one")])
        opened.assert_not_called()


class _StubS3Client:
    def __init__(self, existing: dict[str, bytes] | None = None) -> None:
        self.objects = dict(existing or {})
        self.puts: list[dict[str, Any]] = []

    def head_object(self, **request: Any) -> dict[str, Any]:
        key = request["Key"]
        if key not in self.objects:
            raise _StubClientError("404", 404)
        payload = self.objects[key]
        return {
            "ContentLength": len(payload),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(payload).digest()).decode(),
        }

    def put_object(self, **request: Any) -> dict[str, Any]:
        self.puts.append(request)
        self.objects[request["Key"]] = request["Body"]
        return {}

    def list_objects_v2(self, **request: Any) -> dict[str, Any]:
        prefix = request.get("Prefix", "")
        return {
            "Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)],
            "IsTruncated": False,
        }


class _StubClientError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class PublishTests(SimpleTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.s3_client = _StubS3Client()
        self.store = S3MediaStore(
            bucket="release-assets",
            prefix="public-projection",
            region="",
            endpoint_url="",
            timeout_seconds=1.0,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        self.store._cached_client = self.s3_client

    def _write(self, relative: str, payload: bytes) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def _publish(self, records: list[dict[str, Any]], **overrides: Any):
        options: dict[str, Any] = {
            "source_root": self.root,
            "store": self.store,
            "maximum_object_bytes": DEFAULT_MAX_OBJECT_BYTES,
            "records": records,
        }
        options.update(overrides)
        return publish_media(**options)

    def test_recorded_objects_are_uploaded_with_type_and_checksum(self) -> None:
        self._write("authors/ aashishnair.jpg", b"portrait")
        record = _record("images/authors/ aashishnair.jpg", b"portrait")
        report = self._publish([record])
        self.assertEqual((report.added, report.skipped, report.failed), (1, 0, 0))
        self.assertEqual(len(self.s3_client.puts), 1)
        put = self.s3_client.puts[0]
        self.assertEqual(put["Bucket"], "release-assets")
        self.assertEqual(put["Key"], "public-projection/images/authors/ aashishnair.jpg")
        self.assertEqual(put["ContentType"], "image/jpeg")
        self.assertEqual(put["ChecksumAlgorithm"], "SHA256")

    def test_an_already_matching_object_is_skipped(self) -> None:
        self._write("a.jpg", b"one")
        record = _record("images/a.jpg", b"one")
        self.assertEqual(self._publish([record]).added, 1)
        second = self._publish([record])
        self.assertEqual((second.added, second.skipped, second.changed), (0, 1, 0))
        self.assertEqual(len(self.s3_client.puts), 1)

    def test_a_changed_object_is_re_uploaded(self) -> None:
        self._write("a.jpg", b"one")
        self.s3_client.objects["public-projection/images/a.jpg"] = b"stale"
        report = self._publish([_record("images/a.jpg", b"one")])
        self.assertEqual((report.added, report.changed), (0, 1))

    def test_an_orphan_file_is_reported_and_never_uploaded(self) -> None:
        self._write("a.jpg", b"one")
        self._write("podcast/s24e06-orphan.jpg", b"orphan")
        report = self._publish([_record("images/a.jpg", b"one")])
        self.assertEqual(report.orphan, 1)
        self.assertEqual(report.orphans, ["images/podcast/s24e06-orphan.jpg"])
        self.assertEqual(
            [put["Key"] for put in self.s3_client.puts], ["public-projection/images/a.jpg"]
        )

    def test_a_local_object_that_fails_its_checksum_is_never_uploaded(self) -> None:
        self._write("a.jpg", b"tampered")
        report = self._publish([_record("images/a.jpg", b"one")])
        self.assertEqual((report.failed, report.added), (1, 0))
        self.assertEqual(self.s3_client.puts, [])

    def test_a_dry_run_uploads_nothing(self) -> None:
        self._write("a.jpg", b"one")
        report = self._publish([_record("images/a.jpg", b"one")], dry_run=True)
        self.assertEqual(report.added, 1)
        self.assertEqual(self.s3_client.puts, [])

    def test_the_command_refuses_a_non_object_store_backend(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="local"):
            with contextlib.redirect_stdout(StringIO()):
                exit_code = publish_main([])
        self.assertEqual(exit_code, 1)


class VerifyTests(SimpleTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _write(self, relative: str, payload: bytes) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def _local_store(self) -> LocalMediaStore:
        return LocalMediaStore(root=self.root, maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES)

    def test_a_matching_local_root_verifies_clean(self) -> None:
        self._write("a.jpg", b"one")
        report = verify_media(store=self._local_store(), records=[_record("images/a.jpg", b"one")])
        self.assertTrue(report.clean)
        self.assertEqual((report.total, report.matched), (1, 1))

    def test_missing_mismatched_and_extra_objects_are_reported(self) -> None:
        self._write("b.jpg", b"tampered")
        self._write("extra.jpg", b"extra")
        report = verify_media(
            store=self._local_store(),
            records=[_record("images/a.jpg", b"one"), _record("images/b.jpg", b"two")],
        )
        self.assertFalse(report.clean)
        self.assertEqual(report.missing, ["images/a.jpg"])
        self.assertEqual(report.mismatched, ["images/b.jpg"])
        self.assertEqual(report.extra, ["images/extra.jpg"])

    def test_the_stubbed_object_store_is_verified_by_head_only(self) -> None:
        client = _StubS3Client({"public-projection/images/a.jpg": b"one"})
        store = S3MediaStore(
            bucket="release-assets",
            prefix="public-projection",
            region="",
            endpoint_url="",
            timeout_seconds=1.0,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        store._cached_client = client
        report = verify_media(store=store, records=[_record("images/a.jpg", b"one")])
        self.assertTrue(report.clean)
        self.assertEqual(report.matched, 1)

    def test_the_stubbed_object_store_reports_an_extra_object(self) -> None:
        client = _StubS3Client(
            {
                "public-projection/images/a.jpg": b"one",
                "public-projection/images/orphan.jpg": b"orphan",
            }
        )
        store = S3MediaStore(
            bucket="release-assets",
            prefix="public-projection",
            region="",
            endpoint_url="",
            timeout_seconds=1.0,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        store._cached_client = client
        report = verify_media(store=store, records=[_record("images/a.jpg", b"one")])
        self.assertEqual(report.extra, ["images/orphan.jpg"])
        self.assertFalse(report.clean)

    def test_an_unrelated_bucket_section_is_not_reported_as_an_orphan(self) -> None:
        """With no prefix the media shares the bucket root with other sections.

        The listing is scoped to the record-key prefix, so a sibling section such
        as ``site-assets/`` is outside the projection's namespace and must not be
        counted as an orphaned object.
        """

        client = _StubS3Client(
            {
                "images/a.jpg": b"one",
                "site-assets/logo.svg": b"<svg/>",
                "site-assets/illustrations/hero.png": b"hero",
            }
        )
        store = S3MediaStore(
            bucket="dtc-website-media",
            prefix="",
            region="",
            endpoint_url="",
            timeout_seconds=1.0,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        store._cached_client = client
        self.assertEqual(store.listing_prefix(), "images/")
        report = verify_media(store=store, records=[_record("images/a.jpg", b"one")])
        self.assertEqual(report.extra, [])
        self.assertTrue(report.clean)
        # A genuine orphan inside the projection namespace is still caught.
        client.objects["images/orphan.jpg"] = b"orphan"
        follow_up = verify_media(store=store, records=[_record("images/a.jpg", b"one")])
        self.assertEqual(follow_up.extra, ["images/orphan.jpg"])

    def test_the_command_exits_non_zero_on_any_difference(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with override_settings(
                PUBLIC_MEDIA_STORE_BACKEND="local", PUBLIC_MEDIA_LOCAL_ROOT=Path(empty)
            ):
                with contextlib.redirect_stdout(StringIO()):
                    exit_code = verify_main([])
        self.assertEqual(exit_code, 1)

    def test_the_offline_store_verifies_the_whole_record_set(self) -> None:
        with override_settings(PUBLIC_MEDIA_STORE_BACKEND="memory"):
            stream = StringIO()
            with contextlib.redirect_stdout(stream):
                exit_code = verify_main([])
            self.assertEqual(exit_code, 0)
            report = json.loads(stream.getvalue())
        self.assertEqual(report["missing_count"], 0)
        self.assertEqual(report["mismatched_count"], 0)
        self.assertEqual(report["extra_count"], 0)


class StoreStatTests(SimpleTestCase):
    def test_a_missing_stat_is_reported_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            store = LocalMediaStore(root=Path(empty), maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES)
            with self.assertRaises(MediaObjectMissing):
                store.stat(_record("images/a.jpg", b"one"))

    @patch("boto3.client")
    def test_a_store_without_a_server_side_digest_falls_back_to_the_bytes(
        self, boto3_client: Mock
    ) -> None:
        payload = b"one"
        client = Mock()
        client.head_object.return_value = {"ContentLength": len(payload)}
        client.get_object.return_value = {
            "Body": _StubBody(payload),
            "ContentLength": len(payload),
        }
        boto3_client.return_value = client
        store = S3MediaStore(
            bucket="release-assets",
            prefix="public-projection",
            region="",
            endpoint_url="",
            timeout_seconds=1.0,
            maximum_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
        )
        stat = store.stat(_record("images/a.jpg", payload))
        self.assertEqual(
            stat, MediaObjectStat(size=len(payload), checksum=hashlib.sha256(payload).hexdigest())
        )


class _StubBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, size: int | None = None) -> bytes:
        return self._payload if size is None else self._payload[:size]

    def close(self) -> None:
        return None
