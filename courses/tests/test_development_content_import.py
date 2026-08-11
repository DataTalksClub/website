import stat
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
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
            downloaded_path = artifact

        self.assertFalse(downloaded_path.exists())
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
