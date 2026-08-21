from __future__ import annotations

import argparse
import dataclasses
import math
import os
import shlex
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
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


# Every component command runs under an explicit wall-clock bound so a hung
# child can never prevent the aggregate report from being emitted.  The default
# exceeds the longest legitimate local suite (the full Django run, the core
# browser run, and the exact production-container build all finish well inside
# one hour); `_docs/ci/change-selective-ci.md` records the override.
DEFAULT_COMPONENT_TIMEOUT_SECONDS = 3600.0
TERMINATE_GRACE_SECONDS = 10.0
GROUP_DRAIN_SECONDS = 10.0
# A timed-out component has no child exit status of its own, so the runner
# records the conventional `timeout(1)` status instead of a bare failure.
TIMEOUT_EXIT_CODE = 124


@dataclasses.dataclass(frozen=True)
class ComponentExecution:
    """One bounded component command run plus its retained output artifact."""

    command: tuple[str, ...]
    output_path: Path
    returncode: int
    timed_out: bool

    @property
    def result(self) -> str:
        if self.timed_out:
            return "timed_out"
        return "success" if self.returncode == 0 else "failure"

    @property
    def exit_code(self) -> int:
        return TIMEOUT_EXIT_CODE if self.timed_out else self.returncode


def _terminate_process_group(
    process: subprocess.Popen[str],
    *,
    grace_seconds: float,
    drain_seconds: float = GROUP_DRAIN_SECONDS,
) -> None:
    """Terminate and then kill a component's whole process group.

    Reaping the direct child does not mean the tree is gone: grandchildren die
    at different times and may still write to the shared output artifact (a
    wrapping `make` prints its error line only after its own child dies).  The
    group gets the same grace window to finish its SIGTERM teardown, is then
    killed, and is finally drained before the caller freezes the component's
    evidence digests.
    """

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace_seconds
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= deadline:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    drained = time.monotonic() + drain_seconds
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= drained:
            break
        time.sleep(0.05)


def execute_component(
    command: Sequence[str],
    *,
    repository: Path,
    environment: Mapping[str, str],
    output_path: Path,
    timeout_seconds: float,
    grace_seconds: float = TERMINATE_GRACE_SECONDS,
) -> ComponentExecution:
    """Run one allowlisted component command under an explicit wall-clock bound.

    The command starts in its own session so an expired bound can terminate and
    then kill every process it spawned (a bare child kill would strand the tree
    `make` started), and its combined output is written straight to
    ``output_path`` so partial output is retained when the bound expires.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            command,
            cwd=repository,
            env=dict(environment),
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_group(process, grace_seconds=grace_seconds)
            # The group has already received SIGKILL if it survived the grace
            # period.  Poll rather than waiting without a bound: an
            # uninterruptible kernel process must not reintroduce the hang the
            # component timeout is designed to prevent.
            polled_returncode = process.poll()
            returncode = polled_returncode if polled_returncode is not None else -signal.SIGKILL
    return ComponentExecution(tuple(command), output_path, returncode, timed_out)


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
            return ("make", "test-django-full")
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
    component_timeout_seconds: float = DEFAULT_COMPONENT_TIMEOUT_SECONDS,
) -> int:
    if not math.isfinite(component_timeout_seconds) or not component_timeout_seconds > 0:
        raise RunnerError("component timeout must be a positive number of seconds")
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
            execution = ComponentExecution(command, output / "selector-plan.json", 0, False)
            dump_json(plan, execution.output_path)
        else:
            execution = execute_component(
                command,
                repository=repository,
                environment=environment,
                output_path=output / f"{component}-output.log",
                timeout_seconds=component_timeout_seconds,
            )
            command_output = execution.output_path.read_text(encoding="utf-8", errors="replace")
            if command_output:
                print(command_output, end="")
            if execution.timed_out:
                print(
                    f"component {component} exceeded its "
                    f"{component_timeout_seconds:g}s wall-clock bound; terminated the "
                    "process group and recording a timed_out result",
                )
            if component == "content_invariants" and invariant_path.is_file():
                execution = dataclasses.replace(execution, output_path=invariant_path)
            elif component == "container" and container_path.is_file():
                execution = dataclasses.replace(execution, output_path=container_path)
        finished = utc_now()
        result = execution.result
        exit_code = execution.exit_code
        result_path = output / f"{component}-result.json"
        environment_path = output / f"{component}-environment.json"
        dump_json(execution_environment, environment_path)
        machine_output = machine_output_claim(
            execution.output_path,
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
                "exit_code": exit_code,
                "output": machine_output["artifact"],
                "result": result,
                "timed_out": execution.timed_out,
            },
            result_path,
        )
        artifact_paths = [result_path, execution.output_path, environment_path]
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
            exit_code=exit_code,
            started_at=started,
            completed_at=finished,
        )
        dump_json(envelope, output / f"{component}-evidence.json")
        failed = failed or result != "success"
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--issue", type=int)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--producer-role", choices=("engineer", "tester"), required=True)
    parser.add_argument(
        "--component-timeout-seconds",
        type=float,
        default=DEFAULT_COMPONENT_TIMEOUT_SECONDS,
        help=(
            "per-component wall-clock bound in seconds; on expiry the component's "
            "process group is terminated and then killed, its partial output is "
            "retained, and its result envelope records a timed_out failure "
            f"(default {DEFAULT_COMPONENT_TIMEOUT_SECONDS:g})"
        ),
    )
    args = parser.parse_args()
    if args.issue is None:
        parser.error(
            "--issue is required for local verification origins: refusing to attribute "
            "verification evidence to a default issue number "
            "(make verification-run VERIFY_ISSUE=<number>)"
        )
    raise SystemExit(
        run_plan(
            plan=load_plan(args.plan),
            repository=args.repository,
            output_directory=args.output_directory,
            issue=args.issue,
            worktree=args.worktree,
            producer_role=args.producer_role,
            component_timeout_seconds=args.component_timeout_seconds,
        )
    )


if __name__ == "__main__":
    main()
