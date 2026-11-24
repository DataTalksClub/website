"""Fail-closed application-CI verdict gate for the deploy workflows (REL-01).

The repository's real verification -- quality, Django, Playwright, screenshots,
and the container-image checks -- lives in ``.github/workflows/ci.yml`` and is
aggregated by its ``ci-gate`` job.  Automatic delivery must never proceed on a
reduced acceptance policy, so the deploy workflows depend on that aggregate: for
one exact source SHA, this module demands a completed run of the CI workflow in
this repository whose ``ci-gate`` job concluded ``success``.

Everything fails closed.  A missing, pending, canceled, or unsuccessful verdict
exits nonzero, and a green run for any other SHA authorizes nothing.

One narrow exception exists for push-driven staging (``--superseded-on``): the
CI workflow's concurrency group keeps a single pending run, so GitHub cancels
the queued CI of a main commit as soon as a newer main commit is pushed.  When
the CI run for the SHA concluded ``cancelled`` with no failed job *and* the
branch head has moved strictly ahead of that SHA, the SHA is ``superseded``: its release is skipped
(exit 0, ``verdict=superseded``) because the newer head carries the change and
gets its own full verdict and deploy.  A cancel without a successor -- the SHA
is still the head, or not an ancestor of it -- and every real failure still
block.  The
decision logic is code-owned here and exercised by
``ci/tests/test_deploy_ci_gate.py``; the workflow steps only wire arguments.
Stdlib-only, so both deploy workflows can run it with the runner's ``python3``
(and so ``deploy/__init__.py`` stays importable without dependencies).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: The one job whose success *is* the repository's verification aggregate.
GATE_JOB_NAME = "ci-gate"

GREEN_CONCLUSION = "success"

#: The only non-green conclusion a newer branch head can explain away.
CANCELLED_CONCLUSION = "cancelled"

#: Job conclusions that do not by themselves show a verification failure.
#: ci.yml cancels its own run after a quality failure, which leaves the run
#: ``cancelled`` but its ``ci-gate`` job ``failure``; a run cancelled while
#: still queued has no jobs at all.
NON_FAILING_JOB_CONCLUSIONS = frozenset({None, "success", "skipped", "cancelled", "neutral"})

SHA_LENGTH = 40


@dataclass(frozen=True)
class VerdictDecision:
    """One classification of what GitHub returned for a source SHA."""

    state: str  # "green" | "superseded" | "blocked" | "pending" | "missing"
    reason: str
    run_id: int | None = None
    conclusion: str | None = None
    run_url: str | None = None


def decide(
    runs: list[dict[str, Any]],
    *,
    workflow_path: str,
    expected_sha: str,
    repository: str,
) -> VerdictDecision:
    """Classify the workflow runs returned for one exact source SHA.

    The API query narrows the list to the requested workflow and SHA, but this
    decision trusts only fields it re-checked itself: the workflow path, the
    full 40-character SHA, and the head repository.  The newest completed run
    for the SHA is authoritative -- an older green run beneath a newer failed
    one is stale evidence, and a rerun still in flight reads as ``pending``
    until it concludes.
    """

    completed: dict[str, Any] | None = None
    active_runs = 0
    for run in runs:
        if run.get("path") != workflow_path:
            continue
        if run.get("head_sha") != expected_sha:
            continue
        head_repository = (run.get("head_repository") or {}).get("full_name")
        if head_repository is not None and head_repository != repository:
            continue
        if run.get("status") == "completed":
            if completed is None:
                completed = run
        else:
            active_runs += 1

    if completed is not None:
        conclusion = completed.get("conclusion")
        if conclusion == GREEN_CONCLUSION:
            return VerdictDecision(
                "green",
                f"{workflow_path} run {completed.get('id')} concluded success for {expected_sha}",
                run_id=completed.get("id"),
                conclusion=conclusion,
                run_url=completed.get("html_url"),
            )
        return VerdictDecision(
            "blocked",
            f"{workflow_path} run {completed.get('id')} for {expected_sha} "
            f"concluded {conclusion!r}; only a successful verification "
            "authorizes a release",
            run_id=completed.get("id"),
            conclusion=conclusion,
            run_url=completed.get("html_url"),
        )
    if active_runs:
        return VerdictDecision(
            "pending",
            f"{active_runs} {workflow_path} run(s) for {expected_sha} have not finished",
        )
    return VerdictDecision(
        "missing",
        f"no {workflow_path} run exists in {repository} for {expected_sha}",
    )


def green_requires_gate_job(
    jobs: list[dict[str, Any]],
    *,
    run_id: int | None,
) -> VerdictDecision | None:
    """Confirm the candidate run actually executed the aggregate gate.

    A probe-mode dispatch of the CI workflow can conclude ``success`` while
    every verification job is skipped, so the run's own green conclusion is not
    acceptance evidence: its ``ci-gate`` job must have concluded ``success``.
    ``None`` means the evidence is acceptable.
    """

    for job in jobs:
        if job.get("name") != GATE_JOB_NAME:
            continue
        if job.get("conclusion") == GREEN_CONCLUSION:
            return None
        return VerdictDecision(
            "blocked",
            f"run {run_id} concluded success but its {GATE_JOB_NAME} job did not "
            f"(conclusion {job.get('conclusion')!r}); skipped verification is "
            "not green",
            run_id=run_id,
            conclusion=job.get("conclusion"),
        )
    return VerdictDecision(
        "blocked",
        f"run {run_id} concluded success but has no {GATE_JOB_NAME} job; "
        "it is not a verification run",
        run_id=run_id,
    )


def cancel_hides_a_failure(
    blocked: VerdictDecision,
    jobs: list[dict[str, Any]],
) -> VerdictDecision | None:
    """Keep a cancelled run blocked when any of its jobs actually failed.

    ``None`` means the cancel carries no failure evidence (e.g. the run was
    cancelled while still queued) and may be judged for supersession.
    """

    failed = sorted(
        f"{job.get('name')}={job.get('conclusion')}"
        for job in jobs
        if job.get("conclusion") not in NON_FAILING_JOB_CONCLUSIONS
    )
    if not failed:
        return None
    return dataclasses.replace(
        blocked,
        reason=f"{blocked.reason}; the cancelled run has failed jobs ({', '.join(failed)}), "
        "so it is a real failure, not a superseded SHA",
    )


def superseded_by(
    blocked: VerdictDecision,
    *,
    compare: dict[str, Any],
    branch: str,
    branch_head: str,
    expected_sha: str,
) -> VerdictDecision | None:
    """Reclassify a cancelled verdict whose SHA a newer branch head replaced.

    ``compare`` is GitHub's ``compare/<expected_sha>...<branch_head>`` payload.
    Only a cancel qualifies, and only when the head is a different commit that
    is strictly ahead of the SHA (``status == "ahead"``), i.e. it contains the
    SHA.  ``None`` keeps the original block.
    """

    if blocked.conclusion != CANCELLED_CONCLUSION:
        return None
    if not _is_full_sha(branch_head) or branch_head == expected_sha:
        return None
    if compare.get("status") != "ahead" or int(compare.get("ahead_by") or 0) < 1:
        return None
    return VerdictDecision(
        "superseded",
        f"{blocked.reason}; {branch} has moved {compare.get('ahead_by')} "
        f"commit(s) ahead to {branch_head}, whose own CI verdict gates its "
        "release, so this superseded SHA is skipped",
        run_id=blocked.run_id,
        conclusion=blocked.conclusion,
        run_url=blocked.run_url,
    )


def default_gh_api(arguments: list[str]) -> str:
    completed = subprocess.run(
        ["gh", "api", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"gh api {arguments[0]} failed: {completed.stderr.strip()}")
    return completed.stdout


def _is_full_sha(value: str) -> bool:
    return len(value) == SHA_LENGTH and all(character in "0123456789abcdef" for character in value)


def require(
    argv: list[str] | None = None,
    *,
    api: Callable[[list[str]], str] = default_gh_api,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Poll for a green CI verdict on one SHA; exit 0 only when it exists.

    With ``--superseded-on BRANCH`` a cancelled verdict for a SHA that BRANCH
    has moved past also exits 0, as ``superseded``.  ``--verdict-output``
    receives ``verdict=green`` or ``verdict=superseded`` so the caller can
    release only on green.

    A completed run is authoritative the moment it appears, so a failure (or
    any non-success conclusion) refuses immediately instead of waiting for a
    rerun that nobody requested.  Only genuinely unfinished verification
    (pending runs, or the startup race before the run record exists) keeps
    polling, and only until the bounded deadline.
    """

    parser = argparse.ArgumentParser(
        prog="python -m deploy.ci_verdict",
        description=__doc__,
    )
    parser.add_argument("command", choices=("require",))
    parser.add_argument(
        "--workflow",
        required=True,
        help="Workflow file name whose verdict is required, e.g. ci.yml",
    )
    parser.add_argument(
        "--repository",
        required=True,
        help="owner/name of this repository",
    )
    parser.add_argument(
        "--sha",
        required=True,
        help="Full 40-character lowercase source commit",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=5400,
        help="Bounded wait for still-running verification (default 5400)",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=60,
        help="Seconds between polls while verification is unfinished (default 60)",
    )
    parser.add_argument(
        "--superseded-on",
        metavar="BRANCH",
        help=(
            "Treat a cancelled verdict as superseded (exit 0) when BRANCH's head "
            "is strictly ahead of --sha; any other non-success still blocks"
        ),
    )
    parser.add_argument(
        "--verdict-output",
        metavar="PATH",
        help="Append verdict=<green|superseded> here on success (e.g. $GITHUB_OUTPUT)",
    )
    arguments = parser.parse_args(argv)

    if not _is_full_sha(arguments.sha):
        print(
            json.dumps(
                {
                    "state": "blocked",
                    "reason": "--sha must be exactly 40 lowercase hex characters",
                }
            ),
            flush=True,
        )
        return 1

    workflow_path = f".github/workflows/{arguments.workflow}"
    deadline = monotonic() + arguments.timeout_seconds
    last_reported_state: str | None = None
    last_error: str | None = None

    def report(decision: VerdictDecision) -> None:
        nonlocal last_reported_state
        if decision.state == last_reported_state:
            return
        print(json.dumps(dataclasses.asdict(decision), sort_keys=True), flush=True)
        last_reported_state = decision.state

    while True:
        decision: VerdictDecision | None = None
        try:
            runs_response = api(
                [
                    f"repos/{arguments.repository}/actions/workflows/"
                    f"{arguments.workflow}/runs?head_sha={arguments.sha}&per_page=20"
                ]
            )
            decision = decide(
                json.loads(runs_response).get("workflow_runs", []),
                workflow_path=workflow_path,
                expected_sha=arguments.sha,
                repository=arguments.repository,
            )
            if decision.state == "green":
                jobs_response = api(
                    [
                        f"repos/{arguments.repository}/actions/runs/"
                        f"{decision.run_id}/jobs?per_page=100"
                    ]
                )
                gate_failure = green_requires_gate_job(
                    json.loads(jobs_response).get("jobs", []),
                    run_id=decision.run_id,
                )
                if gate_failure is not None:
                    decision = gate_failure
            elif (
                decision.state == "blocked"
                and arguments.superseded_on
                and decision.conclusion == CANCELLED_CONCLUSION
            ):
                cancelled_jobs = api(
                    [
                        f"repos/{arguments.repository}/actions/runs/{decision.run_id}/jobs?per_page=100"
                    ]
                )
                hidden_failure = cancel_hides_a_failure(
                    decision, json.loads(cancelled_jobs).get("jobs", [])
                )
                if hidden_failure is not None:
                    report(hidden_failure)
                    return 1
                branch_response = api(
                    [f"repos/{arguments.repository}/branches/{arguments.superseded_on}"]
                )
                branch_head = str(
                    (json.loads(branch_response).get("commit") or {}).get("sha") or ""
                )
                if _is_full_sha(branch_head) and branch_head != arguments.sha:
                    compare_response = api(
                        [f"repos/{arguments.repository}/compare/{arguments.sha}...{branch_head}"]
                    )
                    decision = (
                        superseded_by(
                            decision,
                            compare=json.loads(compare_response),
                            branch=arguments.superseded_on,
                            branch_head=branch_head,
                            expected_sha=arguments.sha,
                        )
                        or decision
                    )
        except (RuntimeError, ValueError) as error:
            # A transient API failure must not look like evidence either way:
            # keep polling until the deadline, then fail closed with the last
            # error in the diagnostic.
            last_error = str(error)

        if decision is None:
            decision = VerdictDecision(
                "pending",
                f"the CI verdict for {arguments.sha} could not be read: {last_error}",
            )
        report(decision)

        if decision.state in ("green", "superseded"):
            if arguments.verdict_output:
                with open(arguments.verdict_output, "a", encoding="utf-8") as output:
                    output.write(f"verdict={decision.state}\n")
            return 0
        if decision.state == "blocked":
            return 1
        now = monotonic()
        if now >= deadline:
            timeout_reason = (
                f"no green CI verdict for {arguments.sha} within "
                f"{arguments.timeout_seconds}s"
                + (f" (last error: {last_error})" if last_error else "")
            )
            print(
                json.dumps({"state": "blocked", "reason": timeout_reason}),
                flush=True,
            )
            return 1
        sleep(min(arguments.interval_seconds, max(deadline - now, 0.0)))


def main(argv: list[str] | None = None) -> int:
    return require(argv)


if __name__ == "__main__":
    raise SystemExit(main())
