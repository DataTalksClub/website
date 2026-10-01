"""Fail-closed identity checks for the isolated D5.3a proof environment."""

from __future__ import annotations

import json
import sys
import tomllib
from importlib.metadata import distribution
from pathlib import Path

PROOF_ROOT = Path(__file__).parent
REPOSITORY_ROOT = PROOF_ROOT.parents[1]
PROOF_ENVIRONMENT = REPOSITORY_ROOT / ".tmp/source-policy-proof-venv"
RELEASE = json.loads((PROOF_ROOT / "release.json").read_text(encoding="utf-8"))


def _toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _requirement(document: dict) -> str:
    prefix = f"{RELEASE['package']} @ "
    matches = []
    for item in document["project"]["dependencies"]:
        if item.startswith(prefix):
            matches.append(item)
    assert len(matches) == 1
    return matches[0]


def _locked_package(document: dict) -> dict:
    matches = []
    for item in document["package"]:
        if item["name"] == RELEASE["package"]:
            matches.append(item)
    assert len(matches) == 1
    return matches[0]


def _assert_declared_release(project: Path, lock: Path, identity: dict) -> None:
    requirement = _requirement(_toml(project))
    package = _locked_package(_toml(lock))
    assert requirement.endswith(f"@{identity['tag']}")
    assert package["version"] == identity["version"]
    source = package["source"]["git"]
    assert f"rev={identity['tag']}" in source
    assert source.endswith(f"#{identity['commit']}")


def pytest_sessionstart() -> None:
    assert RELEASE["schema_version"] == 1
    assert RELEASE["proof"]["published_wheel_sha256"] == (
        "e9a7139016aa301814790b47c7d99fdfde4a9e8bf0f596a8cee684468516bef0"
    )
    _assert_declared_release(
        REPOSITORY_ROOT / "pyproject.toml", REPOSITORY_ROOT / "uv.lock", RELEASE["runtime"]
    )
    _assert_declared_release(
        PROOF_ROOT / "pyproject.toml", PROOF_ROOT / "uv.lock", RELEASE["proof"]
    )
    installed = distribution(RELEASE["package"])
    assert installed.version == RELEASE["proof"]["version"]
    assert Path(sys.prefix).resolve() == PROOF_ENVIRONMENT.resolve()
    installed_root = Path(str(installed.locate_file(""))).resolve()
    assert PROOF_ENVIRONMENT.resolve() in installed_root.parents
    direct_url = json.loads(installed.read_text("direct_url.json") or "{}")
    assert direct_url["vcs_info"]["requested_revision"] == RELEASE["proof"]["tag"]
    assert direct_url["vcs_info"]["commit_id"] == RELEASE["proof"]["commit"]
