"""The pull entry point's target selection is its write authorization (REL-05).

``configure_target()`` decides where the rows land and refuses anything short
of a complete, self-consistent selection; the local seeders keep their own
local-only guard and this script borrows nothing from them.  These tests drive
``main()`` end to end with the shared ingestion boundary replaced by a
recorder, so a reviewed deployed selection demonstrably reaches ingestion --
and a broken selection is refused before any write -- without connecting to
anything.
"""

from __future__ import annotations

import os
import shutil
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Any

from django.test import TestCase

from content_sync.course_repository_ingest import (
    CHECKOUT_TRANSPORT,
    CourseRepositoryIngestResult,
)
from content_sync.tests.test_course_repository_transport_parity import build_checkout
from content_sync.tests.test_sync_course_repositories import make_source
from scripts.prod.sync_course_repositories import main
from scripts.prod.target import REQUIRED_DEPLOYED_ENVIRONMENT
from scripts.tests.test_prod_write_target import SYNTHETIC_DEPLOYED_ENVIRONMENT

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_ROOT = PROJECT_ROOT / ".tmp" / "pull-course-repositories-cli"
DEPLOYED_SELECTION = (
    "--deployment-target",
    "website-production",
    "--allow-production-write",
    "website-production",
)


class IngestRecorder:
    """Stand-in for the shared ingestion boundary; records and writes nothing."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, source: Any, commit_sha: str, checkout_root: Path | None):
        del checkout_root
        self.calls.append({"stable_id": source.stable_id, "commit_sha": commit_sha})
        return CourseRepositoryIngestResult(
            source_stable_id=source.stable_id,
            repository=f"{source.repository_owner}/{source.repository_name}",
            branch=source.branch,
            commit_sha=commit_sha,
            transport=CHECKOUT_TRANSPORT,
            file_count=1,
            total_bytes=1,
            counts=MappingProxyType({"modules": 1}),
            replayed=False,
        )


class PullCliTargetSelectionTests(TestCase):
    checkout: Path
    commit_sha: str

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        cls.checkout = SCRATCH_ROOT / "llm-zoomcamp"
        cls.commit_sha = build_checkout(cls.checkout)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(SCRATCH_ROOT, ignore_errors=True)
        super().tearDownClass()

    def run_main(
        self,
        argv: list[str],
        recorder: IngestRecorder,
        *,
        with_environment: dict[str, str] | None = None,
        without_environment: tuple[str, ...] = (),
    ) -> int:
        # patch.dict rolls back every environment change configure_target()
        # exports, so one run's target selection cannot describe the next.
        with (
            unittest.mock.patch.dict(os.environ, with_environment or {}),
            unittest.mock.patch(
                "content_sync.course_repository_ingest.ingest_course_repository",
                recorder,
            ),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            for name in without_environment:
                os.environ.pop(name, None)
            return main(argv)

    def test_deployed_selection_reaches_the_ingestion_boundary(self) -> None:
        source = make_source()
        recorder = IngestRecorder()

        exit_code = self.run_main(
            [*DEPLOYED_SELECTION, "--from-disk", str(SCRATCH_ROOT), "--quiet"],
            recorder,
            with_environment=dict(SYNTHETIC_DEPLOYED_ENVIRONMENT),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            [call["stable_id"] for call in recorder.calls],
            [source.stable_id],
        )
        self.assertEqual(recorder.calls[0]["commit_sha"], self.commit_sha)

    def test_local_selection_reaches_the_ingestion_boundary(self) -> None:
        source = make_source()
        recorder = IngestRecorder()

        exit_code = self.run_main(
            [
                "--database",
                str(SCRATCH_ROOT / "scratch.sqlite3"),
                "--from-disk",
                str(SCRATCH_ROOT),
            ],
            recorder,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual([call["stable_id"] for call in recorder.calls], [source.stable_id])

    def test_refusals_happen_before_any_write(self) -> None:
        recorder = IngestRecorder()
        cases = {
            "no target at all": [],
            "opt-in without a target": ["--allow-production-write", "website-production"],
            "deployed without the opt-in": ["--deployment-target", "website-production"],
            "flags that disagree": [
                "--deployment-target",
                "website-production",
                "--allow-production-write",
                "website-sandbox",
            ],
        }
        for label, argv in cases.items():
            with self.subTest(selection=label):
                with self.assertRaises(SystemExit) as raised:
                    self.run_main([*argv, "--from-disk", str(SCRATCH_ROOT)], recorder)
                self.assertEqual(raised.exception.code, 2)

        with self.subTest(selection="deployed missing its environment names"):
            with self.assertRaises(SystemExit) as raised:
                self.run_main(
                    [*DEPLOYED_SELECTION, "--from-disk", str(SCRATCH_ROOT)],
                    recorder,
                    without_environment=REQUIRED_DEPLOYED_ENVIRONMENT,
                )
            self.assertEqual(raised.exception.code, 2)

        self.assertEqual(recorder.calls, [])
