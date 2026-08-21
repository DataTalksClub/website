from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ci.evidence import artifact_records, machine_output_claim
from ci.selection import ChangeRecord, classify_records


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def repository_with_change(
    tmp_path: Path,
    changed: dict[str, str],
    *,
    initial: dict[str, str] | None = None,
) -> tuple[Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "ci@example.invalid")
    git(repository, "config", "user.name", "CI Test")
    initial = initial or {path: "initial\n" for path in changed}
    initial.setdefault("README.md", "baseline\n")
    for path, body in initial.items():
        destination = repository / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "baseline")
    base = git(repository, "rev-parse", "HEAD")
    for path, body in changed.items():
        destination = repository / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "candidate")
    return repository, base, git(repository, "rev-parse", "HEAD")


def selection_for(paths: tuple[str, ...], *, base: str, head: str):
    records = tuple(ChangeRecord("M", (path,)) for path in paths)
    return classify_records(records, event="push", base=base, head=head), records


def component_output(
    root: Path,
    plan: dict[str, Any],
    component: str,
    *,
    result: str = "success",
    path: Path | None = None,
    screenshot: dict[str, Any] | None = None,
):
    output = path or root / f"{component}-output.log"
    if component == "selector":
        output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif component == "container":
        output.write_text(
            json.dumps(
                {
                    "assertions": ["container_contract"],
                    "revision": plan["head"],
                    "schema_version": 1,
                    "status": "pass",
                }
            )
            + "\n",
            encoding="utf-8",
        )
    elif component == "screenshots":
        if not output.exists():
            output.write_bytes(b"screenshot")
    else:
        body = "2 passed, 1 skipped in 0.01s\n"
        if component == "playwright":
            body += (
                "DTC_FLAKE_POLICY_V1 attempted=3 passed=2 failed=0 skipped=1 "
                "rerun=0 quarantined=0 complete=1\n"
            )
        output.write_text(body, encoding="utf-8")
    records = artifact_records((output,), root=root)
    claim = machine_output_claim(
        output,
        root=root,
        component=component,
        plan=plan,
        result=result,
        screenshot=screenshot,
    )
    return records, claim
