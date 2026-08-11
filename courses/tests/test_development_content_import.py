import io
import json
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.bootstrap import RuntimeEnvironment
from core.models import IdempotencyRecord
from courses.models import Course
from courses.services.development_content_import import (
    APPROVED_SCHEMA_CHECKSUM,
    ArtifactContract,
    DevelopmentContentImportError,
    _reset_imported_sequences,
    _target_schema,
    import_development_course_content,
    schema_contract_checksum,
    semantic_dataset_checksum,
)
from courses.services.development_content_transport import downloaded_s3_artifact
from review_import.manifest import ALLOWLIST, COPY_ORDER
from review_import.workflow import (
    AllowedDataset,
    _logical_checksum,
    _relationship_evidence,
)


def _single_course_dataset() -> AllowedDataset:
    values = {
        "id": 101,
        "slug": "imported-course",
        "title": "Imported course",
        "description": "Approved public course content.",
        "start_date": None,
        "end_date": None,
        "registration_url": "",
        "github_repo_url": "",
        "social_media_hashtag": "",
        "first_homework_scored": 0,
        "finished": 0,
        "faq_document_url": "",
        "min_projects_to_pass": 1,
        "homework_problems_comments_field": 0,
        "project_passing_score": 0,
        "visible": 1,
    }
    rows = {table: [] for table in COPY_ORDER}
    rows["courses_course"] = [tuple(values[column] for column in ALLOWLIST["courses_course"])]
    counts = {table: len(table_rows) for table, table_rows in rows.items()}
    relationships = _relationship_evidence(rows)
    return AllowedDataset(
        rows=rows,
        counts=counts,
        relationships=relationships,
        logical_checksum=_logical_checksum(rows),
    )


def _contract(dataset: AllowedDataset) -> ArtifactContract:
    return ArtifactContract(
        source_sha256="a" * 64,
        source_size=1,
        logical_checksum=dataset.logical_checksum,
        semantic_checksum=semantic_dataset_checksum(dataset),
        counts=dataset.counts,
        relationships=dataset.relationships,
    )


@override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST)
class DevelopmentContentImportTests(TestCase):
    def test_target_schema_covers_actual_non_app_registry_tables(self) -> None:
        schema = _target_schema()

        self.assertIn("django_migrations", schema)
        self.assertGreaterEqual(schema["django_migrations"], {"id", "app", "name", "applied"})

    def test_schema_contract_checksum_is_frozen(self) -> None:
        self.assertEqual(schema_contract_checksum(), APPROVED_SCHEMA_CHECKSUM)

    def test_atomic_import_preserves_private_rows_and_replays_without_writes(self) -> None:
        dataset = _single_course_dataset()
        contract = _contract(dataset)
        user = get_user_model().objects.create_user(
            username="existing-user",
            email="existing@example.invalid",
        )

        with (
            patch(
                "courses.services.development_content_import._load_artifact",
                return_value=dataset,
            ),
            patch(
                "courses.services.development_content_import._reset_imported_sequences",
                wraps=_reset_imported_sequences,
            ) as reset_sequences,
        ):
            first = import_development_course_content(
                Path("unused"),
                contract=contract,
                allow_test_environment=True,
            )
            second = import_development_course_content(
                Path("unused"),
                contract=contract,
                allow_test_environment=True,
            )

        reset_sequences.assert_called_once_with()
        self.assertTrue(first.imported)
        self.assertFalse(first.replayed)
        self.assertFalse(second.imported)
        self.assertTrue(second.replayed)
        self.assertTrue(first.sensitive_tables_preserved)
        self.assertEqual(Course.objects.get(pk=101).slug, "imported-course")
        next_course = Course.objects.create(
            slug="post-import-course",
            title="Post-import course",
            description="Sequence portability check.",
        )
        self.assertGreater(next_course.pk, 101)
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_partial_or_different_target_is_refused_without_receipt(self) -> None:
        dataset = _single_course_dataset()
        contract = _contract(dataset)
        Course.objects.create(
            slug="different-course",
            title="Different course",
            description="Existing target content.",
        )

        with (
            patch(
                "courses.services.development_content_import._load_artifact",
                return_value=dataset,
            ),
            self.assertRaisesRegex(
                DevelopmentContentImportError,
                "target-not-empty-or-exact",
            ),
        ):
            import_development_course_content(
                Path("unused"),
                contract=contract,
                allow_test_environment=True,
            )

        self.assertFalse(IdempotencyRecord.objects.exists())
        self.assertEqual(Course.objects.get().slug, "different-course")

    def test_replay_still_refuses_target_drift(self) -> None:
        dataset = _single_course_dataset()
        contract = _contract(dataset)
        with patch(
            "courses.services.development_content_import._load_artifact",
            return_value=dataset,
        ):
            import_development_course_content(
                Path("unused"),
                contract=contract,
                allow_test_environment=True,
            )
            Course.objects.filter(pk=101).update(title="Drifted title")
            with self.assertRaisesRegex(
                DevelopmentContentImportError,
                "target-content-drift",
            ):
                import_development_course_content(
                    Path("unused"),
                    contract=contract,
                    allow_test_environment=True,
                )

    @override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.PRODUCTION)
    def test_production_environment_is_always_refused(self) -> None:
        with self.assertRaisesRegex(
            DevelopmentContentImportError,
            "environment-not-development",
        ):
            import_development_course_content(Path("unused"))


class DevelopmentContentTransportTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(
            prefix="issue-128-transport-",
            dir=scratch,
        )
        self.addCleanup(temporary.cleanup)
        self.temporary_root = Path(temporary.name)
        self.staging_root = self.temporary_root / "runtime-tmp"
        self.staging_root.mkdir(mode=0o700)
        staging_patch = patch(
            "courses.services.development_content_transport._EPHEMERAL_STAGING_ROOT",
            self.staging_root,
        )
        staging_patch.start()
        self.addCleanup(staging_patch.stop)

    def _assert_staging_root_empty(self) -> None:
        self.assertEqual(list(self.staging_root.iterdir()), [])

    def _client(self, body: Mock, *, encryption: str = "aws:kms") -> Mock:
        metadata = {
            "ContentLength": 3,
            "ServerSideEncryption": encryption,
            "SSEKMSKeyId": "approved-key",
        }
        client = Mock()
        client.head_object.return_value = metadata
        client.get_object.return_value = {**metadata, "Body": body}
        return client

    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_version_pinned_download_is_private_and_always_removed(
        self,
        boto3_client: Mock,
    ) -> None:
        body = Mock()
        body.iter_chunks.return_value = [b"a", b"bc"]
        client = self._client(body)
        boto3_client.return_value = client

        with downloaded_s3_artifact(
            bucket="private-bucket",
            key="private-key",
            version_id="immutable-version",
            expected_bucket_owner="123456789012",
            expected_kms_key_arn="approved-key",
        ) as artifact:
            self.assertEqual(artifact.read_bytes(), b"abc")
            self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(artifact.parent.stat().st_mode), 0o700)
            self.assertEqual(artifact.parent.parent, self.staging_root)
            downloaded_path = artifact
            staging_directory = artifact.parent

        self.assertFalse(downloaded_path.exists())
        self.assertFalse(staging_directory.exists())
        self._assert_staging_root_empty()
        body.close.assert_called_once_with()
        expected_request = {
            "Bucket": "private-bucket",
            "Key": "private-key",
            "VersionId": "immutable-version",
            "ExpectedBucketOwner": "123456789012",
        }
        client.head_object.assert_called_once_with(**expected_request)
        client.get_object.assert_called_once_with(**expected_request)

    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_readonly_application_tree_does_not_control_private_staging(
        self,
        boto3_client: Mock,
    ) -> None:
        if os.geteuid() == 0:
            self.skipTest("non-root filesystem semantics require a non-root test process")
        application_tree = self.temporary_root / "readonly-app"
        application_tree.mkdir(mode=0o500)
        injected_root = self.temporary_root / "injected-tmpdir"
        injected_root.mkdir(mode=0o700)
        body = Mock()
        body.iter_chunks.return_value = [b"abc"]
        boto3_client.return_value = self._client(body)
        captured: dict[str, Path] = {}

        def import_staged_artifact(path: Path) -> SimpleNamespace:
            captured["path"] = path
            captured["directory"] = path.parent
            self.assertEqual(path.read_bytes(), b"abc")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(path.parent.parent, self.staging_root)
            return SimpleNamespace(
                counts={},
                imported=True,
                logical_checksum="logical",
                relationships={},
                replayed=False,
                sensitive_tables_preserved=True,
                source_sha256="source",
            )

        output = io.StringIO()
        with (
            override_settings(BASE_DIR=application_tree),
            patch.dict(os.environ, {"TMPDIR": str(injected_root)}),
            patch(
                "courses.management.commands.import_development_course_content."
                "import_development_course_content",
                side_effect=import_staged_artifact,
            ),
            patch(
                "courses.management.commands.import_development_course_content."
                "delete_s3_artifact_version"
            ) as delete_version,
        ):
            call_command(
                "import_development_course_content",
                "--s3-bucket",
                "private-bucket",
                "--s3-key",
                "private-key",
                "--s3-version-id",
                "immutable-version",
                "--expected-bucket-owner",
                "123456789012",
                "--expected-kms-key-arn",
                "approved-key",
                stdout=output,
            )

        self.assertTrue(json.loads(output.getvalue())["transport_deleted"])
        delete_version.assert_called_once_with(
            bucket="private-bucket",
            key="private-key",
            version_id="immutable-version",
            expected_bucket_owner="123456789012",
        )
        self.assertFalse(captured["path"].exists())
        self.assertFalse(captured["directory"].exists())
        self.assertEqual(list(injected_root.iterdir()), [])
        self._assert_staging_root_empty()
        body.close.assert_called_once_with()

    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_symlink_staging_root_is_rejected_without_following(
        self,
        boto3_client: Mock,
    ) -> None:
        target = self.temporary_root / "symlink-target"
        target.mkdir(mode=0o700)
        symlink = self.temporary_root / "runtime-tmp-symlink"
        symlink.symlink_to(target, target_is_directory=True)
        body = Mock()
        body.iter_chunks.return_value = [b"abc"]
        boto3_client.return_value = self._client(body)

        with (
            patch(
                "courses.services.development_content_transport._EPHEMERAL_STAGING_ROOT",
                symlink,
            ),
            self.assertRaisesRegex(
                DevelopmentContentImportError,
                "transport-local-storage-failed",
            ),
        ):
            with downloaded_s3_artifact(
                bucket="private-bucket",
                key="private-key",
                version_id="immutable-version",
                expected_bucket_owner="123456789012",
                expected_kms_key_arn="approved-key",
            ):
                self.fail("a symlink staging root must not be followed")

        self.assertEqual(list(target.iterdir()), [])
        body.close.assert_called_once_with()
        self._assert_staging_root_empty()

    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_shared_root_without_sticky_bit_is_rejected(
        self,
        boto3_client: Mock,
    ) -> None:
        shared_root = self.temporary_root / "shared-runtime-tmp"
        shared_root.mkdir(mode=0o777)
        shared_root.chmod(0o777)
        body = Mock()
        body.iter_chunks.return_value = [b"abc"]
        boto3_client.return_value = self._client(body)

        with (
            patch(
                "courses.services.development_content_transport._EPHEMERAL_STAGING_ROOT",
                shared_root,
            ),
            self.assertRaisesRegex(
                DevelopmentContentImportError,
                "transport-local-storage-failed",
            ),
        ):
            with downloaded_s3_artifact(
                bucket="private-bucket",
                key="private-key",
                version_id="immutable-version",
                expected_bucket_owner="123456789012",
                expected_kms_key_arn="approved-key",
            ):
                self.fail("an untrusted shared root must not be used")

        self.assertEqual(list(shared_root.iterdir()), [])
        body.close.assert_called_once_with()
        self._assert_staging_root_empty()

    @patch("courses.services.development_content_transport.os.chmod")
    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_setup_failure_removes_created_private_directory(
        self,
        boto3_client: Mock,
        chmod: Mock,
    ) -> None:
        chmod.side_effect = NotImplementedError("local detail")
        body = Mock()
        body.iter_chunks.return_value = [b"abc"]
        boto3_client.return_value = self._client(body)

        with self.assertRaisesRegex(
            DevelopmentContentImportError,
            "transport-local-storage-failed",
        ):
            with downloaded_s3_artifact(
                bucket="private-bucket",
                key="private-key",
                version_id="immutable-version",
                expected_bucket_owner="123456789012",
                expected_kms_key_arn="approved-key",
            ):
                self.fail("failed private setup must not yield an artifact")

        self._assert_staging_root_empty()
        body.close.assert_called_once_with()

    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_preexisting_staged_file_symlink_is_not_followed_and_is_cleaned(
        self,
        boto3_client: Mock,
    ) -> None:
        sentinel = self.temporary_root / "sentinel"
        sentinel.write_bytes(b"unchanged")
        forced_directory = self.staging_root / "dtc-course-content-forced"
        forced_directory.mkdir(mode=0o700)
        (forced_directory / "artifact.sqlite3").symlink_to(sentinel)
        body = Mock()
        body.iter_chunks.return_value = [b"abc"]
        boto3_client.return_value = self._client(body)

        with (
            patch(
                "courses.services.development_content_transport.tempfile.mkdtemp",
                return_value=str(forced_directory),
            ),
            self.assertRaisesRegex(
                DevelopmentContentImportError,
                "transport-local-storage-failed",
            ),
        ):
            with downloaded_s3_artifact(
                bucket="private-bucket",
                key="private-key",
                version_id="immutable-version",
                expected_bucket_owner="123456789012",
                expected_kms_key_arn="approved-key",
            ):
                self.fail("a preexisting staged symlink must not be followed")

        self.assertEqual(sentinel.read_bytes(), b"unchanged")
        self.assertFalse(forced_directory.exists())
        self._assert_staging_root_empty()
        body.close.assert_called_once_with()

    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_consumer_failure_still_removes_private_staging(
        self,
        boto3_client: Mock,
    ) -> None:
        body = Mock()
        body.iter_chunks.return_value = [b"abc"]
        boto3_client.return_value = self._client(body)

        with self.assertRaisesRegex(RuntimeError, "import failed"):
            with downloaded_s3_artifact(
                bucket="private-bucket",
                key="private-key",
                version_id="immutable-version",
                expected_bucket_owner="123456789012",
                expected_kms_key_arn="approved-key",
            ) as artifact:
                staged_path = artifact
                staging_directory = artifact.parent
                raise RuntimeError("import failed")

        self.assertFalse(staged_path.exists())
        self.assertFalse(staging_directory.exists())
        self._assert_staging_root_empty()
        body.close.assert_called_once_with()

    @patch("courses.services.development_content_transport.os.fsync")
    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_local_write_failure_is_sanitized_and_cleans_private_staging(
        self,
        boto3_client: Mock,
        fsync: Mock,
    ) -> None:
        fsync.side_effect = OSError("local detail")
        body = Mock()
        body.iter_chunks.return_value = [b"abc"]
        boto3_client.return_value = self._client(body)

        with self.assertRaisesRegex(
            DevelopmentContentImportError,
            "transport-local-storage-failed",
        ):
            with downloaded_s3_artifact(
                bucket="private-bucket",
                key="private-key",
                version_id="immutable-version",
                expected_bucket_owner="123456789012",
                expected_kms_key_arn="approved-key",
            ):
                self.fail("a failed fsync must not yield an artifact")

        self._assert_staging_root_empty()
        body.close.assert_called_once_with()

    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_invalid_download_metadata_closes_body_without_creating_artifact(
        self,
        boto3_client: Mock,
    ) -> None:
        body = Mock()
        client = self._client(body)
        client.get_object.return_value["ServerSideEncryption"] = "AES256"
        boto3_client.return_value = client

        with self.assertRaisesRegex(
            DevelopmentContentImportError,
            "transport-not-kms-encrypted",
        ):
            with downloaded_s3_artifact(
                bucket="private-bucket",
                key="private-key",
                version_id="immutable-version",
                expected_bucket_owner="123456789012",
                expected_kms_key_arn="approved-key",
            ):
                self.fail("invalid transport metadata must not yield an artifact")

        body.close.assert_called_once_with()
        self._assert_staging_root_empty()

    @patch("courses.services.development_content_transport.APPROVED_SOURCE_SIZE", 3)
    @patch("boto3.client")
    def test_stream_failure_is_sanitized_and_removes_artifact(
        self,
        boto3_client: Mock,
    ) -> None:
        body = Mock()
        body.iter_chunks.side_effect = RuntimeError("provider detail")
        boto3_client.return_value = self._client(body)

        with self.assertRaisesRegex(
            DevelopmentContentImportError,
            "transport-download-failed",
        ):
            with downloaded_s3_artifact(
                bucket="private-bucket",
                key="private-key",
                version_id="immutable-version",
                expected_bucket_owner="123456789012",
                expected_kms_key_arn="approved-key",
            ):
                self.fail("failed transport must not yield an artifact")

        body.close.assert_called_once_with()
        self._assert_staging_root_empty()
