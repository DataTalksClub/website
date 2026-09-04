"""The imported commit has to be one a public reader can resolve.

A unit records its provenance as a repository plus a commit SHA, and every
public affordance built from that provenance -- the "Edit on GitHub" link, the
raw image URL, a source path a reader follows -- assumes the commit is on the
public repository.  Importing a commit that only exists on a local clone
publishes pages whose source links can only 404, so ``sync_course_repositories.py
--require-public-commit`` refuses it.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from content.models import ContentSource
from content_sync.course_repository_checkout import commit_is_public, public_repository_urls
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE
from scripts.prod.sync_course_repositories import (
    SyncCourseRepositoriesError,
    select_sources,
)
from scripts.prod.sync_course_repositories import (
    pull as pull_sources,
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

    def test_public_url_spellings_cover_the_forms_a_clone_records(self) -> None:
        urls = public_repository_urls("DataTalksClub", "llm-zoomcamp")

        self.assertIn("https://github.com/datatalksclub/llm-zoomcamp", urls)
        self.assertIn("https://github.com/datatalksclub/llm-zoomcamp.git", urls)
        self.assertIn("git@github.com:datatalksclub/llm-zoomcamp.git", urls)
        self.assertIn("ssh://git@github.com/datatalksclub/llm-zoomcamp", urls)


class RequirePublicCommitOptionTests(TestCase):
    """The guard is a precondition on the one entry point, not a second path."""

    def setUp(self) -> None:
        super().setUp()
        scratch = Path(settings.BASE_DIR) / ".tmp" / "require-public-commit"
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
        _git(self.root, "commit", "--message", "local only")
        ContentSource.objects.create(
            id=uuid.uuid4(),
            stable_id="llm-zoomcamp",
            display_name="LLM Zoomcamp",
            repository_owner="DataTalksClub",
            repository_name="llm-zoomcamp",
            branch="main",
            adapter_type=COURSE_REPOSITORY_ADAPTER_TYPE,
            mount_path="/",
            enabled=True,
            max_files=5_000,
            max_bytes=100_000_000,
        )

    def pull(self, *, require_public_commit: bool = False) -> None:
        checkouts = {"llm-zoomcamp": self.root}
        sources = select_sources((), explicit=checkouts, root=None)
        pull_sources(
            sources=sources, checkouts=checkouts, require_public_commit=require_public_commit
        )

    def test_an_unpublished_commit_is_refused_when_the_guard_is_asked_for(self) -> None:
        with self.assertRaisesRegex(SyncCourseRepositoriesError, "is not on a branch of"):
            self.pull(require_public_commit=True)

    def test_the_guard_is_off_by_default(self) -> None:
        # The checkout is unpublished, so reaching selection at all proves the
        # reachability guard did not fire.
        with self.assertRaisesRegex(SyncCourseRepositoriesError, "course repository pull refused"):
            self.pull()
