from __future__ import annotations

import io
import tarfile
from unittest.mock import patch

from django.test import SimpleTestCase

from content_sync.course_repository_sync import (
    CourseRepositoryFetchError,
    fetch_course_repository_snapshot,
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
            output.addfile(info, io.BytesIO(body) if kind == "file" else None)
    return target.getvalue()


class CourseRepositoryArchiveTests(SimpleTestCase):
    @patch("content_sync.course_repository_sync.requests.get")
    def test_fetch_strips_codeload_root_and_returns_regular_files(self, get):
        body = archive(
            ("llm-zoomcamp-a/course.yaml", b"course", "file"),
            ("llm-zoomcamp-a/cohorts/2026/cohort.yaml", b"cohort", "file"),
        )
        get.return_value = _Response(body)

        snapshot = fetch_course_repository_snapshot(
            owner="DataTalksClub",
            repository="llm-zoomcamp",
            commit_sha="a" * 40,
            max_files=10,
            max_bytes=100,
        )

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

    @patch("content_sync.course_repository_sync.requests.get")
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
                    fetch_course_repository_snapshot(
                        owner="DataTalksClub",
                        repository="llm-zoomcamp",
                        commit_sha="a" * 40,
                        max_files=10,
                        max_bytes=100,
                    )

    @patch("content_sync.course_repository_sync.requests.get")
    def test_source_limits_are_enforced_before_returning_snapshot(self, get):
        body = archive(
            ("repo/course.yaml", b"course", "file"),
            ("repo/README.md", b"readme", "file"),
        )
        get.return_value = _Response(body)

        with self.assertRaisesRegex(CourseRepositoryFetchError, "source_limit_exceeded"):
            fetch_course_repository_snapshot(
                owner="DataTalksClub",
                repository="llm-zoomcamp",
                commit_sha="a" * 40,
                max_files=1,
                max_bytes=100,
            )
