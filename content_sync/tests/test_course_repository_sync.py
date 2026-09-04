from __future__ import annotations

import io
import tarfile
from dataclasses import replace
from unittest.mock import patch

from django.test import SimpleTestCase

from content_sync.course_repository import DEFAULT_LIMITS
from content_sync.course_repository_ingest import (
    CourseRepositoryFetchError,
    fetch_course_repository_snapshot,
)

LIMITS = replace(DEFAULT_LIMITS, max_files=10, max_total_bytes=100)


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


def archive(*entries: tuple[str, bytes, str]) -> bytes:
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w:gz") as output:
        for name, body, kind in entries:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "course.yaml"
                info.size = 0
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                info.size = 0
            output.addfile(info, io.BytesIO(body) if kind == "file" else None)
    return target.getvalue()


def fetch(*, limits=LIMITS):
    return fetch_course_repository_snapshot(
        owner="DataTalksClub",
        repository="llm-zoomcamp",
        commit_sha="a" * 40,
        limits=limits,
    )


class CourseRepositoryArchiveTests(SimpleTestCase):
    @patch("content_sync.course_repository_ingest.requests.get")
    def test_fetch_strips_codeload_root_and_returns_regular_files(self, get):
        body = archive(
            ("llm-zoomcamp-a/course.yaml", b"course", "file"),
            ("llm-zoomcamp-a/cohorts/2026/cohort.yaml", b"cohort", "file"),
        )
        get.return_value = _Response(body)

        snapshot = fetch()

        self.assertEqual(
            snapshot,
            {
                "course.yaml": b"course",
                "cohorts/2026/cohort.yaml": b"cohort",
            },
        )
        get.assert_called_once_with(
            "https://codeload.github.com/DataTalksClub/llm-zoomcamp/tar.gz/" + "a" * 40,
            timeout=30,
            stream=True,
        )

    @patch("content_sync.course_repository_ingest.requests.get")
    def test_directory_entries_are_structure_not_content(self, get):
        """GitHub's ``git archive`` tarball carries a member per directory.

        Refusing those made the push path reject every real repository archive
        while still passing on hand-built fixtures that omitted them -- which is
        exactly the drift a webhook that has never fired cannot surface.
        """

        get.return_value = _Response(
            archive(
                ("llm-zoomcamp-a/", b"", "dir"),
                ("llm-zoomcamp-a/cohorts/", b"", "dir"),
                ("llm-zoomcamp-a/cohorts/2026/", b"", "dir"),
                ("llm-zoomcamp-a/cohorts/2026/cohort.yaml", b"cohort", "file"),
                ("llm-zoomcamp-a/course.yaml", b"course", "file"),
            )
        )

        self.assertEqual(
            fetch(),
            {"course.yaml": b"course", "cohorts/2026/cohort.yaml": b"cohort"},
        )

    @patch("content_sync.course_repository_ingest.requests.get")
    def test_traversal_and_symlink_entries_are_rejected(self, get):
        for entry in (
            ("repo/../escape", b"bad", "file"),
            ("repo/course.yaml", b"", "symlink"),
        ):
            with self.subTest(entry=entry[0]):
                get.return_value = _Response(archive(entry))
                with self.assertRaisesRegex(
                    CourseRepositoryFetchError,
                    "course_repository_archive_",
                ):
                    fetch()

    @patch("content_sync.course_repository_ingest.requests.get")
    def test_source_limits_are_enforced_before_returning_snapshot(self, get):
        get.return_value = _Response(
            archive(
                ("repo/course.yaml", b"course", "file"),
                ("repo/README.md", b"readme", "file"),
            )
        )

        with self.assertRaisesRegex(CourseRepositoryFetchError, "source_limit_exceeded"):
            fetch(limits=replace(LIMITS, max_files=1))

    @patch("content_sync.course_repository_ingest.requests.get")
    def test_oversized_file_names_the_path_and_both_sizes(self, get):
        get.return_value = _Response(
            archive(
                ("repo/course.yaml", b"course", "file"),
                ("repo/assets/dataset.parquet", b"x" * 64, "file"),
            )
        )

        with self.assertRaises(CourseRepositoryFetchError) as caught:
            fetch(limits=replace(LIMITS, max_file_bytes=32))

        self.assertEqual(caught.exception.code, "course_repository_file_too_large")
        self.assertIn("assets/dataset.parquet", str(caught.exception))
        self.assertIn("64 bytes", str(caught.exception))
        self.assertIn("32-byte", str(caught.exception))
