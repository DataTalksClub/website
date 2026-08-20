from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from django.test import TestCase, override_settings

from content.models import ContentSource
from content_sync.course_repository_sync import (
    import_course_repository_commit,
)
from content_sync.course_repository_webhook import (
    COURSE_REPOSITORY_ADAPTER_TYPE,
    COURSE_REPOSITORY_JOB_HANDLER,
    parse_github_course_push,
)
from courses.services.curriculum_import import CurriculumImportResult
from jobs.models import DurableJob


SECRET = "test-course-repository-webhook-secret"
COMMIT_SHA = "a" * 40
DELIVERY_ID = "delivery-course-1"
FIXTURE_ROOT = (
    Path(__file__).parents[2]
    / "content_sync"
    / "tests"
    / "fixtures"
    / "course_repository"
    / "llm_zoomcamp_2026"
)


def github_payload(*, ref: str = "refs/heads/main", deleted: bool = False) -> bytes:
    return json.dumps(
        {
            "ref": ref,
            "after": COMMIT_SHA,
            "deleted": deleted,
            "repository": {
                "full_name": "DataTalksClub/llm-zoomcamp",
                "name": "llm-zoomcamp",
                "owner": {"login": "DataTalksClub"},
            },
        },
        separators=(",", ":"),
    ).encode()


def signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class CourseRepositoryWebhookTests(TestCase):
    def setUp(self) -> None:
        self.source = ContentSource.objects.create(
            stable_id="llm-zoomcamp-source",
            display_name="LLM Zoomcamp repository",
            repository_owner="DataTalksClub",
            repository_name="llm-zoomcamp",
            branch="main",
            path_allowlist=["course.yaml", "cohorts/**"],
            adapter_type=COURSE_REPOSITORY_ADAPTER_TYPE,
            mount_path="/",
            enabled=True,
            max_files=5_000,
            max_bytes=100_000_000,
        )

    def post(self, body: bytes, *, delivery_id: str = DELIVERY_ID, event: str = "push"):
        return self.client.post(
            "/api/webhooks/github",
            data=body,
            content_type="application/json",
            HTTP_X_GITHUB_EVENT=event,
            HTTP_X_GITHUB_DELIVERY=delivery_id,
            HTTP_X_HUB_SIGNATURE_256=signature(body),
        )

    @override_settings(COURSE_REPOSITORY_WEBHOOK_SECRET=SECRET)
    def test_valid_push_fences_delivery_and_enqueues_only_safe_identifiers(self) -> None:
        response = self.post(github_payload())

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["outcome"], "accepted")
        job = DurableJob.objects.get(handler=COURSE_REPOSITORY_JOB_HANDLER)
        self.assertEqual(
            set(job.payload),
            {"source_uuid", "commit_sha", "delivery_record_id"},
        )
        self.assertEqual(job.payload["source_uuid"], str(self.source.id))
        self.assertEqual(job.payload["commit_sha"], COMMIT_SHA)
        UUID(job.payload["delivery_record_id"])
        self.assertNotIn("DataTalksClub", json.dumps(job.payload))

    @override_settings(COURSE_REPOSITORY_WEBHOOK_SECRET=SECRET)
    def test_exact_delivery_replay_does_not_create_a_second_job(self) -> None:
        first = self.post(github_payload())
        second = self.post(github_payload())

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.json()["outcome"], "replayed")
        self.assertEqual(DurableJob.objects.count(), 1)

    @override_settings(COURSE_REPOSITORY_WEBHOOK_SECRET=SECRET)
    def test_delivery_id_conflict_does_not_enqueue_or_mutate(self) -> None:
        self.assertEqual(self.post(github_payload()).status_code, 202)
        changed = github_payload().replace(b'"after":"' + COMMIT_SHA.encode(), b'"after":"' + ("b" * 40).encode())

        response = self.post(changed)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(DurableJob.objects.count(), 1)

    @override_settings(COURSE_REPOSITORY_WEBHOOK_SECRET=SECRET)
    def test_signature_is_verified_before_json_and_unregistered_sources_are_rejected(self) -> None:
        malformed = b"not-json"
        response = self.post(malformed)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DurableJob.objects.count(), 0)

        self.source.delete()
        response = self.post(github_payload(), delivery_id="delivery-unregistered")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(DurableJob.objects.count(), 0)

    @override_settings(COURSE_REPOSITORY_WEBHOOK_SECRET=SECRET)
    def test_invalid_push_metadata_is_rejected_without_a_fence(self) -> None:
        for index, body in enumerate(
            (
                github_payload(ref="refs/tags/v1"),
                github_payload(deleted=True),
            )
        ):
            with self.subTest(index=index):
                response = self.post(body, delivery_id=f"delivery-invalid-{index}")
                self.assertEqual(response.status_code, 400)
        self.assertEqual(DurableJob.objects.count(), 0)

    def test_push_parser_requires_consistent_repository_identity(self) -> None:
        payload = json.loads(github_payload())
        payload["repository"]["owner"]["login"] = "other"

        with self.assertRaisesRegex(ValueError, "github_repository_invalid"):
            parse_github_course_push(payload, event_type="push")

    @patch("content_sync.course_repository_sync.import_course_repository_curriculum")
    @patch("content_sync.course_repository_sync.fetch_course_repository_snapshot")
    def test_worker_fetches_exact_commit_and_calls_course_import(
        self,
        fetch_snapshot,
        import_curriculum,
    ) -> None:
        snapshot = {
            path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
            for path in FIXTURE_ROOT.rglob("*")
            if path.is_file()
        }
        fetch_snapshot.return_value = snapshot

        import_course_repository_commit(
            None,
            {
                "source_uuid": str(self.source.id),
                "commit_sha": "a" * 40,
                "delivery_record_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            },
        )

        fetch_snapshot.assert_called_once_with(
            owner="DataTalksClub",
            repository="llm-zoomcamp",
            commit_sha="a" * 40,
            max_files=5_000,
            max_bytes=100_000_000,
        )
        import_curriculum.assert_called_once()
        command = import_curriculum.call_args.args[0]
        self.assertEqual(command.source_uuid, self.source.id)
        self.assertEqual(command.commit_sha, "a" * 40)

