"""The pull entry point: registered sources, local checkouts, no network."""

from __future__ import annotations

import io
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from content.models import ContentSource
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE
from content_sync.tests.test_course_repository_transport_parity import (
    _git,
    build_checkout,
)
from courses.models import Cohort, Course, Module, Project

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_ROOT = PROJECT_ROOT / ".tmp" / "pull-course-repositories-command"


def make_source(**overrides: object) -> ContentSource:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "stable_id": "llm-zoomcamp",
        "display_name": "LLM Zoomcamp",
        "repository_owner": "DataTalksClub",
        "repository_name": "llm-zoomcamp",
        "branch": "main",
        "adapter_type": COURSE_REPOSITORY_ADAPTER_TYPE,
        "mount_path": "/",
        "enabled": True,
        "max_files": 5_000,
        "max_bytes": 100_000_000,
    }
    values.update(overrides)
    return ContentSource.objects.create(**values)


class PullCourseRepositoriesCommandTests(TestCase):
    checkout: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        cls.checkout = SCRATCH_ROOT / "llm-zoomcamp"
        build_checkout(cls.checkout)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(SCRATCH_ROOT, ignore_errors=True)
        super().tearDownClass()

    def tearDown(self) -> None:
        _git(self.checkout, "checkout", "--", ".")

    def pull(self, *arguments: str) -> str:
        out = io.StringIO()
        call_command(
            "pull_course_repositories",
            "--checkout",
            f"llm-zoomcamp={self.checkout}",
            *arguments,
            stdout=out,
            stderr=io.StringIO(),
        )
        return out.getvalue()

    def test_the_repository_list_is_registered_data_not_a_hardcoded_list(self) -> None:
        make_source()
        make_source(
            id=uuid.uuid4(),
            stable_id="data-engineering-zoomcamp",
            display_name="Data Engineering Zoomcamp",
            repository_name="data-engineering-zoomcamp",
        )
        make_source(
            id=uuid.uuid4(),
            stable_id="disabled-zoomcamp",
            display_name="Disabled",
            repository_name="disabled-zoomcamp",
            enabled=False,
        )
        out = io.StringIO()

        call_command("pull_course_repositories", "--checkout-plan", "--from-disk", "/x", stdout=out)

        lines = [line.split("\t")[0] for line in out.getvalue().splitlines()]
        self.assertEqual(lines, ["data-engineering-zoomcamp", "llm-zoomcamp"])

    def test_no_registered_source_names_the_registration_command(self) -> None:
        with self.assertRaisesRegex(CommandError, "register_course_repository"):
            call_command("pull_course_repositories", "--checkout-plan")

    def test_unknown_stable_id_is_refused(self) -> None:
        make_source()

        with self.assertRaisesRegex(CommandError, "not registered or not enabled: nope"):
            call_command("pull_course_repositories", "--checkout-plan", "--stable-id", "nope")

    def test_pull_projects_the_checkout_without_any_network_call(self) -> None:
        make_source()
        course = Course.objects.create(slug="llm-zoomcamp", title="LLM Zoomcamp")
        cohort = Cohort.objects.create(
            course=course,
            slug="llm-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="LLM Zoomcamp 2026",
            description="Existing shell for the project reference.",
        )
        now = timezone.now()
        Project.objects.create(
            course=cohort,
            slug="project-01",
            title="Project 1",
            submission_due_date=now + timedelta(days=7),
            peer_review_due_date=now + timedelta(days=14),
        )

        output = self.pull()

        self.assertIn('"transport": "checkout"', output)
        self.assertTrue(Module.objects.exists())

    def test_a_dirty_checkout_is_refused_and_names_the_change(self) -> None:
        make_source()
        (self.checkout / "course.yaml").write_text("slug: tampered\n", encoding="utf-8")

        with self.assertRaisesRegex(CommandError, "uncommitted changes"):
            self.pull()

    def test_a_branch_mismatch_is_refused(self) -> None:
        make_source(branch="release")

        with self.assertRaisesRegex(CommandError, "registered for 'release'"):
            self.pull()

    def test_a_missing_checkout_names_the_paths_it_looked_for(self) -> None:
        make_source()

        with self.assertRaisesRegex(CommandError, "no checkout for llm-zoomcamp"):
            call_command(
                "pull_course_repositories",
                "--from-disk",
                str(SCRATCH_ROOT / "absent"),
                stdout=io.StringIO(),
            )
