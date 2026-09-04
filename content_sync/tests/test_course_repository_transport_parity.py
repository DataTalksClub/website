"""Push and pull are one implementation, so they must produce one result.

The push route (CI/CD -> signed webhook -> durable job -> commit archive) had
never fired in production, so nothing had ever compared it against the pull
route a developer actually runs.  These tests run *one* repository through both
transports and assert the snapshot and the projected rows are identical.  A
change that teaches one route something the other does not know fails here.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from content.models import ContentSource
from content_sync.course_repository import DEFAULT_LIMITS
from content_sync.course_repository_ingest import (
    CourseRepositoryFetchError,
    course_repository_limits,
    read_course_repository_checkout,
)
from content_sync.course_repository_sync import (
    fetch_course_repository_snapshot,
    import_course_repository_commit,
)
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE
from courses.models import (
    Cohort,
    Course,
    CourseCurriculumImportRun,
    CurriculumFlowItem,
    Homework,
    Module,
    Project,
    Question,
    Unit,
)
from jobs.registry import JobContext

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "course_repository"
FIXTURE_ROOT = FIXTURE_DIR / "llm_zoomcamp_2026"
EXPORT_IGNORE_OVERLAY = FIXTURE_DIR / "export_ignore_overlay"
SCRATCH_ROOT = PROJECT_ROOT / ".tmp" / "course-repository-transport-parity"
STABLE_ID = "llm-zoomcamp"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout


def build_checkout(root: Path, *, overlay: Path | None = None) -> str:
    """Materialise the fixture repository as a real, clean git checkout.

    ``overlay`` copies extra committed files over the fixture, which is how a
    checkout that really carries a ``.gitattributes`` is built without a second
    copy of every fixture file.
    """

    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(FIXTURE_ROOT, root)
    if overlay is not None:
        shutil.copytree(overlay, root, dirs_exist_ok=True)
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.email", "parity@example.invalid")
    _git(root, "config", "user.name", "Transport Parity")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "--message", "fixture")
    return _git(root, "rev-parse", "HEAD").strip()


def codeload_archive(root: Path, commit_sha: str) -> bytes:
    """Byte-for-byte the shape codeload serves: a prefixed ``git archive``."""

    completed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "archive",
            "--format=tar.gz",
            f"--prefix=llm-zoomcamp-{commit_sha[:7]}/",
            commit_sha,
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return completed.stdout


def job_context() -> JobContext:
    """The worker context the durable job runner would hand the handler."""

    return JobContext(
        job_id=uuid.uuid4(),
        operation_id=None,
        request_id=None,
        correlation_id=None,
        attempt_count=1,
        worker_id="transport-parity",
        lease_token=uuid.uuid4(),
    )


class _Response:
    status_code = 200

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.headers = {"Content-Length": str(len(body))}

    def iter_content(self, *, chunk_size: int):
        del chunk_size
        yield self.body

    def close(self) -> None:
        pass


if TYPE_CHECKING:
    # A mixin at runtime, so the loader does not collect the contract itself; a
    # TestCase to the type checker, so its assertions are still checked.
    _ContractBase = TestCase
else:
    _ContractBase = object


class _TransportParityContract(_ContractBase):
    """The parity claims, stated once and driven from more than one fixture.

    ``OVERLAY`` selects which fixture repository the checkout is built from, so
    a repository carrying export attributes is held to exactly the same claims
    as one that does not.
    """

    OVERLAY: Path | None = None
    CHECKOUT_NAME = "llm-zoomcamp"

    checkout: Path
    commit_sha: str
    archive: bytes

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        cls.checkout = SCRATCH_ROOT / cls.CHECKOUT_NAME
        cls.commit_sha = build_checkout(cls.checkout, overlay=cls.OVERLAY)
        cls.archive = codeload_archive(cls.checkout, cls.commit_sha)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(SCRATCH_ROOT / cls.CHECKOUT_NAME, ignore_errors=True)
        super().tearDownClass()

    def setUp(self) -> None:
        self.source = ContentSource.objects.create(
            id=uuid.UUID("7736c1e6-5d66-4286-8180-b1eef3f83a84"),
            stable_id=STABLE_ID,
            display_name="LLM Zoomcamp",
            repository_owner="DataTalksClub",
            repository_name="llm-zoomcamp",
            branch="main",
            adapter_type=COURSE_REPOSITORY_ADAPTER_TYPE,
            mount_path="/",
            enabled=True,
            max_files=5_000,
            max_bytes=100_000_000,
            revision=1,
        )
        self.seed_project_shell()

    def seed_project_shell(self) -> None:
        """The fixture cohort references a project the importer never creates.

        Both routes start from exactly this state, so the comparison measures
        the ingestion and nothing else.
        """

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

    # -- helpers ------------------------------------------------------------

    def tracked_paths(self) -> list[str]:
        return [line for line in _git(self.checkout, "ls-files", "-z").split("\0") if line]

    def run_push(self) -> None:
        with patch(
            "content_sync.course_repository_ingest.requests.get",
            return_value=_Response(self.archive),
        ):
            import_course_repository_commit(
                job_context(),
                {
                    "source_uuid": str(self.source.id),
                    "commit_sha": self.commit_sha,
                    "delivery_record_id": str(uuid.uuid4()),
                },
            )

    def run_pull(self) -> dict:
        from scripts.prod.sync_course_repositories import pull, select_sources

        checkouts = {STABLE_ID: self.checkout}
        sources = select_sources((), explicit=checkouts, root=None)
        return pull(sources=sources, checkouts=checkouts)

    def projection(self) -> dict[str, list]:
        """Every row the import owns, in a stable, comparable shape."""

        return {
            "courses": list(
                Course.objects.order_by("slug").values(
                    "slug",
                    "title",
                    "description",
                    "source_content_id",
                    "source_path",
                    "source_commit_sha",
                )
            ),
            "cohorts": list(
                Cohort.objects.order_by("slug").values(
                    "slug",
                    "identifier",
                    "title",
                    "year",
                    "curriculum_format",
                    "finished",
                    "source_content_id",
                    "source_path",
                    "source_commit_sha",
                )
            ),
            "modules": list(
                Module.objects.order_by("cohort__slug", "slug").values(
                    "slug", "title", "position", "source_content_id", "source_path"
                )
            ),
            "units": list(
                Unit.objects.order_by("module__slug", "slug").values(
                    "slug", "title", "position", "source_content_id", "source_path"
                )
            ),
            "homeworks": list(
                Homework.objects.order_by("course__slug", "slug").values(
                    "slug", "title", "state", "source_content_id", "source_path"
                )
            ),
            "questions": list(
                Question.objects.order_by("homework__slug", "id").values(
                    "text", "question_type", "scores_for_correct_answer"
                )
            ),
            "flow": list(
                CurriculumFlowItem.objects.order_by("cohort__slug", "position").values(
                    "position", "module__slug", "project__slug"
                )
            ),
            "runs": list(
                CourseCurriculumImportRun.objects.order_by("source_stable_id").values(
                    "source_stable_id",
                    "repository_owner",
                    "repository_name",
                    "repository_branch",
                    "commit_sha",
                    "state",
                    "counts",
                    "diagnostics",
                    "manifest_checksum",
                )
            ),
        }

    # -- the parity claims --------------------------------------------------

    def test_both_transports_read_the_same_snapshot(self) -> None:
        limits = course_repository_limits(self.source)
        with patch(
            "content_sync.course_repository_ingest.requests.get",
            return_value=_Response(self.archive),
        ):
            pushed = fetch_course_repository_snapshot(
                owner="DataTalksClub",
                repository="llm-zoomcamp",
                commit_sha=self.commit_sha,
                limits=limits,
            )
        pulled = read_course_repository_checkout(
            self.checkout, commit_sha=self.commit_sha, limits=limits
        )

        self.assertEqual(sorted(pushed), sorted(pulled))
        self.assertEqual(pushed, pulled)
        self.assertIn("course.yaml", pushed)

    def test_both_entry_points_project_identical_rows(self) -> None:
        # Both routes must start from byte-identical state for the comparison to
        # mean anything, so the push projection is rolled back to the savepoint
        # rather than deleted row by row.
        savepoint = transaction.savepoint()
        self.run_push()
        pushed = self.projection()
        self.assertTrue(pushed["modules"], "the push route projected nothing to compare")
        transaction.savepoint_rollback(savepoint)
        self.assertEqual(Module.objects.count(), 0)

        self.run_pull()
        pulled = self.projection()

        self.assertEqual(pushed, pulled)

    def test_pull_refuses_content_the_webhook_would_refuse(self) -> None:
        """One limit, one refusal, whichever route the content arrives by."""

        oversized = (
            self.checkout / "cohorts" / "2026" / "01-agentic-rag" / "code" / "notebook.ipynb"
        )
        narrow = replace(course_repository_limits(self.source), max_file_bytes=8)

        with patch(
            "content_sync.course_repository_ingest.requests.get",
            return_value=_Response(self.archive),
        ):
            with self.assertRaises(CourseRepositoryFetchError) as pushed:
                fetch_course_repository_snapshot(
                    owner="DataTalksClub",
                    repository="llm-zoomcamp",
                    commit_sha=self.commit_sha,
                    limits=narrow,
                )
        with self.assertRaises(CourseRepositoryFetchError) as pulled:
            read_course_repository_checkout(
                self.checkout, commit_sha=self.commit_sha, limits=narrow
            )

        self.assertEqual(pushed.exception.code, "course_repository_file_too_large")
        self.assertEqual(pulled.exception.code, pushed.exception.code)
        self.assertEqual(pulled.exception.detail, pushed.exception.detail)
        self.assertIn("8-byte per-file limit", pulled.exception.detail)
        self.assertTrue(oversized.is_file())

    def test_source_limits_never_widen_the_parser_ceiling(self) -> None:
        self.source.max_files = 1_000_000
        self.source.max_bytes = 900_000_000

        limits = course_repository_limits(self.source)

        self.assertEqual(limits.max_files, DEFAULT_LIMITS.max_files)
        self.assertEqual(limits.max_total_bytes, DEFAULT_LIMITS.max_total_bytes)
        self.assertEqual(limits.max_file_bytes, DEFAULT_LIMITS.max_file_bytes)

    def test_the_pull_transport_makes_no_network_call(self) -> None:
        """The claim the runbook makes, enforced rather than asserted in prose."""

        limits = course_repository_limits(self.source)

        def refuse(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise AssertionError("the pull transport opened a network connection")

        with (
            patch.object(socket.socket, "connect", refuse),
            patch.object(socket.socket, "connect_ex", refuse),
            patch("content_sync.course_repository_ingest.requests.get", refuse),
        ):
            pulled = read_course_repository_checkout(
                self.checkout, commit_sha=self.commit_sha, limits=limits
            )

        self.assertIn("course.yaml", pulled)


class CourseRepositoryTransportParityTests(_TransportParityContract, TestCase):
    """A repository with no ``.gitattributes`` at all."""


class CourseRepositoryExportAttributeParityTests(_TransportParityContract, TestCase):
    """The same claims for a repository that carries ``export-ignore``.

    ``git archive`` -- and therefore codeload, and therefore the push transport
    -- drops an ``export-ignore`` path.  A pull transport that reads the working
    tree or ``git ls-files`` keeps it.  No course repository carries a
    ``.gitattributes`` today, which is exactly why the divergence could sit in
    the code with a green parity suite; this fixture removes that cover.
    """

    OVERLAY = EXPORT_IGNORE_OVERLAY
    CHECKOUT_NAME = "llm-zoomcamp-export-ignore"

    def test_the_fixture_really_exercises_export_ignore(self) -> None:
        """Guard against a vacuous pass if the attributes stop taking effect."""

        limits = course_repository_limits(self.source)
        tracked = set(self.tracked_paths())
        exported = set(
            read_course_repository_checkout(
                self.checkout, commit_sha=self.commit_sha, limits=limits
            )
        )

        self.assertIn(".gitattributes", tracked)
        # The exported snapshot is what both transports must agree on, and it is
        # strictly smaller than the tracked file list -- which is the difference
        # the pull transport used to be blind to.
        self.assertEqual(
            sorted(tracked - exported),
            ["SITE.md", "cohorts/2025/cohort.yaml"],
        )
        self.assertEqual(exported - tracked, set())

    def test_the_export_ignored_cohort_is_absent_from_both_projections(self) -> None:
        """The divergence was visible in rows, not only in the file list."""

        self.run_pull()

        # cohorts/2025/cohort.yaml is export-ignored, so neither route can see the
        # 2025 cohort; before the fix the pull route created it and the push route
        # did not.
        self.assertEqual(
            sorted(Cohort.objects.values_list("identifier", flat=True)),
            ["2026"],
        )
