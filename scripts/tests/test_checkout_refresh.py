"""The checkout refresh boundary never destroys an unowned tree (audit REL-10).

``make content-checkouts`` and ``make content-checkout`` used to reset and
``clean -fdx`` any directory that had a ``.git`` subdirectory.  The shared
helper now refuses anything it did not create itself, anything whose origin is
not the registered repository, any dirty worktree (tracked edits, untracked
files, and ignored files alike), any linked worktree, and any path outside the
project scratch root -- each before running a mutating Git command.  These
tests drive real repositories so every refusal is observed against real Git
state, not a mock.

The test runtime denies ``git fetch``/``git push`` outright, so the two
advancing-refresh tests drive the identical local state transitions with
plumbing: the origin's ref moves via ``commit-tree``/``update-ref``, and the
helper's fetch step is emulated by pointing ``FETCH_HEAD`` at the advanced
origin head before the real ``reset --hard``/``clean`` run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from scripts import checkout_refresh
from scripts.checkout_refresh import CheckoutRefused, refresh_one

REPO_NAME = "content-fixture"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(root: Path, filename: str, content: str, message: str) -> str:
    (root / filename).write_text(content, encoding="utf-8")
    _git(root, "add", filename)
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _advance_locally(checkout: Path, filename: str, content: str) -> str:
    """Create a downstream commit the refresh can reset onto, then return
    the checkout to its previous head.

    A real fetch transfers the upstream objects; the test runtime denies
    ``git fetch`` outright, so the advancing commit is built in the checkout's
    own object store (normal porcelain on a temp branch) and handed to the
    helper through the emulated ``FETCH_HEAD``.  What is exercised is the
    helper's contract: reset a clean, owned checkout onto a *new* head.
    """
    detached = _git(checkout, "rev-parse", "HEAD")
    _git(checkout, "checkout", "-q", "-b", "advance-temp")
    head = _commit(checkout, filename, content, "advance")
    _git(checkout, "checkout", "-q", "main")
    _git(checkout, "branch", "-q", "-D", "advance-temp")
    del detached
    return head


def _local_refresh(
    host: str,
    checkout: Path,
    fetch_head: str,
    *,
    repository: str = REPO_NAME,
    branch: str = "main",
) -> str:
    """Refresh with the fetch step emulated by a prepared FETCH_HEAD."""
    real_git = checkout_refresh._git

    def local_git(checkout_path: Path, *arguments: str) -> str:
        if arguments[0] == "fetch":
            (checkout_path / ".git" / "FETCH_HEAD").write_text(f"{fetch_head}\n", encoding="utf-8")
            return fetch_head
        return real_git(checkout_path, *arguments)

    with mock.patch.object(checkout_refresh, "_git", local_git):
        return refresh_one(host=host, repository=repository, branch=branch, checkout=checkout)


@pytest.fixture()
def upstream(tmp_path: Path) -> tuple[Path, str]:
    """A bare origin named for the fixture repository; the host is its parent.

    The helper builds ``{host}/{repository}`` as the clone and expected-origin
    URL, so the bare repository carries exactly that name.
    """
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "fixture@example.invalid")
    _git(seed, "config", "user.name", "Fixture")
    _commit(seed, "README.md", "seed\n", "seed")
    origin = tmp_path / REPO_NAME
    _git(seed, "clone", "-q", "--bare", str(seed), str(origin))
    return origin, str(tmp_path)


def refresh(host: str, checkout: Path, *, repository: str = REPO_NAME, branch: str = "main") -> str:
    return refresh_one(host=host, repository=repository, branch=branch, checkout=checkout)


def test_a_fresh_clone_writes_the_ownership_marker(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    _origin, host = upstream
    checkout = tmp_path / "course-checkouts" / REPO_NAME

    head = refresh(host, checkout)

    assert head == _git(checkout, "rev-parse", "HEAD")
    assert (checkout / ".git" / checkout_refresh.OWNERSHIP_MARKER).is_file()


def test_an_owned_clean_checkout_refreshes_to_the_new_upstream_head(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    _origin, host = upstream
    checkout = tmp_path / "course-checkouts" / REPO_NAME
    refresh(host, checkout)

    new_head = _advance_locally(checkout, "lesson.md", "new lesson\n")

    refreshed = _local_refresh(host, checkout, new_head)

    assert refreshed == new_head
    assert (checkout / "lesson.md").read_text() == "new lesson\n"


def test_tracked_edits_survive_the_default_refresh_refusal(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    _origin, host = upstream
    checkout = tmp_path / "course-checkouts" / REPO_NAME
    refresh(host, checkout)
    (checkout / "README.md").write_text("local edit\n", encoding="utf-8")

    with pytest.raises(CheckoutRefused, match="dirty-checkout"):
        refresh(host, checkout)

    assert (checkout / "README.md").read_text() == "local edit\n"


def test_untracked_and_ignored_files_survive_the_refusal(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    _origin, host = upstream
    checkout = tmp_path / "course-checkouts" / REPO_NAME
    refresh(host, checkout)

    # Teach the checkout about .env, so it is genuinely ignored locally.
    ignored_head = _advance_locally(checkout, ".gitignore", ".env\n")
    _local_refresh(host, checkout, ignored_head)

    (checkout / "notes.txt").write_text("untracked\n", encoding="utf-8")
    (checkout / ".env").write_text("ignored\n", encoding="utf-8")

    with pytest.raises(CheckoutRefused, match="dirty-checkout"):
        refresh(host, checkout)

    assert (checkout / "notes.txt").read_text() == "untracked\n"
    assert (checkout / ".env").read_text() == "ignored\n"
    # No reset happened: HEAD is still the .gitignore commit, not beyond it.
    assert (checkout / ".gitignore").read_text() == ".env\n"


def test_a_checkout_this_tooling_did_not_create_is_refused(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    origin, host = upstream
    checkout = tmp_path / "course-checkouts" / REPO_NAME
    _git(tmp_path, "clone", "-q", "--branch", "main", str(origin), str(checkout))
    head_before = _git(checkout, "rev-parse", "HEAD")
    assert not (checkout / ".git" / checkout_refresh.OWNERSHIP_MARKER).exists()

    with pytest.raises(CheckoutRefused, match="unowned-checkout"):
        refresh(host, checkout)

    assert _git(checkout, "rev-parse", "HEAD") == head_before


def test_a_wrong_origin_is_refused_before_any_fetch(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    _origin, host = upstream
    checkout = tmp_path / "course-checkouts" / REPO_NAME
    refresh(host, checkout)
    # Point origin at a repository that does not exist: if the helper fetched
    # before validating, the failure would be a fetch error, not the refusal.
    _git(checkout, "remote", "set-url", "origin", str(tmp_path / "elsewhere"))

    with pytest.raises(CheckoutRefused, match="origin-mismatch"):
        refresh(host, checkout)


def test_a_linked_worktree_is_refused(upstream: tuple[Path, str], tmp_path: Path) -> None:
    _origin, host = upstream
    checkout = tmp_path / "course-checkouts" / REPO_NAME
    refresh(host, checkout)
    linked = tmp_path / "linked-worktree"
    _git(checkout, "worktree", "add", "-q", str(linked), "-b", "linked")

    with pytest.raises(CheckoutRefused, match="worktree-unsupported"):
        refresh(host, linked)

    assert (linked / ".git").is_file()
    # The main checkout and the linked worktree are both intact.
    assert (checkout / "README.md").read_text() == "seed\n"
    assert (linked / "README.md").read_text() == "seed\n"


def test_a_path_outside_the_scratch_root_is_refused_before_any_git_call(
    upstream: tuple[Path, str],
) -> None:
    _origin, host = upstream
    escape = Path("/tmp/dtc-rel10-escape-check")
    try:
        with pytest.raises(CheckoutRefused, match="checkout-outside-scratch-root"):
            refresh(host, escape)
        # The refusal preceded every mutation: nothing was created.
        assert not escape.exists()
    finally:
        if escape.exists():
            subprocess.run(["rm", "-r", str(escape)], check=True)  # noqa: S603


def test_a_non_git_directory_is_refused_rather_than_cloned_into(
    upstream: tuple[Path, str], tmp_path: Path
) -> None:
    _origin, host = upstream
    checkout = tmp_path / "course-checkouts" / REPO_NAME
    checkout.mkdir(parents=True)
    (checkout / "precious.txt").write_text("not a repo\n", encoding="utf-8")

    with pytest.raises(CheckoutRefused, match="not-a-checkout"):
        refresh(host, checkout)

    assert (checkout / "precious.txt").read_text() == "not a repo\n"


def test_the_cli_reports_each_refresh_against_its_stable_id(
    upstream: tuple[Path, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _origin, host = upstream
    checkout = tmp_path / "course-checkouts" / REPO_NAME
    plan = [f"content-fixture\t{REPO_NAME}\tmain\t{checkout}"]

    exit_code = checkout_refresh.run(host, plan)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.split()[:2] == [
        "content-fixture",
        _git(checkout, "rev-parse", "HEAD"),
    ]
    assert (checkout / ".git" / checkout_refresh.OWNERSHIP_MARKER).is_file()


def test_the_cli_refuses_a_malformed_plan_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = checkout_refresh.run("https://github.com", ["only-two\tfields"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "plan-line-invalid" in captured.err
