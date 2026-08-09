from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ci.selection import SCHEMA_VERSION, SHA_RE

WORKFLOW_PATH = ".github/workflows/scheduled-full-regression.yml"
MARKER_NAME = "full-regression"
GITHUB_API_VERSION = "2026-03-10"
MAX_API_RESPONSE_BYTES = 16 * 1024 * 1024
KNOWN_CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)
RETRY_CONCLUSIONS = KNOWN_CONCLUSIONS - {"success"}
FIXED_REASONS = frozenset(
    {
        "already_successfully_covered",
        "first_scheduled_run",
        "history_unavailable",
        "no_coverage_anchor",
        "sha_changed",
    }
)
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class HistoryUnavailable(ValueError):
    """GitHub Actions history cannot safely answer the coverage question."""


def decide_schedule(
    *,
    current_sha: str,
    current_run_id: int,
    runs: Sequence[object],
    jobs_for_run: Callable[[int], Sequence[object]],
) -> dict[str, Any]:
    if not SHA_RE.fullmatch(current_sha):
        raise HistoryUnavailable("current scheduled SHA is invalid")
    relevant = _relevant_runs(runs, current_run_id=current_run_id)
    if not relevant:
        return _decision(current_sha=current_sha, reason="first_scheduled_run", run_full=True)

    previous = relevant[0]
    if previous["conclusion"] != "success":
        return _decision(
            current_sha=current_sha,
            reason=f"retry_after_{previous['conclusion']}",
            run_full=True,
            previous=previous,
            history_depth=1,
        )

    anchor: dict[str, Any] | None = None
    later_failure: dict[str, Any] | None = None
    history_depth = 0
    for run in relevant:
        history_depth += 1
        if run["conclusion"] != "success":
            if later_failure is None:
                later_failure = run
            continue
        marker = _full_regression_marker(jobs_for_run(run["id"]), run_id=run["id"])
        if marker == "success":
            anchor = run
            break

    if anchor is None:
        return _decision(
            current_sha=current_sha,
            reason="no_coverage_anchor",
            run_full=True,
            previous=previous,
            history_depth=history_depth,
        )
    if later_failure is not None:
        return _decision(
            current_sha=current_sha,
            reason=f"retry_after_{later_failure['conclusion']}",
            run_full=True,
            previous=previous,
            anchor=anchor,
            history_depth=history_depth,
        )
    if anchor["head_sha"] != current_sha:
        return _decision(
            current_sha=current_sha,
            reason="sha_changed",
            run_full=True,
            previous=previous,
            anchor=anchor,
            history_depth=history_depth,
        )
    return _decision(
        current_sha=current_sha,
        reason="already_successfully_covered",
        run_full=False,
        previous=previous,
        anchor=anchor,
        history_depth=history_depth,
    )


def unavailable_decision(current_sha: str) -> dict[str, Any]:
    safe_sha = current_sha if SHA_RE.fullmatch(current_sha) else None
    return _decision(
        current_sha=safe_sha,
        reason="history_unavailable",
        run_full=True,
    )


def validate_schedule_decision(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("scheduled decision must be a JSON object")
    expected = {
        "coverage_anchor_run_id",
        "coverage_anchor_sha",
        "current_sha",
        "decision",
        "event",
        "history_depth_inspected",
        "previous_run_conclusion",
        "previous_run_id",
        "reason",
        "schema_version",
    }
    if set(payload) != expected:
        raise ValueError("scheduled decision has unexpected or missing fields")
    if payload["schema_version"] != SCHEMA_VERSION or payload["event"] != "schedule":
        raise ValueError("unsupported scheduled-decision schema")
    if payload["decision"] not in {"run_full", "skip"}:
        raise ValueError("unsupported scheduled decision")
    reason = payload["reason"]
    if not isinstance(reason, str) or (
        reason not in FIXED_REASONS
        and not (
            reason.startswith("retry_after_")
            and reason.removeprefix("retry_after_") in RETRY_CONCLUSIONS
        )
    ):
        raise ValueError("unsupported scheduled-decision reason")
    current_sha = payload["current_sha"]
    if current_sha is not None and (
        not isinstance(current_sha, str) or not SHA_RE.fullmatch(current_sha)
    ):
        raise ValueError("current_sha must be a lowercase full SHA or null")
    for field in ("previous_run_id", "coverage_anchor_run_id"):
        value = payload[field]
        invalid_id = not isinstance(value, int) or isinstance(value, bool) or value <= 0
        if value is not None and invalid_id:
            raise ValueError(f"{field} must be a positive integer or null")
    previous_conclusion = payload["previous_run_conclusion"]
    if previous_conclusion is not None and previous_conclusion not in KNOWN_CONCLUSIONS:
        raise ValueError("previous conclusion is not recognized")
    anchor_sha = payload["coverage_anchor_sha"]
    if anchor_sha is not None and (
        not isinstance(anchor_sha, str) or not SHA_RE.fullmatch(anchor_sha)
    ):
        raise ValueError("coverage anchor SHA must be a lowercase full SHA or null")
    depth = payload["history_depth_inspected"]
    if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 100:
        raise ValueError("history depth must be between zero and 100")
    if payload["decision"] == "skip":
        if reason != "already_successfully_covered" or current_sha != anchor_sha:
            raise ValueError("skip requires exact successful SHA coverage")
        if previous_conclusion != "success" or payload["coverage_anchor_run_id"] is None:
            raise ValueError("skip requires a successful previous run and full marker anchor")
    elif reason == "already_successfully_covered":
        raise ValueError("coverage reason can only be used for a skip")
    return payload


def dump_schedule_decision(payload: object, path: str | Path) -> None:
    validated = validate_schedule_decision(payload)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(validated, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_schedule_decision(path: str | Path) -> dict[str, Any]:
    return validate_schedule_decision(json.loads(Path(path).read_text(encoding="utf-8")))


def schedule_summary(payload: object) -> str:
    decision = validate_schedule_decision(payload)
    return "\n".join(
        (
            "## Scheduled full regression",
            "",
            f"- Current SHA: `{decision['current_sha'] or 'unavailable'}`",
            f"- Decision: `{decision['decision']}`",
            f"- Reason: `{decision['reason']}`",
            f"- Previous run: `{decision['previous_run_id'] or 'none'}`",
            f"- Previous conclusion: `{decision['previous_run_conclusion'] or 'none'}`",
            f"- Coverage anchor: `{decision['coverage_anchor_run_id'] or 'none'}`",
            f"- Coverage SHA: `{decision['coverage_anchor_sha'] or 'none'}`",
            f"- History depth inspected: `{decision['history_depth_inspected']}`",
            "",
        )
    )


class GitHubActionsClient:
    def __init__(
        self,
        *,
        api_url: str,
        repository: str,
        token: str,
        fetch_json: Callable[[str, Mapping[str, str]], object] | None = None,
    ) -> None:
        if not re_repository(repository):
            raise HistoryUnavailable("repository identifier is invalid")
        if not token:
            raise HistoryUnavailable("Actions token is unavailable")
        self.api_url = api_url.rstrip("/")
        self.repository = repository
        self.token = token
        self.fetch_json = fetch_json or _fetch_json

    def list_runs(self, workflow: str) -> list[object]:
        quoted_workflow = urllib.parse.quote(workflow, safe="")
        endpoint = f"/repos/{self.repository}/actions/workflows/{quoted_workflow}/runs"
        payload = self._get(
            endpoint,
            {
                "branch": "main",
                "event": "schedule",
                "status": "completed",
                "per_page": "100",
                "page": "1",
            },
        )
        return _bounded_page(payload, key="workflow_runs", limit=100)

    def list_latest_jobs(self, run_id: int) -> list[object]:
        endpoint = f"/repos/{self.repository}/actions/runs/{run_id}/jobs"
        jobs: list[object] = []
        page = 1
        while True:
            payload = self._get(
                endpoint,
                {"filter": "latest", "per_page": "100", "page": str(page)},
            )
            current = _bounded_page(payload, key="jobs", limit=1000, allow_partial=True)
            total_count = payload.get("total_count") if isinstance(payload, dict) else None
            if not isinstance(total_count, int) or isinstance(total_count, bool) or total_count < 0:
                raise HistoryUnavailable("job history count is malformed")
            jobs.extend(current)
            if len(jobs) >= total_count:
                if len(jobs) != total_count:
                    raise HistoryUnavailable("job history count is inconsistent")
                return jobs
            if not current or len(jobs) > 1000:
                raise HistoryUnavailable("job history pagination is incomplete")
            page += 1

    def _get(self, endpoint: str, query: Mapping[str, str]) -> object:
        url = f"{self.api_url}{endpoint}?{urllib.parse.urlencode(query)}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "DataTalksClub-website-scheduled-regression",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        try:
            return self.fetch_json(url, headers)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise HistoryUnavailable("GitHub Actions history request failed") from exc


def _bounded_page(
    payload: object,
    *,
    key: str,
    limit: int,
    allow_partial: bool = False,
) -> list[object]:
    if not isinstance(payload, dict):
        raise HistoryUnavailable("history response is not an object")
    total = payload.get("total_count")
    items = payload.get(key)
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
        or not isinstance(items, list)
        or len(items) > min(100, limit)
    ):
        raise HistoryUnavailable("history page is malformed")
    if not allow_partial and len(items) != min(total, limit):
        raise HistoryUnavailable("bounded run history is incomplete")
    return items


def _fetch_json(url: str, headers: Mapping[str, str]) -> object:
    request = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        if response.status != 200:
            raise HistoryUnavailable("GitHub Actions history returned an error")
        body = response.read(MAX_API_RESPONSE_BYTES + 1)
        if len(body) > MAX_API_RESPONSE_BYTES:
            raise HistoryUnavailable("GitHub Actions history response is too large")
    return json.loads(body)


def _relevant_runs(runs: Sequence[object], *, current_run_id: int) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for value in runs[:100]:
        if not isinstance(value, dict):
            raise HistoryUnavailable("workflow run is not an object")
        run_id = value.get("id")
        if run_id == current_run_id:
            continue
        if value.get("event") != "schedule":
            continue
        if value.get("head_branch") != "main":
            continue
        if not _matches_workflow_path(value.get("path")):
            continue
        conclusion = value.get("conclusion")
        head_sha = value.get("head_sha")
        if (
            not isinstance(run_id, int)
            or isinstance(run_id, bool)
            or run_id <= 0
            or run_id in seen_ids
            or value.get("status") != "completed"
            or conclusion not in KNOWN_CONCLUSIONS
            or not isinstance(head_sha, str)
            or not SHA_RE.fullmatch(head_sha)
        ):
            raise HistoryUnavailable("workflow run fields are malformed or ambiguous")
        seen_ids.add(run_id)
        relevant.append({"id": run_id, "conclusion": conclusion, "head_sha": head_sha})
    return relevant


def _matches_workflow_path(value: object) -> bool:
    if not isinstance(value, str):
        return False
    path, _separator, _revision = value.partition("@")
    return path == WORKFLOW_PATH


def _full_regression_marker(jobs: Sequence[object], *, run_id: int) -> str | None:
    markers: list[str] = []
    for value in jobs:
        if not isinstance(value, dict):
            raise HistoryUnavailable("workflow job is not an object")
        if value.get("name") != MARKER_NAME:
            continue
        job_run_id = value.get("run_id")
        status = value.get("status")
        conclusion = value.get("conclusion")
        if (
            not isinstance(job_run_id, int)
            or isinstance(job_run_id, bool)
            or job_run_id <= 0
            or job_run_id != run_id
        ):
            raise HistoryUnavailable("marker workflow run is malformed or mismatched")
        if status != "completed":
            raise HistoryUnavailable("marker status is not completed")
        if conclusion not in KNOWN_CONCLUSIONS:
            raise HistoryUnavailable("marker conclusion is malformed")
        markers.append(conclusion)
    if len(markers) > 1:
        raise HistoryUnavailable("full-regression marker is duplicated")
    if not markers:
        return None
    if markers[0] not in {"success", "skipped"}:
        raise HistoryUnavailable("successful workflow has an inconsistent full marker")
    return markers[0]


def _decision(
    *,
    current_sha: str | None,
    reason: str,
    run_full: bool,
    previous: Mapping[str, Any] | None = None,
    anchor: Mapping[str, Any] | None = None,
    history_depth: int = 0,
) -> dict[str, Any]:
    result = {
        "coverage_anchor_run_id": anchor["id"] if anchor else None,
        "coverage_anchor_sha": anchor["head_sha"] if anchor else None,
        "current_sha": current_sha,
        "decision": "run_full" if run_full else "skip",
        "event": "schedule",
        "history_depth_inspected": history_depth,
        "previous_run_conclusion": previous["conclusion"] if previous else None,
        "previous_run_id": previous["id"] if previous else None,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
    }
    validate_schedule_decision(result)
    return result


def re_repository(value: str) -> bool:
    return REPOSITORY_RE.fullmatch(value) is not None


def _verify_checkout(repository: Path, current_sha: str) -> None:
    if not SHA_RE.fullmatch(current_sha):
        raise HistoryUnavailable("current scheduled SHA is invalid")
    result = subprocess.run(
        ["git", "-C", os.fspath(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() != current_sha:
        raise HistoryUnavailable("checkout does not match scheduled SHA")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com")
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", default="scheduled-full-regression.yml")
    parser.add_argument("--current-run-id", type=int, required=True)
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--current-ref", required=True)
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary")
    parser.add_argument("--github-output")
    args = parser.parse_args()

    try:
        if args.current_run_id <= 0:
            raise HistoryUnavailable("current workflow run id is invalid")
        if args.current_ref != "refs/heads/main":
            raise HistoryUnavailable("scheduled workflow did not run on main")
        _verify_checkout(args.checkout, args.current_sha)
    except (HistoryUnavailable, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit("scheduled checkout validation failed") from exc

    try:
        client = GitHubActionsClient(
            api_url=args.api_url,
            repository=args.repository,
            token=os.environ.get("GITHUB_TOKEN", ""),
        )
        decision = decide_schedule(
            current_sha=args.current_sha,
            current_run_id=args.current_run_id,
            runs=client.list_runs(args.workflow),
            jobs_for_run=client.list_latest_jobs,
        )
    except HistoryUnavailable:
        decision = unavailable_decision(args.current_sha)

    dump_schedule_decision(decision, args.output)
    if args.summary:
        summary_path = Path(args.summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(schedule_summary(decision), encoding="utf-8")
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as output:
            output.write(f"decision={decision['decision']}\n")
            output.write(f"reason={decision['reason']}\n")


if __name__ == "__main__":
    main()
