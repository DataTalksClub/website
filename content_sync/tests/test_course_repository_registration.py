"""Which repositories exist is registered data, and a fresh database can get it.

Before this existed, `ContentSource` rows had to be typed in by hand on every
machine, so `make content-pull` and the production-prep rebuild both failed on a
new checkout with "no enabled course-repository sources are registered".  The
pinned input closes that without becoming a second source of truth: it only
creates rows, never rewrites one, and every reader still reads the table.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from content.models import ContentSource
from content_sync.course_repository_registration import (
    CourseRepositoryRegistrationError,
    load_registration_input,
)
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE
from scripts.prod.sync_course_repositories import checkout_plan, select_sources
from scripts.prod.sync_course_repository_sources import (
    SyncCourseRepositorySourcesError,
)
from scripts.prod.sync_course_repository_sources import (
    sync as sync_course_repository_sources,
)


class RegistrationInputTests(SimpleTestCase):
    def test_the_pinned_input_names_only_repositories_that_can_be_ingested(self) -> None:
        registrations = load_registration_input()

        self.assertEqual(
            sorted(registration.stable_id for registration in registrations),
            ["ai-dev-tools-zoomcamp", "llm-zoomcamp", "ml-zoomcamp"],
        )
        for registration in registrations:
            with self.subTest(stable_id=registration.stable_id):
                self.assertEqual(registration.repository_owner, "DataTalksClub")
                self.assertEqual(registration.branch, "main")

    def write_input(self, payload: object) -> Path:
        scratch = Path(settings.BASE_DIR) / ".tmp" / "course-repository-registration"
        scratch.mkdir(parents=True, exist_ok=True)
        directory = self.enterContext(tempfile.TemporaryDirectory(dir=scratch))
        path = Path(directory) / "sources.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_a_missing_input_is_a_bounded_refusal(self) -> None:
        with self.assertRaises(CourseRepositoryRegistrationError):
            load_registration_input(Path("does-not-exist.json"))

    def test_an_unexpected_field_is_refused_rather_than_ignored(self) -> None:
        path = self.write_input(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "stable_id": "x",
                        "display_name": "X",
                        "repository_owner": "DataTalksClub",
                        "repository_name": "x",
                        "branch": "main",
                        "max_files": "9999",
                    }
                ],
            }
        )

        with self.assertRaises(CourseRepositoryRegistrationError):
            load_registration_input(path)

    def test_a_repeated_stable_id_is_refused(self) -> None:
        record = {
            "stable_id": "x",
            "display_name": "X",
            "repository_owner": "DataTalksClub",
            "repository_name": "x",
            "branch": "main",
        }
        path = self.write_input({"schema_version": 1, "sources": [record, dict(record)]})

        with self.assertRaises(CourseRepositoryRegistrationError):
            load_registration_input(path)


class CourseRepositoryRegistrationTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stdout = io.StringIO()

    def test_management_command_registers_disabled_source_by_default(self) -> None:
        call_command(
            "register_course_repository",
            stable_id="llm-zoomcamp-source",
            display_name="LLM Zoomcamp repository",
            owner="DataTalksClub",
            repository="llm-zoomcamp",
            stdout=self.stdout,
        )

        source = ContentSource.objects.get(stable_id="llm-zoomcamp-source")
        self.assertFalse(source.enabled)
        self.assertEqual(source.adapter_type, COURSE_REPOSITORY_ADAPTER_TYPE)
        self.assertEqual(source.repository_owner, "DataTalksClub")


class SeedCourseRepositorySourcesTests(TestCase):
    def seed(self) -> list[dict[str, object]]:
        return sync_course_repository_sources()["sources"]  # type: ignore[return-value]

    def test_seeding_registers_every_pinned_source(self) -> None:
        report = self.seed()

        registered = ContentSource.objects.filter(
            adapter_type=COURSE_REPOSITORY_ADAPTER_TYPE, enabled=True
        )
        self.assertEqual(registered.count(), 3)
        self.assertTrue(all(entry["created"] for entry in report))
        self.assertEqual(
            sorted(registered.values_list("repository_name", flat=True)),
            ["ai-dev-tools-zoomcamp", "llm-zoomcamp", "machine-learning-zoomcamp"],
        )

    def test_seeding_twice_changes_nothing(self) -> None:
        self.seed()
        before = sorted(ContentSource.objects.values_list("id", "stable_id", "revision"))

        report = self.seed()

        self.assertFalse(any(entry["created"] for entry in report))
        self.assertEqual(
            sorted(ContentSource.objects.values_list("id", "stable_id", "revision")),
            before,
        )

    def test_a_registered_source_is_never_rewritten(self) -> None:
        """An operator who repointed a source meant it; a seed must not undo it."""

        self.seed()
        source = ContentSource.objects.get(stable_id="llm-zoomcamp")
        source.repository_name = "llm-zoomcamp-fork"
        source.save(update_fields=["repository_name"])

        self.seed()

        source.refresh_from_db()
        self.assertEqual(source.repository_name, "llm-zoomcamp-fork")

    def test_the_seeded_rows_are_what_the_pull_plan_reads(self) -> None:
        self.seed()

        sources = select_sources((), explicit={}, root=None)
        plan = checkout_plan(sources, root=Path("/x"), explicit={})

        self.assertEqual(
            [stable_id for stable_id, _repository, _branch, _target in plan],
            ["ai-dev-tools-zoomcamp", "llm-zoomcamp", "ml-zoomcamp"],
        )

    def test_an_unreadable_input_is_a_bounded_refusal(self) -> None:
        with self.assertRaises(SyncCourseRepositorySourcesError):
            sync_course_repository_sources(
                registration_input=Path("nowhere/course_repository_sources.json")
            )
