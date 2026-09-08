"""Shared-graph import service tests (W3).

These exercise the schema-2 branch of the curriculum importer against the
versioned ``llm_zoomcamp_shared`` fixture -- the same known-output tree the
parser and the release-gate script consume.  Test data only.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from django.core.files.storage import default_storage
from django.test import TestCase

from content_sync.course_repository import parse_course_repository
from courses.models import (
    Cohort,
    CohortSharedModule,
    Course,
    Homework,
    SharedCurriculum,
    SharedCurriculumAsset,
    SharedLesson,
    SharedModule,
)
from courses.services.curriculum_import import (
    CurriculumImportCommand,
    CurriculumImportError,
    import_course_repository_curriculum,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "content_sync"
    / "tests"
    / "fixtures"
    / "course_repository"
    / "llm_zoomcamp_shared"
)
COMMIT_SHA = "c" * 40


def fixture_snapshot() -> dict[str, bytes]:
    return {
        path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file() and path.name != "expected-v2.json"
    }


def make_command(snapshot: dict[str, bytes], commit_sha: str = COMMIT_SHA):
    parsed = parse_course_repository(snapshot, commit_sha=commit_sha)
    return CurriculumImportCommand(
        source=parsed,
        source_uuid=uuid.uuid4(),
        source_stable_id="llm-zoomcamp",
        repository_owner="DataTalksClub",
        repository_name="llm-zoomcamp",
        repository_branch="main",
        commit_sha=commit_sha,
        snapshot=snapshot,
    )


class SharedCurriculumImportTests(TestCase):
    def import_fixture(self) -> None:
        import_course_repository_curriculum(make_command(fixture_snapshot()))

    def test_one_shared_graph_serves_both_deliveries(self) -> None:
        self.import_fixture()

        self.assertEqual(SharedModule.objects.count(), 1)
        self.assertEqual(SharedLesson.objects.count(), 2)
        self.assertEqual(SharedCurriculum.objects.count(), 1)

        live = CohortSharedModule.objects.filter(cohort__identifier="2026").get()
        self.assertEqual(live.position, 0)
        self.assertEqual(live.terminal_homework.slug, "hw1")
        self.assertEqual(live.terminal_homework.course.identifier, "2026")
        # The self-paced delivery binds the same shared module identity with
        # no homework of its own.
        self.assertEqual(
            CohortSharedModule.objects.filter(cohort__identifier="self-paced").count(),
            0,
        )
        course = Course.objects.get(slug="llm-zoomcamp")
        self.assertEqual(course.shared_curriculum.modules.count(), 1)
        cohort_2026 = live.cohort
        self.assertEqual(cohort_2026.curriculum_format, "shared")
        self.assertEqual(cohort_2026.delivery_mode, "live")
        self.assertEqual(cohort_2026.curriculum_source, "current")
        self.assertEqual(cohort_2026.shared_curriculum.course, course)

    def test_archive_cohort_gets_notice_url_and_null_module_homework_only(self) -> None:
        self.import_fixture()

        cohort_2025 = Cohort.objects.get(identifier="2025")
        self.assertEqual(cohort_2025.curriculum_source, "github_archive")
        self.assertEqual(cohort_2025.curriculum_format, "legacy")
        self.assertIsNone(cohort_2025.shared_curriculum)
        self.assertEqual(
            CohortSharedModule.objects.filter(cohort=cohort_2025).count(), 0
        )
        self.assertEqual(cohort_2025.archive_notice_path, "cohorts/2025/README.md")
        self.assertEqual(cohort_2025.archive_commit_sha, COMMIT_SHA)
        self.assertEqual(
            cohort_2025.archive_url,
            "https://github.com/DataTalksClub/llm-zoomcamp/blob/"
            + COMMIT_SHA
            + "/cohorts/2025/README.md",
        )
        # Only the explicitly mapped archive homework is registered; the
        # archive's opaque module/lesson tree created zero rows.
        self.assertTrue(
            Homework.objects.filter(
                course=cohort_2025, slug="hw-old"
            ).exists()
        )
        self.assertEqual(SharedLesson.objects.count(), 2)

    def test_relative_assets_are_stored_and_rewritten(self) -> None:
        self.import_fixture()

        lesson = SharedLesson.objects.get(slug="01-lesson")
        asset = SharedCurriculumAsset.objects.get(
            lesson=lesson, source_path="01-agentic-rag/images/architecture.svg"
        )
        self.assertIn(COMMIT_SHA, asset.storage_key)
        self.assertIn(str(lesson.source_content_id), asset.storage_key)
        self.assertTrue(asset.public_path.startswith("/course-assets/lessons/"))
        self.assertTrue(default_storage.exists(asset.storage_key))
        self.assertNotIn("images/", lesson.rendered_html)
        self.assertIn(asset.public_path, lesson.rendered_html)
        self.assertNotIn("github.com", lesson.rendered_html)

        code_asset = SharedCurriculumAsset.objects.get(
            lesson=lesson, source_path="01-agentic-rag/code/notebook.ipynb"
        )
        self.assertTrue(default_storage.exists(code_asset.storage_key))

    def test_editing_current_markdown_updates_the_one_shared_row(self) -> None:
        self.import_fixture()
        snapshot = fixture_snapshot()
        snapshot["01-agentic-rag/01-lesson.md"] = (
            b"---\nvideo_url: https://www.youtube.com/watch?v=fixture-intro\n---\n\n"
            b"# Introduction\n\nUpdated body.\n"
        )
        import_course_repository_curriculum(
            make_command(snapshot, commit_sha="d" * 40)
        )

        self.assertEqual(SharedLesson.objects.count(), 2)
        lesson = SharedLesson.objects.get(slug="01-lesson")
        self.assertIn("Updated body.", lesson.rendered_html)

    def test_identical_commit_replays_without_mutation(self) -> None:
        command = make_command(fixture_snapshot())
        first = import_course_repository_curriculum(command)
        second = import_course_repository_curriculum(command)

        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(SharedModule.objects.count(), 1)

    def test_slug_change_keeps_identity_and_requires_an_alias(self) -> None:
        self.import_fixture()
        snapshot = fixture_snapshot()
        for key in [key for key in snapshot if key.startswith("01-agentic-rag/")]:
            snapshot["01-renamed-rag/" + key.removeprefix("01-agentic-rag/")] = (
                snapshot.pop(key)
            )
        snapshot["01-renamed-rag/module.yaml"] = snapshot[
            "01-renamed-rag/module.yaml"
        ].replace(b"01-agentic-rag", b"01-renamed-rag")
        snapshot["cohorts/2026/cohort.yaml"] = snapshot[
            "cohorts/2026/cohort.yaml"
        ].replace(b"module: 01-agentic-rag", b"module: 01-renamed-rag")
        snapshot["cohorts/2026/cohort.yaml"] = snapshot[
            "cohorts/2026/cohort.yaml"
        ].replace(
            b"cohorts/2026/homework/01-agentic-rag/",
            b"cohorts/2026/homework/01-renamed-rag/",
        )
        snapshot["cohorts/2026/homework/01-renamed-rag/homework.yaml"] = snapshot.pop(
            "cohorts/2026/homework/01-agentic-rag/homework.yaml"
        )
        snapshot["cohorts/2026/homework/01-renamed-rag/homework.md"] = snapshot.pop(
            "cohorts/2026/homework/01-agentic-rag/homework.md"
        )

        import_course_repository_curriculum(
            make_command(snapshot, commit_sha="e" * 40)
        )

        # Same stable ID keeps its database identity; the old slug is gone and
        # an explicit CurriculumRouteAlias is the only bridge for old links.
        module = SharedModule.objects.get(
            source_content_id="22222222-2222-4222-8222-222222222222"
        )
        self.assertEqual(module.slug, "01-renamed-rag")

    def test_missing_asset_fails_atomically_leaving_previous_rows(self) -> None:
        self.import_fixture()
        snapshot = fixture_snapshot()
        del snapshot["01-agentic-rag/images/architecture.svg"]
        snapshot["01-agentic-rag/01-lesson.md"] = (
            b"---\nvideo_url: https://www.youtube.com/watch?v=fixture-intro\n---\n\n"
            b"# Introduction\n\nNow references a missing image: "
            b"![Architecture](images/architecture.svg)\n"
        )

        with self.assertRaises(CurriculumImportError) as raised:
            import_course_repository_curriculum(
                make_command(snapshot, commit_sha="f" * 40)
            )
        self.assertEqual(raised.exception.code, "shared_asset_reference_missing")
        # The previously imported current graph is intact.
        self.assertEqual(SharedModule.objects.count(), 1)
        self.assertEqual(SharedLesson.objects.count(), 2)
        lesson = SharedLesson.objects.get(slug="01-lesson")
        self.assertIn("fixture-intro", lesson.video_url)

    def test_module_removal_retires_instead_of_deleting(self) -> None:
        self.import_fixture()
        # A two-module source where the original module is dropped: it must
        # retire (stay as inactive rows), never disappear.
        snapshot = fixture_snapshot()
        snapshot["02-replacement/module.yaml"] = (
            snapshot["01-agentic-rag/module.yaml"]
            .replace(b"01-agentic-rag", b"02-replacement")
            .replace(
                b'content_id: "22222222-2222-4222-8222-222222222222"',
                b'content_id: "29999999-9999-4999-8999-999999999999"',
            )
            .replace(
                b'content_id: "33333333-3333-4333-8333-333333333333"',
                b'content_id: "38888888-8888-4888-8888-888888888888"',
            )
            .replace(
                b'content_id: "34444444-4444-4444-8444-444444444444"',
                b'content_id: "37777777-7777-4777-8777-777777777777"',
            )
        )
        for suffix in ("01-lesson.md", "02-practice.md", "README.md"):
            snapshot[f"02-replacement/{suffix}"] = snapshot[f"01-agentic-rag/{suffix}"]
        snapshot["02-replacement/images/architecture.svg"] = snapshot[
            "01-agentic-rag/images/architecture.svg"
        ]
        snapshot["02-replacement/code/notebook.ipynb"] = snapshot[
            "01-agentic-rag/code/notebook.ipynb"
        ]
        for key in [key for key in snapshot if key.startswith("01-agentic-rag/")]:
            snapshot.pop(key)
        snapshot["cohorts/2026/cohort.yaml"] = snapshot[
            "cohorts/2026/cohort.yaml"
        ].replace(b"module: 01-agentic-rag", b"module: 02-replacement")
        snapshot["cohorts/2026/cohort.yaml"] = snapshot[
            "cohorts/2026/cohort.yaml"
        ].replace(
            b"cohorts/2026/homework/01-agentic-rag/",
            b"cohorts/2026/homework/02-replacement/",
        )
        snapshot["cohorts/2026/homework/02-replacement/homework.yaml"] = snapshot.pop(
            "cohorts/2026/homework/01-agentic-rag/homework.yaml"
        )
        snapshot["cohorts/2026/homework/02-replacement/homework.md"] = snapshot.pop(
            "cohorts/2026/homework/01-agentic-rag/homework.md"
        )
        import_course_repository_curriculum(
            make_command(snapshot, commit_sha="2" * 40)
        )
        module = SharedModule.objects.get(
            source_content_id="22222222-2222-4222-8222-222222222222"
        )
        self.assertFalse(module.published)
        self.assertIsNotNone(module.retired_at)
        replacement = SharedModule.objects.get(
            source_content_id="29999999-9999-4999-8999-999999999999"
        )
        self.assertTrue(replacement.published)
        self.assertIsNone(replacement.retired_at)
