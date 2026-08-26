from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

from ci import classifier as classifier_module
from ci.classifier import ZERO_SHA, classify_git_change


def git(repository: Path, *arguments: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
    )
    return result.stdout.strip()


def commit(repository: Path, message: str) -> str:
    git(repository, "add", "-A")
    git(repository, "commit", "-m", message)
    return git(repository, "rev-parse", "HEAD")


def repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.name", "CI Test")
    git(repo, "config", "user.email", "ci@example.invalid")
    (repo / "api").mkdir()
    (repo / "api" / "module.py").write_text("BASE = True\n", encoding="utf-8")
    return repo, commit(repo, "base")


def classify(repo: Path, base: str, head: str, **overrides: str):
    return classify_git_change(
        repository=repo,
        event=overrides.get("event", "push"),
        base=base,
        after=overrides.get("after", head),
        github_sha=overrides.get("github_sha", head),
        release_sha=overrides.get("release_sha", head),
    )


def test_real_git_modify_and_delete_use_exact_base_to_head(tmp_path: Path) -> None:
    repo, base = repository(tmp_path)
    (repo / "api" / "module.py").write_text("BASE = False\n", encoding="utf-8")
    (repo / "api" / "deleted.py").write_text("DELETE = True\n", encoding="utf-8")
    middle = commit(repo, "middle")
    (repo / "api" / "deleted.py").unlink()
    head = commit(repo, "head")

    modified = classify(repo, base, middle)
    deleted = classify(repo, middle, head)
    complete_range = classify(repo, base, head)

    assert modified["profile"] == "focused"
    assert deleted["profile"] == "focused"
    assert complete_range["profile"] == "focused"
    assert complete_range["base"] == base
    assert complete_range["head"] == head


def test_manual_dispatch_never_guesses_a_base(tmp_path: Path) -> None:
    repo, head = repository(tmp_path)
    result = classify(repo, "9" * 40, head, event="workflow_dispatch", after="")
    assert result["profile"] == "full"
    assert result["reason"] == "manual_dispatch"
    assert result["base"] is None


def test_rollback_dispatch_stays_full_without_a_base(tmp_path: Path) -> None:
    repo, head = repository(tmp_path)
    result = classify_git_change(
        repository=repo,
        event="workflow_dispatch",
        base="",
        after="",
        github_sha=head,
        release_sha=head,
        dispatch_operation="rollback",
    )
    assert result["profile"] == "full"
    assert result["reason"] == "manual_dispatch"
    assert result["base"] is None


def test_promote_dispatch_classifies_the_first_parent_range(tmp_path: Path) -> None:
    repo, base = repository(tmp_path)
    (repo / "api" / "module.py").write_text("BASE = False\n", encoding="utf-8")
    head = commit(repo, "head")
    result = classify_git_change(
        repository=repo,
        event="workflow_dispatch",
        base="",
        after="",
        github_sha="c" * 40,
        release_sha=head,
        dispatch_operation="promote",
    )
    assert result["profile"] == "focused"
    assert result["reason"] == "single_application"
    assert result["base"] == base
    assert result["head"] == head
    assert result["event"] == "workflow_dispatch"


def test_unsafe_push_sources_fail_safe_with_stable_reasons(tmp_path: Path) -> None:
    repo, head = repository(tmp_path)
    cases = [
        (ZERO_SHA, {}, "base_zero"),
        ("bad", {}, "base_invalid"),
        ("3" * 40, {}, "base_unavailable"),
        (head, {"after": "4" * 40}, "head_mismatch"),
        (head, {"github_sha": "4" * 40}, "head_mismatch"),
        (head, {"release_sha": "5" * 40}, "head_mismatch"),
        (
            head,
            {"after": "6" * 40, "github_sha": "6" * 40, "release_sha": "6" * 40},
            "head_unavailable",
        ),
    ]
    for base, overrides, reason in cases:
        result = classify(repo, base, head, **overrides)
        assert result["profile"] == "full"
        assert result["reason"] == reason


def test_empty_and_nonancestor_ranges_run_full(tmp_path: Path) -> None:
    repo, head = repository(tmp_path)
    empty = classify(repo, head, head)
    unrelated = git(repo, "commit-tree", f"{head}^{{tree}}", input_text="unrelated\n")
    nonancestor = classify(repo, unrelated, head)

    assert empty["reason"] == "diff_empty"
    assert nonancestor["reason"] == "non_ancestor_base"


def test_added_symlink_is_never_focused(tmp_path: Path) -> None:
    repo, base = repository(tmp_path)
    (repo / "api" / "link.py").symlink_to("module.py")
    head = commit(repo, "add symlink")

    result = classify(repo, base, head)

    assert result["profile"] == "full"
    assert result["reason"] == "unsupported_file_mode"


def test_failed_or_malformed_diff_runs_full(tmp_path: Path) -> None:
    repo, base = repository(tmp_path)
    (repo / "api" / "module.py").write_text("CHANGED = True\n", encoding="utf-8")
    head = commit(repo, "head")
    real_git = classifier_module._git

    for diff_result, reason in (
        (subprocess.CompletedProcess([], 1, b"", b"failed"), "diff_failed"),
        (subprocess.CompletedProcess([], 0, b"M\0api/module.py", b""), "diff_unparseable"),
        (subprocess.CompletedProcess([], 0, b"T\0api/module.py\0", b""), "unsupported_status"),
    ):

        def replace_diff(
            repository: Path,
            *arguments: str,
            check: bool = True,
            result: subprocess.CompletedProcess[bytes] = diff_result,
        ) -> subprocess.CompletedProcess[bytes]:
            if arguments[0] == "diff":
                return result
            return real_git(repository, *arguments, check=check)

        with mock.patch("ci.classifier._git", side_effect=replace_diff):
            result = classify(repo, base, head)
        assert result["profile"] == "full"
        assert result["reason"] == reason


def test_real_cross_application_rename_runs_full(tmp_path: Path) -> None:
    repo, base = repository(tmp_path)
    (repo / "studio").mkdir()
    git(repo, "mv", "api/module.py", "studio/module.py")
    head = commit(repo, "move across apps")

    result = classify(repo, base, head)

    assert result["profile"] == "full"
    assert result["reason"] == "cross_application"
