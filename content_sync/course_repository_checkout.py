"""Whether a local checkout's commit is one a public reader can resolve.

A unit records its provenance as a repository plus a commit SHA, and every
public affordance built from that provenance -- the "Edit on GitHub" link, the
raw image URL, a source path a reader follows -- assumes the commit is on the
public repository.  Importing a commit that exists only on a local clone
publishes pages whose source links can only 404.

Reachability is read from the checkout's own remote-tracking branches, so this
stays offline and deterministic: a checkout cloned from a private or local
mirror simply has no branch of the public remote containing the commit.

This is a property of a *checkout*, not of ingestion, which is why it lives
beside the pull entry point rather than inside the shared ingestion service.
The push route cannot need it: codeload only ever serves a commit GitHub has.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

GIT_TIMEOUT_SECONDS = 30


def _git(root: Path, *arguments: str) -> str | None:
    """Return trimmed git output for the checkout, or ``None`` when git fails."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def public_repository_urls(owner: str, name: str) -> frozenset[str]:
    """Return the URL spellings that identify one public GitHub repository."""

    path = f"{owner}/{name}".casefold()
    return frozenset(
        {
            f"https://github.com/{path}",
            f"https://github.com/{path}.git",
            f"http://github.com/{path}",
            f"http://github.com/{path}.git",
            f"ssh://git@github.com/{path}",
            f"ssh://git@github.com/{path}.git",
            f"git@github.com:{path}",
            f"git@github.com:{path}.git",
        }
    )


def _public_remote_names(root: Path, *, owner: str, name: str) -> tuple[str, ...]:
    listing = _git(root, "config", "--get-regexp", r"^remote\..*\.url$")
    if listing is None:
        return ()
    expected = public_repository_urls(owner, name)
    remotes: list[str] = []
    for line in listing.splitlines():
        key, _, url = line.partition(" ")
        if not url or not key.startswith("remote.") or not key.endswith(".url"):
            continue
        if url.strip().rstrip("/").casefold() in expected:
            remotes.append(key[len("remote.") : -len(".url")])
    return tuple(remotes)


def commit_is_public(root: Path, *, owner: str, name: str, commit_sha: str) -> bool:
    """Return whether the commit is on a branch of the public GitHub remote."""

    remotes = _public_remote_names(root, owner=owner, name=name)
    if not remotes:
        return False
    containing = _git(root, "branch", "--remotes", "--contains", commit_sha, "--format=%(refname)")
    if not containing:
        return False
    prefixes = tuple(f"refs/remotes/{remote}/" for remote in remotes)
    return any(line.strip().startswith(prefixes) for line in containing.splitlines())


__all__ = ("commit_is_public", "public_repository_urls")
