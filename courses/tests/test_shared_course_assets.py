"""Serving of managed shared-curriculum lesson assets."""

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from courses.models import (
    Course,
    SharedCurriculum,
    SharedCurriculumAsset,
    SharedLesson,
    SharedModule,
)

SHA = "a" * 40
CHECKSUM = "b" * 64
LESSON_ID = "33333333-3333-4333-8333-333333333333"
BYTES_CHECKSUM = "c" * 64
STORAGE_KEY = (
    f"shared-lesson/{LESSON_ID}/{SHA}/{BYTES_CHECKSUM}/architecture.svg"
)
PUBLIC_PATH = f"/course-assets/lessons/{LESSON_ID}/{BYTES_CHECKSUM}/architecture.svg"


def make_asset() -> SharedCurriculumAsset:
    course = Course.objects.create(slug="asset-family", title="Asset Family")
    shared = SharedCurriculum.objects.create(
        course=course,
        parser_version="course-repository-v2",
        source_content_id="11111111-1111-4111-8111-111111111111",
        source_path="course.yaml",
        source_commit_sha=SHA,
        source_checksum="d" * 64,
    )
    module = SharedModule.objects.create(
        curriculum=shared,
        position=0,
        slug="01-agentic-rag",
        title="Agentic RAG",
        source_content_id="22222222-2222-4222-8222-222222222222",
        source_path="01-agentic-rag/module.yaml",
        source_commit_sha=SHA,
        source_checksum="d" * 64,
    )
    lesson = SharedLesson.objects.create(
        module=module,
        position=0,
        slug="01-lesson",
        title="Introduction",
        source_content_id=LESSON_ID,
        source_path="01-agentic-rag/01-lesson.md",
        source_commit_sha=SHA,
        source_checksum="d" * 64,
    )
    default_storage.save(STORAGE_KEY, ContentFile(b"<svg></svg>\n"))
    return SharedCurriculumAsset.objects.create(
        lesson=lesson,
        source_path="01-agentic-rag/images/architecture.svg",
        public_path=PUBLIC_PATH,
        storage_key=STORAGE_KEY,
        content_type="image/svg+xml",
        byte_size=12,
        source_commit_sha=SHA,
        source_checksum="d" * 64,
    )


class SharedCourseAssetViewTests(TestCase):
    def test_asset_serves_row_bytes_with_immutable_cache(self) -> None:
        asset = make_asset()

        response = self.client.get(asset.public_path)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"<svg></svg>\n")
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn("immutable", response["Cache-Control"])
        self.assertIn("public", response["Cache-Control"])

    def test_head_is_allowed_and_post_is_not(self) -> None:
        asset = make_asset()

        self.assertEqual(self.client.head(asset.public_path).status_code, 200)
        self.assertEqual(self.client.post(asset.public_path).status_code, 405)

    def test_unknown_or_malformed_paths_are_real_404s(self) -> None:
        make_asset()

        # Well-formed path with no row: 404, never a fallback.
        missing = PUBLIC_PATH.replace(BYTES_CHECKSUM, "e" * 64)
        self.assertEqual(self.client.get(missing).status_code, 404)

        # A checksum segment that is not a sha256 hex string never matches
        # the route at all.
        malformed = PUBLIC_PATH.replace(BYTES_CHECKSUM, "not-a-checksum")
        self.assertEqual(self.client.get(malformed).status_code, 404)

        # Traversal-shaped filenames cannot reach the filesystem.
        traversal = f"/course-assets/lessons/{LESSON_ID}/{BYTES_CHECKSUM}/..%2f..%2fsecret"
        self.assertEqual(self.client.get(traversal).status_code, 404)

    def test_deleted_row_stops_serving(self) -> None:
        asset = make_asset()
        asset.delete()

        self.assertEqual(self.client.get(PUBLIC_PATH).status_code, 404)
