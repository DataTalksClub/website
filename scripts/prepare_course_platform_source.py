#!/usr/bin/env python3
"""Provision the clean, detached CMP checkout recorded in source-pin.json."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_course_platform_adoption import SOURCE_PIN


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _metadata(repo: Path) -> tuple[str, str, Path]:
    path = repo / SOURCE_PIN
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid source pin metadata: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise SystemExit(f"unsupported source pin metadata: {path}")
    repository = value.get("source_repository")
    commit = value.get("source_commit")
    checkout_value = value.get("source_checkout")
    if not (
        isinstance(repository, str)
        and isinstance(commit, str)
        and isinstance(checkout_value, str)
        and repository.strip()
        and commit.strip()
        and checkout_value.strip()
    ):
        raise SystemExit(f"source pin metadata is incomplete: {path}")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise SystemExit(f"source pin commit is not an exact lowercase SHA: {path}")
    checkout = Path(checkout_value)
    return repository, commit, checkout if checkout.is_absolute() else repo / checkout


def provision(*, repo: Path, override: Path | None = None) -> Path:
    repository, commit, recorded_checkout = _metadata(repo)
    checkout = override or recorded_checkout
    if checkout.exists():
        if not (checkout / ".git").exists():
            raise SystemExit(f"source checkout is not a Git repository: {checkout}")
        status = _git(checkout, "status", "--porcelain=v1", "--untracked-files=all")
        if status:
            raise SystemExit(f"source checkout is dirty: {checkout}")
    else:
        checkout.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "git",
            "clone",
            "--no-local",
            "--no-tags",
            "--no-checkout",
            repository,
            str(checkout),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)

    _git(checkout, "checkout", "--detach", commit)
    if _git(checkout, "rev-parse", "HEAD") != commit:
        raise SystemExit(f"source checkout did not resolve to pinned commit: {commit}")
    if _git(checkout, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SystemExit(f"source checkout is dirty after provisioning: {checkout}")
    print(f"provisioned clean CMP checkout {checkout} at {commit}")
    return checkout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-checkout", type=Path, default=None)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    provision(repo=repo, override=args.source_checkout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
