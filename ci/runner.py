from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ci.evidence import (
    ALLOWLISTED_CONFIG,
    artifact_records,
    build_envelope,
    environment_fingerprint,
    machine_output_claim,
    utc_now,
    worktree_manifest,
)
from ci.verification import AUTOMATED_COMPONENTS, dump_json, load_plan

RUN_ORDER = (
    "selector",
    "evidence_validation",
    "quality",
    "compatibility",
    "content_invariants",
    "django",
    "playwright",
    "container",
)


class RunnerError(ValueError):
    """A local verification plan cannot be executed safely."""


def command_for(plan: Mapping[str, Any], component: str) -> tuple[str, ...]:
    if component not in AUTOMATED_COMPONENTS:
        raise RunnerError("component is not an automated verification component")
    if component == "selector":
        return ("make", "verification-plan")
    if component == "evidence_validation":
        return ("make", "test-ci")
    if component == "quality":
        return (
            "uv",
            "run",
            "--frozen",
            "python",
            "-m",
            "ci.quality_contract",
            "--repository",
            ".",
        )
    if component == "compatibility":
        return (
            "make",
            "compatibility-source-artifacts-check",
            "compatibility-artifacts-check",
            "check-links",
            "check-seo",
        )
    if component == "content_invariants":
        return ("make", "verification-content-invariants")
    if component == "django":
        if plan["profile"] == "full":
            return ("make", "test")
        if not plan["test_labels"]:
            raise RunnerError("focused Django verification requires selected test labels")
        return ("make", "test-ci-focused")
    if component == "playwright":
        target = "test-playwright" if plan["browser_profile"] == "full" else "test-playwright-core"
        return ("make", target)
    if component == "container":
        return ("make", "verification-container")
    raise RunnerError("component has no allowlisted local command")


def run_plan(
    *,
    plan: Mapping[str, Any],
    repository: str | Path,
    output_directory: str | Path,
    issue: int,
    worktree: str,
    producer_role: str,
) -> int:
    repository = Path(repository)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    actual_head = subprocess.run(
        ["git", "-C", os.fspath(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual_head != plan["head"]:
        raise RunnerError("verification plan head does not match the checked-out source")
    if plan["source_mode"] == "worktree":
        _manifest, source_tree = worktree_manifest(repository, actual_head)
        if source_tree["manifest_sha256"] != plan["source_tree"]["manifest_sha256"]:
            raise RunnerError("worktree content changed after the verification plan was created")
    failed = False
    selection_path = output / "ci-selection.json"
    dump_json(plan["legacy_selection"], selection_path)
    for component in RUN_ORDER:
        item = plan["components"][component]
        if item["disposition"] != "rerun":
            continue
        command = command_for(plan, component)
        environment = os.environ.copy()
        for name in ALLOWLISTED_CONFIG:
            environment.pop(name, None)
        environment.update(item["environment"]["allowlisted_config"])
        if component == "django":
            environment["CI_SELECTION_PATH"] = os.fspath(selection_path)
        invariant_path = output / "content-invariants.json"
        if component == "content_invariants":
            environment["VERIFY_PLAN"] = environment.get("VERIFY_PLAN", "")
            if not environment["VERIFY_PLAN"]:
                plan_path = output / "runner-plan.json"
                dump_json(plan, plan_path)
                environment["VERIFY_PLAN"] = os.fspath(plan_path)
            environment["VERIFY_INVARIANT"] = os.fspath(invariant_path)
        container_path = output / "container-check.json"
        if component == "container":
            environment["VERIFY_CONTAINER_OUTPUT"] = os.fspath(container_path)
        execution_environment = environment_fingerprint(environment)
        if execution_environment != item["environment"]:
            raise RunnerError("component execution environment does not match the plan")
        started = utc_now()
        if component == "selector":
            completed = subprocess.CompletedProcess(command, 0, stdout="")
            output_path = output / "selector-plan.json"
            dump_json(plan, output_path)
        else:
            completed = subprocess.run(
                command,
                cwd=repository,
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            command_output = completed.stdout or ""
            if command_output:
                print(command_output, end="")
            output_path = output / f"{component}-output.log"
            output_path.write_text(command_output, encoding="utf-8")
            if component == "content_invariants" and invariant_path.is_file():
                output_path = invariant_path
            elif component == "container" and container_path.is_file():
                output_path = container_path
        finished = utc_now()
        result = "success" if completed.returncode == 0 else "failure"
        result_path = output / f"{component}-result.json"
        environment_path = output / f"{component}-environment.json"
        dump_json(execution_environment, environment_path)
        machine_output = machine_output_claim(
            output_path,
            root=output,
            component=component,
            plan=plan,
            result=result,
        )
        dump_json(
            {
                "command": shlex.join(command),
                "component": component,
                "counts": machine_output["counts"],
                "exit_code": completed.returncode,
                "output": machine_output["artifact"],
                "result": result,
            },
            result_path,
        )
        artifact_paths = [result_path, output_path, environment_path]
        envelope = build_envelope(
            plan=plan,
            component=component,
            result=result,
            origin={
                "issue": issue,
                "kind": "local",
                "producer_role": producer_role,
                "worktree": worktree,
            },
            command=item["command"],
            execution_environment=execution_environment,
            artifacts=artifact_records(artifact_paths, root=output),
            machine_output=machine_output,
            exit_code=completed.returncode,
            started_at=started,
            completed_at=finished,
        )
        dump_json(envelope, output / f"{component}-evidence.json")
        failed = failed or completed.returncode != 0
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--issue", type=int, default=113)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--producer-role", choices=("engineer", "tester"), required=True)
    args = parser.parse_args()
    raise SystemExit(
        run_plan(
            plan=load_plan(args.plan),
            repository=args.repository,
            output_directory=args.output_directory,
            issue=args.issue,
            worktree=args.worktree,
            producer_role=args.producer_role,
        )
    )


if __name__ == "__main__":
    main()
