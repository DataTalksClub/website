#!/usr/bin/env python3
"""Checked clone/refresh for the disposable content checkouts.

The ``scripts/content.py checkouts`` and ``checkout`` commands used to inline the
same two destructive loops: any directory with a ``.git`` subdirectory was
reset with ``git reset --hard FETCH_HEAD`` and scrubbed with
``git clean -fdx``, with no check of which repository it belonged to, whose
changes it held, or whether it was a linked worktree (audit REL-10).
Overriding the checkout variable at an editable repository was enough for a
refresh to erase tracked edits and every untracked and ignored file.

This helper is the one clone/refresh boundary for both targets.  It reads the
same tab-separated plan lines the checkout-plan commands print (``stable``,
``repository``, ``branch``, ``checkout``) and, per checkout:

* refuses any path that resolves outside the project's ``.tmp/`` scratch root
  before running any Git command;
* detects linked worktrees through Git's own discovery (a ``.git`` *file*)
  and refuses them, instead of treating one as a clone target;
* refreshes an existing checkout only when this tooling created it (a marker
  inside ``.git``, written at clone time), its ``origin`` URL is exactly the
  registered ``host/repository``, and the worktree has no tracked
  modifications, untracked files, or ignored files -- anything a
  ``reset --hard`` + ``clean -fdx`` pair would destroy; and
* clones anything else fresh, so a checkout this tooling owns is always
  distinguishable from one it must never touch.

Every refusal is a bounded condition code -- never a reflected path or URL.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = PROJECT_ROOT / ".tmp"

#: Written inside ``.git`` at clone time.  It lives under ``.git`` deliberately:
#: it never appears in ``git status`` output, so it cannot make an otherwise
#: clean checkout read as dirty, and it never leaves the local clone.
OWNERSHIP_MARKER = "dtc-disposable-checkout"


class CheckoutRefused(RuntimeError):
    """A fail-closed refusal naming the unmet condition, before any mutation."""


def _refuse(code: str) -> None:
    raise CheckoutRefused(code)


def _git(checkout: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        _refuse(f"git-{arguments[0]}-failed")
    return completed.stdout.strip()


def _normalize_remote(url: str) -> str:
    return url.strip().rstrip("/").removesuffix(".git")


def refresh_one(*, host: str, repository: str, branch: str, checkout: Path) -> str:
    """Clone or refresh one checkout; return the commit it now names."""
    resolved = checkout.expanduser().resolve()
    try:
        resolved.relative_to(SCRATCH_ROOT)
    except ValueError:
        _refuse("checkout-outside-scratch-root")

    git_dir = resolved / ".git"
    if git_dir.is_file():
        # Git's own discovery: a .git file is a linked worktree, whose
        # reset/clean would mutate a tree this tooling does not own.
        _refuse("worktree-unsupported")

    if git_dir.is_dir():
        if not (git_dir / OWNERSHIP_MARKER).is_file():
            _refuse("unowned-checkout")
        origin = _git(resolved, "remote", "get-url", "origin")
        expected = f"{host.rstrip('/')}/{repository}"
        if _normalize_remote(origin) != _normalize_remote(expected):
            _refuse("origin-mismatch")
        if _git(resolved, "status", "--porcelain", "--ignored"):
            _refuse("dirty-checkout")
        _git(resolved, "fetch", "--quiet", "origin", branch)
        _git(resolved, "checkout", "--quiet", branch)
        _git(resolved, "reset", "--hard", "--quiet", "FETCH_HEAD")
        _git(resolved, "clean", "--quiet", "-fdx")
    else:
        if resolved.exists() and any(resolved.iterdir()):
            _refuse("not-a-checkout")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--branch",
                branch,
                f"{host.rstrip('/')}/{repository}",
                str(resolved),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            _refuse("clone-failed")
        (git_dir / OWNERSHIP_MARKER).write_text(
            "created by scripts/checkout_refresh.py; safe to reset\n",
            encoding="utf-8",
        )
    return _git(resolved, "rev-parse", "HEAD")


def run(host: str, plan_lines: list[str]) -> int:
    for line in plan_lines:
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            print(
                json.dumps({"error": "plan-line-invalid"}, indent=2),
                file=sys.stderr,
            )
            return 1
        stable, repository, branch, checkout = fields
        try:
            head = refresh_one(
                host=host,
                repository=repository,
                branch=branch,
                checkout=Path(checkout),
            )
        except CheckoutRefused as refusal:
            print(
                json.dumps({"stable": stable, "error": str(refusal)}, indent=2),
                file=sys.stderr,
            )
            return 1
        print(f"{stable} {head}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        required=True,
        help="Git host base the plan's owner/repository is cloned from",
    )
    arguments = parser.parse_args(argv)
    return run(arguments.host, sys.stdin.read().splitlines())


if __name__ == "__main__":
    raise SystemExit(main())
