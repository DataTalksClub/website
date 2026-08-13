from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from scripts.sync_course_platform import (
    SourcePin,
    SyncFailure,
    apply_plan,
    build_plan,
    entries_at_commit,
    prepare_source,
    render_source_pin,
)
from scripts.verify_course_platform_adoption import (
    MANIFEST,
    PATCH_MANIFEST,
    render_manifest,
)


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "cmp"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "sync@example.invalid")
    _git(source, "config", "user.name", "Sync Fixture")
    for relative, content in {
        "accounts/example.py": "baseline\n",
        "cadmin/templates/cadmin/base.html": "template\n",
        "courses/migrations/0001_initial.py": "migration\n",
        ".claude/notes.md": "metadata\n",
    }.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "baseline")
    return source, _git(source, "rev-parse", "HEAD")


def _target(tmp_path: Path, source: Path, current: str) -> Path:
    target = tmp_path / "website"
    (target / "_docs/adoption/course-platform").mkdir(parents=True)
    entries = entries_at_commit(source, current)
    for entry in entries:
        destination = target / entry.destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / entry.source).read_bytes())
    (target / MANIFEST).write_text(render_manifest(entries), encoding="utf-8")
    (target / PATCH_MANIFEST).write_text(
        "destination_path\tsize_bytes\tsha256\trationale\n", encoding="utf-8"
    )
    (target / "_docs/adoption/course-platform/source-pin.json").write_text(
        render_source_pin(
            SourcePin(
                repository=str(source),
                commit=current,
                checkout=".tmp/cmp-source-sync",
            )
        ),
        encoding="utf-8",
    )
    (target / "_docs/adoption/course-platform/README.md").write_text(
        f"Pinned source commit: `{current}`\n", encoding="utf-8"
    )
    return target


def _commit_change(source: Path, relative: str, content: str, message: str) -> str:
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", message)
    return _git(source, "rev-parse", "HEAD")


def test_dry_run_noop_has_no_copy_entries(tmp_path: Path) -> None:
    source, current = _source(tmp_path)
    target = _target(tmp_path, source, current)
    before = (target / MANIFEST).read_bytes()

    plan = build_plan(
        repo=target,
        source=source,
        current_pin=SourcePin(str(source), current, ".tmp/cmp-source-sync"),
        target_commit=current,
    )

    assert plan.changed_paths == []
    assert plan.copy_entries == []
    assert plan.can_apply
    assert (target / MANIFEST).read_bytes() == before


def test_excluded_change_is_reported_without_copying(tmp_path: Path) -> None:
    source, current = _source(tmp_path)
    target = _target(tmp_path, source, current)
    target_commit = _commit_change(source, ".claude/notes.md", "metadata changed\n", "metadata")
    _git(source, "checkout", "--detach", target_commit)

    plan = build_plan(
        repo=target,
        source=source,
        current_pin=SourcePin(str(source), current, ".tmp/cmp-source-sync"),
        target_commit=target_commit,
    )

    assert not plan.fatal_errors
    assert not plan.copy_entries
    assert len(plan.excluded_paths) == 1
    assert plan.excluded_paths[0].new_path == ".claude/notes.md"


def test_source_deletion_is_fail_closed(tmp_path: Path) -> None:
    source, current = _source(tmp_path)
    target = _target(tmp_path, source, current)
    (source / "accounts/example.py").unlink()
    _git(source, "add", ".")
    _git(source, "commit", "-m", "delete account")
    target_commit = _git(source, "rev-parse", "HEAD")

    plan = build_plan(
        repo=target,
        source=source,
        current_pin=SourcePin(str(source), current, ".tmp/cmp-source-sync"),
        target_commit=target_commit,
    )

    assert any("deletion" in error for error in plan.fatal_errors)
    assert not plan.copy_entries


def test_dirty_source_repository_is_rejected(tmp_path: Path) -> None:
    source, current = _source(tmp_path)
    (source / "uncommitted.txt").write_text("do not copy\n", encoding="utf-8")

    with pytest.raises(SyncFailure, match="source repository is dirty"):
        prepare_source(
            repo=tmp_path,
            repository=str(source),
            source_ref=current,
            current_commit=current,
            checkout=tmp_path / "checkout",
        )




def test_non_overlaid_change_is_copied_and_manifest_is_updated(tmp_path: Path) -> None:
    source, current = _source(tmp_path)
    target = _target(tmp_path, source, current)
    target_commit = _commit_change(source, "accounts/example.py", "updated\n", "update account")
    _git(source, "checkout", "--detach", target_commit)

    plan = build_plan(
        repo=target,
        source=source,
        current_pin=SourcePin(str(source), current, ".tmp/cmp-source-sync"),
        target_commit=target_commit,
    )
    assert [entry.destination for entry in plan.copy_entries] == ["accounts/example.py"]

    apply_plan(repo=target, source=source, plan=plan)

    assert (target / "accounts/example.py").read_text(encoding="utf-8") == "updated\n"
    assert f'"source_commit": "{target_commit}"' in (
        target / "_docs/adoption/course-platform/source-pin.json"
    ).read_text(encoding="utf-8")
    assert hashlib.sha256((target / "accounts/example.py").read_bytes()).hexdigest() in (
        (target / MANIFEST).read_text(encoding="utf-8")
    )


def test_cadmin_mapping_is_preserved(tmp_path: Path) -> None:
    source, current = _source(tmp_path)
    target = _target(tmp_path, source, current)
    target_commit = _commit_change(
        source, "cadmin/templates/cadmin/new.html", "new\n", "add studio template"
    )
    _git(source, "checkout", "--detach", target_commit)

    plan = build_plan(
        repo=target,
        source=source,
        current_pin=SourcePin(str(source), current, ".tmp/cmp-source-sync"),
        target_commit=target_commit,
    )

    assert [entry.destination for entry in plan.copy_entries] == [
        "studio_courses/templates/studio_courses/new.html"
    ]


def test_overlay_change_is_reported_and_preserved(tmp_path: Path) -> None:
    source, current = _source(tmp_path)
    target = _target(tmp_path, source, current)
    overlay = target / "accounts/example.py"
    overlay.write_text("target overlay\n", encoding="utf-8")
    overlay_sha = hashlib.sha256(overlay.read_bytes()).hexdigest()
    (target / PATCH_MANIFEST).write_text(
        "destination_path\tsize_bytes\tsha256\trationale\n"
        f"accounts/example.py\t{overlay.stat().st_size}\t{overlay_sha}\tlocal seam\n",
        encoding="utf-8",
    )
    target_commit = _commit_change(source, "accounts/example.py", "upstream\n", "upstream change")
    _git(source, "checkout", "--detach", target_commit)

    plan = build_plan(
        repo=target,
        source=source,
        current_pin=SourcePin(str(source), current, ".tmp/cmp-source-sync"),
        target_commit=target_commit,
    )

    assert not plan.can_apply
    assert len(plan.conflicts) == 1
    assert plan.conflicts[0].old_source_sha256 != plan.conflicts[0].new_source_sha256
    with pytest.raises(SyncFailure):
        apply_plan(repo=target, source=source, plan=plan)
    assert overlay.read_text(encoding="utf-8") == "target overlay\n"


def test_migration_rewrite_is_fail_closed(tmp_path: Path) -> None:
    source, current = _source(tmp_path)
    target = _target(tmp_path, source, current)
    target_commit = _commit_change(
        source, "courses/migrations/0001_initial.py", "rewritten\n", "rewrite migration"
    )
    _git(source, "checkout", "--detach", target_commit)

    plan = build_plan(
        repo=target,
        source=source,
        current_pin=SourcePin(str(source), current, ".tmp/cmp-source-sync"),
        target_commit=target_commit,
    )

    assert any("migration replacement" in error for error in plan.fatal_errors)
    assert plan.copy_entries == []
