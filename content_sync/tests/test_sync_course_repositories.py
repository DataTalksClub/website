"""The pull entry point: registered sources, local checkouts, no network."""

from __future__ import annotations

import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from content.models import ContentSource
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE
from content_sync.tests.test_course_repository_transport_parity import (
    _git,
    build_checkout,
)
from courses.models import Cohort, Course, Homework, Module, Project
from scripts.prod.sync_course_repositories import (
    SyncCourseRepositoriesError,
    checkout_plan,
    select_sources,
)
from scripts.prod.sync_course_repositories import (
    pull as pull_sources,
)

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

    def pull(self, *, stable_ids: tuple[str, ...] = (), **kwargs: object) -> dict:
        checkouts = kwargs.pop("checkouts", {"llm-zoomcamp": self.checkout})
        root = kwargs.pop("root", None)
        sources = select_sources(stable_ids, explicit=checkouts, root=root)
        return pull_sources(sources=sources, checkouts=checkouts, root=root, **kwargs)  # type: ignore[arg-type]

    def seed_project_shell(self) -> None:
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

        sources = select_sources((), explicit={}, root=Path("/x"))
        plan = checkout_plan(sources, root=Path("/x"), explicit={})

        lines = [stable_id for stable_id, _repository, _branch, _target in plan]
        self.assertEqual(lines, ["data-engineering-zoomcamp", "llm-zoomcamp"])

    def test_no_registered_source_names_the_registration_command(self) -> None:
        with self.assertRaisesRegex(SyncCourseRepositoriesError, "register_course_repository"):
            select_sources((), explicit={}, root=None)

    def test_unknown_stable_id_is_refused(self) -> None:
        make_source()

        with self.assertRaisesRegex(
            SyncCourseRepositoriesError, "not registered or not enabled: nope"
        ):
            select_sources(("nope",), explicit={}, root=None)

    def test_pull_projects_the_checkout_without_any_network_call(self) -> None:
        make_source()
        self.seed_project_shell()

        output = self.pull()

        self.assertEqual(output["sources"][0]["transport"], "checkout")
        self.assertTrue(Module.objects.exists())

    def test_naming_one_checkout_names_the_run(self) -> None:
        """`--checkout X=PATH` alone must not attempt every other source."""

        make_source()
        make_source(
            id=uuid.uuid4(),
            stable_id="data-engineering-zoomcamp",
            display_name="Data Engineering Zoomcamp",
            repository_name="data-engineering-zoomcamp",
        )
        self.seed_project_shell()

        output = self.pull()

        self.assertEqual(len(output["sources"]), 1)
        self.assertEqual(output["sources"][0]["source_stable_id"], "llm-zoomcamp")

    def test_from_disk_keeps_an_explicit_checkout_as_an_override(self) -> None:
        """With a root there are many sources, and an explicit checkout overrides one."""

        make_source()
        make_source(
            id=uuid.uuid4(),
            stable_id="data-engineering-zoomcamp",
            display_name="Data Engineering Zoomcamp",
            repository_name="data-engineering-zoomcamp",
        )
        checkouts = {"llm-zoomcamp": self.checkout}

        sources = select_sources((), explicit=checkouts, root=SCRATCH_ROOT)
        plan = checkout_plan(sources, root=SCRATCH_ROOT, explicit=checkouts)

        self.assertEqual(
            [stable_id for stable_id, _repository, _branch, _target in plan],
            ["data-engineering-zoomcamp", "llm-zoomcamp"],
        )

    def test_a_waived_dirty_checkout_still_imports_the_commit(self) -> None:
        """The snapshot is `git archive HEAD`, so working-tree edits stay out."""

        make_source()
        self.seed_project_shell()
        (self.checkout / "cohorts" / "2026" / "cohort.yaml").write_text(
            (self.checkout / "cohorts" / "2026" / "cohort.yaml")
            .read_text(encoding="utf-8")
            .replace("title: LLM Zoomcamp 2026", "title: Tampered In The Working Tree"),
            encoding="utf-8",
        )
        warnings: list[str] = []

        self.pull(allow_modified_checkout=True, warn=warnings.append)

        self.assertTrue(any("NOT imported" in message for message in warnings))
        self.assertEqual(
            Cohort.objects.get(identifier="2026").title,
            "LLM Zoomcamp 2026",
        )

    def test_a_dirty_checkout_is_refused_and_names_the_change(self) -> None:
        make_source()
        (self.checkout / "course.yaml").write_text("slug: tampered\n", encoding="utf-8")

        with self.assertRaisesRegex(SyncCourseRepositoriesError, "uncommitted changes"):
            self.pull()

    def test_a_branch_mismatch_is_refused(self) -> None:
        make_source(branch="release")

        with self.assertRaisesRegex(SyncCourseRepositoriesError, "registered for 'release'"):
            self.pull()

    def test_a_missing_checkout_names_the_paths_it_looked_for(self) -> None:
        make_source()

        with self.assertRaisesRegex(SyncCourseRepositoriesError, "no checkout for llm-zoomcamp"):
            sources = select_sources((), explicit={}, root=SCRATCH_ROOT / "absent")
            pull_sources(sources=sources, root=SCRATCH_ROOT / "absent")

    def test_an_unowned_row_holding_the_repository_slug_is_refused(self) -> None:
        """One path means one adoption rule, and this is what it currently is.

        A homework nobody's import owns -- a row the CMP snapshot copied in, for
        instance -- that already carries the slug the repository declares is a
        refusal, not an adoption. The retired local importer adopted it, through
        ``preserve_existing_records``; the single path does not, and neither
        does the push route, so the local dataset and production behave the
        same way. Reconciling a CMP-owned row with a repository-owned one is the
        CMP importer's job (`courses/services/cmp_content_import.py` already
        pairs them), not something the course path should special-case for one
        caller. This test exists so that decision is met here rather than
        discovered during a rebuild.
        """

        make_source()
        self.seed_project_shell()
        Homework.objects.create(
            course=Cohort.objects.get(slug="llm-zoomcamp-2026"),
            slug="hw1",
            title="Homework 1, copied from CMP and owned by no import",
            due_date=timezone.now() + timedelta(days=3),
        )

        errors: list[str] = []
        with self.assertRaises(SyncCourseRepositoriesError):
            self.pull(warn=errors.append)

        self.assertTrue(
            any("course_repository_homework_slug_collision" in message for message in errors)
        )
        self.assertEqual(Homework.objects.get(slug="hw1").source_content_id, None)
