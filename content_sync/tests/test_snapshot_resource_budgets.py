"""Resource-budget acceptance for the snapshot transports (PUB-05).

The snapshot boundary must refuse pathological input before the work is
done, not after: ``git archive`` output is capped while streaming, tar
structure and decompression have budgets independent of the admitted
content, the codeload fetch has one monotonic total deadline, and both
transports refuse with the same vocabulary. Everything here runs at
harmless scale (kilobyte tars, megabyte repositories) -- no memory-stress
fixtures.
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from content_sync import snapshot
from content_sync.course_repository_ingest import (
    CourseRepositoryFetchError,
    CourseRepositoryLimits,
    fetch_course_repository_snapshot,
    read_course_repository_checkout,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_ROOT = PROJECT_ROOT / ".tmp" / "snapshot-resource-budgets"


def _build_tar(*specs: tuple[str, bytes, bytes, dict[str, str] | None]) -> bytes:
    """One tar session holding every spec: ``(name, type, content, pax)``."""

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, entry_type, content, pax_headers in specs:
            info = tarfile.TarInfo(name)
            info.type = entry_type
            info.size = len(content)
            if pax_headers:
                info.pax_headers = pax_headers
            archive.addfile(info, io.BytesIO(content) if info.size else None)
    return buffer.getvalue()


def _file(name: str, content: bytes = b"x") -> tuple[str, bytes, bytes, None]:
    return (name, tarfile.REGTYPE, content, None)


def _dir(name: str) -> tuple[str, bytes, bytes, None]:
    return (name, tarfile.DIRTYPE, b"", None)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout


def _git_repo(
    name: str, *, file_relative: str = "file.txt", content: bytes = b"x\n"
) -> tuple[Path, str]:
    root = SCRATCH_ROOT / name
    shutil.rmtree(root, ignore_errors=True)
    target = root / file_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.email", "budgets@example.invalid")
    _git(root, "config", "user.name", "Snapshot Budgets")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "--message", "fixture")
    return root, _git(root, "rev-parse", "HEAD").strip()


def _fake_git(name: str, script: str) -> str:
    """Put a fake ``git`` first on PATH for calls that pick the env up live."""

    bin_dir = SCRATCH_ROOT / "fake-git" / name
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = bin_dir / "git"
    executable.write_text(f"#!/bin/sh\n{script}", encoding="utf-8")
    executable.chmod(0o755)
    return str(bin_dir)


class _StubResponse:
    status_code = 200

    def __init__(self, body: bytes = b"", *, sleep_first: float = 0.0) -> None:
        self.body = body
        self.sleep_first = sleep_first
        self.headers: dict[str, str] = {}
        self.closed = False

    def iter_content(self, *, chunk_size: int):
        del chunk_size
        if self.sleep_first:
            time.sleep(self.sleep_first)
        yield self.body

    def close(self) -> None:
        self.closed = True


class GitArchiveStreamingTests(SimpleTestCase):
    """``run_git_archive`` bounds the child, not just the result."""

    def test_output_over_the_cap_is_refused_midstream(self) -> None:
        root, commit_sha = _git_repo(
            "over-cap", file_relative="big.bin", content=os.urandom(2 * 1024 * 1024)
        )

        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.run_git_archive(root, commit_sha, timeout_seconds=60, max_bytes=64 * 1024)

        self.assertEqual(raised.exception.code, "archive_too_large")

    def test_success_under_the_cap_returns_the_archive(self) -> None:
        root, commit_sha = _git_repo("under-cap", file_relative="a.txt", content=b"hello\n")

        archive = snapshot.run_git_archive(
            root, commit_sha, timeout_seconds=60, max_bytes=1024 * 1024
        )

        self.assertEqual(
            snapshot.read_snapshot_archive(
                archive,
                limits=CourseRepositoryLimits(max_files=5, max_total_bytes=100, max_file_bytes=100),
                strip_root=False,
            ),
            {"a.txt": b"hello\n"},
        )

    def test_huge_stderr_on_success_cannot_deadlock_the_child(self) -> None:
        """A chatty git must be drained while we read stdout, then discarded."""

        payload = SCRATCH_ROOT / "fake-git" / "loud-success.tar"
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(_build_tar(_file("a.txt", b"hello")))
        bin_dir = _fake_git(
            "loud-success",
            f"head -c 100000 /dev/zero | tr '\\0' 'x' >&2\ncat {payload}\n",
        )

        with patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}):
            archive = snapshot.run_git_archive(
                SCRATCH_ROOT, "0" * 40, timeout_seconds=60, max_bytes=1024 * 1024
            )

        self.assertIn(b"a.txt", archive)

    def test_failure_stderr_is_capped_and_reports_the_first_line(self) -> None:
        bin_dir = _fake_git(
            "loud-failure",
            "head -c 100000 /dev/zero | tr '\\0' 'x' >&2\nexit 3\n",
        )

        with patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}):
            with self.assertRaises(snapshot.SnapshotError) as raised:
                snapshot.run_git_archive(
                    SCRATCH_ROOT, "0" * 40, timeout_seconds=60, max_bytes=1024 * 1024
                )

        self.assertEqual(raised.exception.code, "checkout_archive_failed")
        # The bounded detail: 100 kB of stderr collapses into one short line.
        self.assertLessEqual(len(raised.exception.detail), 400)

    def test_a_hanging_git_is_killed_at_the_deadline(self) -> None:
        bin_dir = _fake_git("hanging", "sleep 30\n")
        started = time.monotonic()

        with patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}):
            with self.assertRaises(snapshot.SnapshotError) as raised:
                snapshot.run_git_archive(
                    SCRATCH_ROOT, "0" * 40, timeout_seconds=1, max_bytes=1024 * 1024
                )

        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(raised.exception.code, "checkout_archive_failed")
        self.assertIn("timed out after 1s", raised.exception.detail)

    def test_an_unusable_checkout_is_refused_before_any_child(self) -> None:
        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.run_git_archive(SCRATCH_ROOT / "does-not-exist", "0" * 40, max_bytes=1024)

        self.assertEqual(raised.exception.code, "checkout_unavailable")


class ArchiveStructureBudgetTests(SimpleTestCase):
    """Tar structure and decompression are bounded before content is admitted."""

    def test_many_directory_entries_exhaust_the_structural_budget(self) -> None:
        """The audit's repro: a directory-only tar is no longer free."""

        archive = _build_tar(*[_dir(f"dir-{index:03d}/") for index in range(100)])

        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.read_snapshot_archive(
                archive,
                limits=CourseRepositoryLimits(max_files=1, max_total_bytes=1, max_file_bytes=1),
                strip_root=False,
            )

        self.assertEqual(raised.exception.code, "archive_members_exceeded")

    def test_legitimate_directories_do_not_consume_the_file_count(self) -> None:
        specs = [_dir(f"dir-{index:02d}/") for index in range(50)]
        specs += [_file(f"dir-{index:02d}/file.txt") for index in range(10)]

        result = snapshot.read_snapshot_archive(
            _build_tar(*specs),
            limits=CourseRepositoryLimits(max_files=10, max_total_bytes=100, max_file_bytes=10),
            strip_root=False,
        )

        self.assertEqual(len(result), 10)

    def test_a_compressed_metadata_bomb_is_bounded(self) -> None:
        """Megabytes of pax metadata refuse even though no file is big."""

        specs = [
            (
                f"placeholder-{index}",
                tarfile.REGTYPE,
                b"",
                {"path": "a" * 200_000},
            )
            for index in range(3)
        ]

        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.read_snapshot_archive(
                gzip.compress(_build_tar(*specs)),
                limits=CourseRepositoryLimits(max_files=1, max_total_bytes=1, max_file_bytes=1),
                strip_root=False,
            )

        self.assertEqual(raised.exception.code, "archive_expansion_exceeded")

    def test_symlinks_and_duplicates_are_still_refused(self) -> None:
        link = ("link", tarfile.SYMTYPE, b"", None)
        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.read_snapshot_archive(
                _build_tar(link),
                limits=CourseRepositoryLimits(max_files=5, max_total_bytes=100, max_file_bytes=10),
                strip_root=False,
            )
        self.assertEqual(raised.exception.code, "archive_entry_invalid")

        duplicate = _build_tar(_file("a.txt", b"one"), _file("a.txt", b"two"))
        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.read_snapshot_archive(
                duplicate,
                limits=CourseRepositoryLimits(max_files=5, max_total_bytes=100, max_file_bytes=10),
                strip_root=False,
            )
        self.assertEqual(raised.exception.code, "duplicate_path")


class SnapshotPathHardeningTests(SimpleTestCase):
    """Collapsed, ambiguous and invisible names refuse instead of collapsing."""

    def test_ambiguous_forms_are_refused(self) -> None:
        for bad_name in (
            "repo//a.md",
            "repo/./a.md",
            "./a.md",
            "a/../b.md",
            "a//",
            "/abs/a.md",
            "a\nb",
            "a\x1fb",
            "a\x7fb",
            "a\\b",
            "a\x00b",
        ):
            with self.subTest(name=bad_name):
                with self.assertRaises(snapshot.SnapshotError) as raised:
                    snapshot.snapshot_relative_path(bad_name, strip_root=False)
                self.assertEqual(raised.exception.code, "archive_path_invalid")

    def test_reviewed_forms_keep_their_meaning(self) -> None:
        cases: list[tuple[str, bool, object]] = [
            ("repo-sha/a.md", True, "a.md"),
            ("repo-sha/nested/b.md", True, "nested/b.md"),
            ("docs/", False, "docs"),
            ("repo-sha/", True, None),
        ]
        for name, strip_root, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    snapshot.snapshot_relative_path(name, strip_root=strip_root),
                    expected,
                )


class FetchBudgetTests(SimpleTestCase):
    """The codeload exchange has one monotonic total deadline."""

    def test_a_budget_spent_on_the_headers_refuses_before_reading(self) -> None:
        stub = _StubResponse(b"never read")

        with (
            patch("content_sync.course_repository_ingest.TOTAL_FETCH_TIMEOUT_SECONDS", 0),
            patch("content_sync.course_repository_ingest.requests.get", return_value=stub),
        ):
            with self.assertRaises(CourseRepositoryFetchError) as raised:
                fetch_course_repository_snapshot(
                    owner="owner",
                    repository="repo",
                    commit_sha="a" * 40,
                    limits=CourseRepositoryLimits(
                        max_files=5, max_total_bytes=100, max_file_bytes=10
                    ),
                )

        self.assertEqual(raised.exception.code, "course_repository_fetch_timeout")
        self.assertTrue(raised.exception.retryable)
        self.assertTrue(stub.closed)

    def test_a_budget_spent_midstream_stops_the_reads(self) -> None:
        stub = _StubResponse(b"chunk", sleep_first=0.05)

        with (
            patch("content_sync.course_repository_ingest.TOTAL_FETCH_TIMEOUT_SECONDS", 0.01),
            patch("content_sync.course_repository_ingest.requests.get", return_value=stub),
        ):
            with self.assertRaises(CourseRepositoryFetchError) as raised:
                fetch_course_repository_snapshot(
                    owner="owner",
                    repository="repo",
                    commit_sha="a" * 40,
                    limits=CourseRepositoryLimits(
                        max_files=5, max_total_bytes=100, max_file_bytes=10
                    ),
                )

        self.assertEqual(raised.exception.code, "course_repository_fetch_timeout")
        self.assertTrue(raised.exception.retryable)
        self.assertTrue(stub.closed)


class TransportBudgetParityTests(SimpleTestCase):
    """Both transports refuse the same pathology with the same vocabulary."""

    def test_a_structurally_exhausting_tree_refuses_identically(self) -> None:
        deep = "/".join(f"level-{index:02d}" for index in range(100))
        root, commit_sha = _git_repo("deep-tree", file_relative=f"{deep}/deepest.txt")
        limits = CourseRepositoryLimits(max_files=1, max_total_bytes=1_000_000, max_file_bytes=100)

        with self.assertRaises(CourseRepositoryFetchError) as pulled:
            read_course_repository_checkout(root, commit_sha=commit_sha, limits=limits)

        prefixed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "archive",
                "--format=tar",
                f"--prefix=codeload-{commit_sha[:7]}/",
                commit_sha,
            ],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
        stub = _StubResponse(prefixed)
        with patch("content_sync.course_repository_ingest.requests.get", return_value=stub):
            with self.assertRaises(CourseRepositoryFetchError) as pushed:
                fetch_course_repository_snapshot(
                    owner="owner",
                    repository="repo",
                    commit_sha=commit_sha,
                    limits=limits,
                )

        self.assertEqual(pulled.exception.code, "course_repository_archive_members_exceeded")
        self.assertEqual(pushed.exception.code, pulled.exception.code)
        self.assertTrue(stub.closed)
