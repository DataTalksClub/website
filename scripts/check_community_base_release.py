"""Compare the pinned community-base tag with the latest upstream release.

Plan issue D0.2, step 2. Read-only: this script never edits the pin. It
reports one of three outcomes as the exit code, so the calling workflow
can act without re-parsing:

- 0 and ``ALREADY_LATEST <tag>``: the pin matches the latest release;
- 2 and ``NEWER <pinned> <latest>``: a newer release exists and the
  caller should open the deduplicated update advisory;
- 1 with an actionable stderr message: the release lookup itself failed
  (auth, rate limit, network) — a silent skip would hide new releases
  forever, so this must stay loud.

Stdlib only, mirroring check_community_base_source.py: the advisory runs
before `uv sync` in the same bare runner. ``COMMUNITY_BASE_RELEASES_URL``
overrides the lookup endpoint for tests.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

PACKAGE_NAME = "community-base"
GITHUB_SLUG = "datatalksclub/community-base"
DEFAULT_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_SLUG}/releases/latest"
PIN_RE = re.compile(
    rf"^{PACKAGE_NAME}\s*@\s*git\+https://github\.com/{GITHUB_SLUG}@(v\d+[0-9A-Za-z.\-]*)$",
    re.IGNORECASE,
)
VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def pinned_tag(repo: Path) -> str:
    try:
        with open(repo / "pyproject.toml", "rb") as handle:
            pyproject = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"cannot read pyproject.toml: {exc}") from None
    for entry in pyproject.get("project", {}).get("dependencies", []):
        if not isinstance(entry, str):
            continue
        match = PIN_RE.match(entry.strip())
        if match is not None:
            return match.group(1)
    raise RuntimeError(
        f"no pinned {PACKAGE_NAME} git tag found in pyproject.toml; "
        "run the D0.1a pin first and keep check_community_base_source.py green."
    )


def version_key(tag: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.match(tag)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def latest_release_tag(releases_url: str, token: str | None) -> str:
    request = urllib.request.Request(
        releases_url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        document = json.load(response)
    tag = document.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("the latest-release document has no tag_name")
    return tag


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    try:
        pinned = pinned_tag(repo)
    except RuntimeError as exc:
        return fail(str(exc))

    import os

    releases_url = os.getenv("COMMUNITY_BASE_RELEASES_URL") or DEFAULT_RELEASES_URL
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    try:
        latest = latest_release_tag(releases_url, token)
    except Exception as exc:  # noqa: BLE001 -- every lookup failure must stay loud
        return fail(
            f"cannot look up the latest {GITHUB_SLUG} release from {releases_url}: {exc}. "
            "Check the token/rate limit and rerun; a silent skip would hide new releases."
        )

    pinned_key = version_key(pinned)
    latest_key = version_key(latest)
    if pinned_key is None or latest_key is None:
        print(f"ALREADY_LATEST {pinned} (cannot order {latest!r}; compare manually)")
        return 0
    if latest_key > pinned_key:
        print(f"NEWER {pinned} {latest}")
        return 2
    print(f"ALREADY_LATEST {pinned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
