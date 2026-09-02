"""The imported commit has to be one a public reader can resolve.

A unit records its provenance as a repository plus a commit SHA, and every
public affordance built from that provenance -- the "Edit on GitHub" link, the
raw image URL, a source path a reader follows -- assumes the commit is on the
public repository.  Importing a commit that only exists on a local clone
publishes pages whose source links can only 404, so the preparation refuses
unless the operator states why it may proceed anyway.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from courses.services.local_course_modules import (
    LocalCourseModulesError,
    commit_is_public,
    public_repository_urls,
    validate_public_commit,
)

PUBLIC_URL = "https://github.com/DataTalksClub/llm-zoomcamp.git"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


class SourceCommitReachabilityTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        scratch = Path(settings.BASE_DIR) / ".tmp" / "source-commit-reachability"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "checkout"
        self.root.mkdir()
        _git(self.root, "init", "--initial-branch=main", ".")
        _git(self.root, "config", "user.email", "fixture@example.invalid")
        _git(self.root, "config", "user.name", "Fixture")
        (self.root / "course.yaml").write_text("slug: llm-zoomcamp\n", encoding="utf-8")
        _git(self.root, "add", "course.yaml")
        _git(self.root, "commit", "--message", "published")
        self.published_sha = _git(self.root, "rev-parse", "HEAD")
        # The remote-tracking branch is what a clone of the public repository
        # would carry, so it stands in for the published tip without a network.
        _git(self.root, "remote", "add", "origin", PUBLIC_URL)
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.published_sha)
        (self.root / "course.yaml").write_text("slug: llm-zoomcamp\ntitle: x\n", encoding="utf-8")
        _git(self.root, "add", "course.yaml")
        _git(self.root, "commit", "--message", "local only")
        self.local_only_sha = _git(self.root, "rev-parse", "HEAD")

    def reachable(self, commit_sha: str) -> bool:
        return commit_is_public(
            self.root,
            owner="DataTalksClub",
            name="llm-zoomcamp",
            commit_sha=commit_sha,
        )

    def test_commit_on_the_public_remote_branch_is_reachable(self) -> None:
        self.assertTrue(self.reachable(self.published_sha))

    def test_commit_that_exists_only_on_local_main_is_not_reachable(self) -> None:
        self.assertFalse(self.reachable(self.local_only_sha))

    def test_a_remote_that_is_not_the_public_repository_never_counts(self) -> None:
        """This is exactly today's local dataset: origin is a sibling clone."""

        _git(self.root, "remote", "set-url", "origin", str(self.root.parent / "mirror"))

        self.assertFalse(self.reachable(self.published_sha))

    def test_a_remote_for_a_different_repository_never_counts(self) -> None:
        _git(self.root, "remote", "set-url", "origin", "https://github.com/DataTalksClub/faq.git")

        self.assertFalse(self.reachable(self.published_sha))

    def test_unknown_commits_and_broken_checkouts_are_not_reachable(self) -> None:
        self.assertFalse(self.reachable("0" * 40))
        self.assertFalse(
            commit_is_public(
                self.root.parent / "missing",
                owner="DataTalksClub",
                name="llm-zoomcamp",
                commit_sha=self.published_sha,
            )
        )

    def guard(self, commit_sha: str, *, unpublished_reason: str = "") -> bool:
        return validate_public_commit(
            self.root,
            owner="DataTalksClub",
            name="llm-zoomcamp",
            commit_sha=commit_sha,
            unpublished_reason=unpublished_reason,
        )

    def test_the_guard_accepts_a_published_commit_without_a_reason(self) -> None:
        self.assertTrue(self.guard(self.published_sha))

    def test_the_guard_refuses_an_unpublished_commit(self) -> None:
        with self.assertRaises(LocalCourseModulesError) as caught:
            self.guard(self.local_only_sha)

        self.assertEqual(str(caught.exception), "source_commit_not_public")

    def test_the_guard_admits_an_unpublished_commit_only_against_a_stated_reason(self) -> None:
        self.assertFalse(
            self.guard(
                self.local_only_sha,
                unpublished_reason="The 2026 curriculum is not pushed yet.",
            )
        )

    def test_public_url_spellings_cover_the_forms_a_clone_records(self) -> None:
        urls = public_repository_urls("DataTalksClub", "llm-zoomcamp")

        self.assertIn("https://github.com/datatalksclub/llm-zoomcamp", urls)
        self.assertIn("https://github.com/datatalksclub/llm-zoomcamp.git", urls)
        self.assertIn("git@github.com:datatalksclub/llm-zoomcamp.git", urls)
        self.assertIn("ssh://git@github.com/datatalksclub/llm-zoomcamp", urls)
