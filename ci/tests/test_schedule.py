from __future__ import annotations

import urllib.parse
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from ci.schedule import (
    GITHUB_API_VERSION,
    WORKFLOW_PATH,
    GitHubActionsClient,
    HistoryUnavailable,
    decide_schedule,
    dump_schedule_decision,
    schedule_summary,
    unavailable_decision,
    validate_schedule_decision,
)

CURRENT = "a" * 40
OLDER = "b" * 40


def run(
    run_id: int,
    *,
    conclusion: object = "success",
    sha: object = CURRENT,
    **overrides: object,
) -> dict[str, object]:
    result = {
        "id": run_id,
        "event": "schedule",
        "head_branch": "main",
        "path": WORKFLOW_PATH,
        "status": "completed",
        "conclusion": conclusion,
        "head_sha": sha,
    }
    result.update(overrides)
    return result


def jobs(markers: dict[int, str | None]):
    def lookup(run_id: int) -> Sequence[object]:
        conclusion = markers[run_id]
        if conclusion is None:
            return [{"name": "selector", "conclusion": "success", "run_id": run_id}]
        return [
            {
                "name": "full-regression",
                "conclusion": conclusion,
                "run_id": run_id,
                "status": "completed",
            }
        ]

    return lookup


def test_first_scheduled_run_runs_full() -> None:
    result = decide_schedule(current_sha=CURRENT, current_run_id=99, runs=[], jobs_for_run=jobs({}))
    assert result["decision"] == "run_full"
    assert result["reason"] == "first_scheduled_run"


def test_matching_successful_full_anchor_skips() -> None:
    result = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=[run(8)],
        jobs_for_run=jobs({8: "success"}),
    )
    assert result["decision"] == "skip"
    assert result["reason"] == "already_successfully_covered"
    assert result["coverage_anchor_run_id"] == 8


def test_changed_sha_runs_full() -> None:
    result = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=[run(8, sha=OLDER)],
        jobs_for_run=jobs({8: "success"}),
    )
    assert result["decision"] == "run_full"
    assert result["reason"] == "sha_changed"


def test_successful_skip_does_not_replace_older_full_anchor() -> None:
    result = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=[run(9), run(8)],
        jobs_for_run=jobs({9: "skipped", 8: "success"}),
    )
    assert result["decision"] == "skip"
    assert result["coverage_anchor_run_id"] == 8
    assert result["history_depth_inspected"] == 2


def test_any_failure_later_than_anchor_blocks_skip_even_after_a_successful_selector() -> None:
    result = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=[run(10), run(9, conclusion="failure"), run(8)],
        jobs_for_run=jobs({10: "skipped", 8: "success"}),
    )
    assert result["decision"] == "run_full"
    assert result["reason"] == "retry_after_failure"
    assert result["previous_run_id"] == 10
    assert result["coverage_anchor_run_id"] == 8


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "cancelled", "timed_out", "stale", "action_required", "neutral", "skipped"],
)
def test_immediately_previous_unsuccessful_run_retries(conclusion: str) -> None:
    result = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=[run(9, conclusion=conclusion), run(8)],
        jobs_for_run=jobs({8: "success"}),
    )
    assert result["decision"] == "run_full"
    assert result["reason"] == f"retry_after_{conclusion}"
    assert result["coverage_anchor_run_id"] is None


@pytest.mark.parametrize("marker", [None, "skipped"])
def test_successful_selector_only_run_is_not_an_anchor(marker: str | None) -> None:
    result = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=[run(8)],
        jobs_for_run=jobs({8: marker}),
    )
    assert result["reason"] == "no_coverage_anchor"
    assert result["decision"] == "run_full"


def test_duplicate_marker_is_ambiguous() -> None:
    def duplicate(_run_id: int):
        return [
            {
                "name": "full-regression",
                "conclusion": "success",
                "run_id": 8,
                "status": "completed",
            },
            {
                "name": "full-regression",
                "conclusion": "success",
                "run_id": 8,
                "status": "completed",
            },
        ]

    with pytest.raises(HistoryUnavailable):
        decide_schedule(
            current_sha=CURRENT,
            current_run_id=99,
            runs=[run(8)],
            jobs_for_run=duplicate,
        )


@pytest.mark.parametrize(
    "marker",
    [
        {"name": "full-regression", "conclusion": "success", "status": "completed"},
        {
            "name": "full-regression",
            "conclusion": "success",
            "run_id": 8,
            "status": "in_progress",
        },
    ],
    ids=["missing-run-id", "in-progress-with-success"],
)
def test_reported_malformed_success_marker_fails_safe_to_full(
    marker: dict[str, object],
) -> None:
    with pytest.raises(HistoryUnavailable):
        decide_schedule(
            current_sha=CURRENT,
            current_run_id=99,
            runs=[run(8)],
            jobs_for_run=lambda _run_id: [marker],
        )

    fallback = unavailable_decision(CURRENT)
    assert fallback["decision"] == "run_full"
    assert fallback["reason"] == "history_unavailable"


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": 9},
        {"run_id": True},
        {"run_id": "8"},
        {"run_id": 0},
        {"status": None},
        {"status": "queued"},
        {"conclusion": None},
        {"conclusion": "unknown"},
        {"conclusion": "failure"},
    ],
    ids=[
        "wrong-run-id",
        "boolean-run-id",
        "string-run-id",
        "non-positive-run-id",
        "missing-status",
        "non-completed-status",
        "missing-conclusion",
        "unknown-conclusion",
        "failed-marker-on-successful-workflow",
    ],
)
def test_nearby_malformed_marker_fields_are_ambiguous(
    overrides: dict[str, object],
) -> None:
    marker: dict[str, object] = {
        "name": "full-regression",
        "conclusion": "success",
        "run_id": 8,
        "status": "completed",
    }
    marker.update(overrides)

    with pytest.raises(HistoryUnavailable):
        decide_schedule(
            current_sha=CURRENT,
            current_run_id=99,
            runs=[run(8)],
            jobs_for_run=lambda _run_id: [marker],
        )


def test_boolean_marker_run_id_cannot_equal_integer_history_id() -> None:
    marker = {
        "name": "full-regression",
        "conclusion": "success",
        "run_id": True,
        "status": "completed",
    }

    with pytest.raises(HistoryUnavailable):
        decide_schedule(
            current_sha=CURRENT,
            current_run_id=99,
            runs=[run(1)],
            jobs_for_run=lambda _run_id: [marker],
        )


def test_current_and_unrelated_runs_are_excluded() -> None:
    irrelevant = [
        run(99),
        run(10, event="push"),
        run(11, head_branch="other"),
        run(12, path=".github/workflows/other.yml"),
    ]
    result = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=[*irrelevant, run(8)],
        jobs_for_run=jobs({8: "success"}),
    )
    assert result["decision"] == "skip"
    assert result["previous_run_id"] == 8


def test_documented_workflow_path_revision_suffix_is_accepted() -> None:
    result = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=[run(8, path=f"{WORKFLOW_PATH}@main")],
        jobs_for_run=jobs({8: "success"}),
    )
    assert result["decision"] == "skip"
    assert result["coverage_anchor_run_id"] == 8


def test_no_anchor_at_bounded_100_run_boundary_runs_full() -> None:
    runs = [run(run_id) for run_id in range(200, 100, -1)]
    result = decide_schedule(
        current_sha=CURRENT,
        current_run_id=999,
        runs=runs,
        jobs_for_run=jobs({run_id: "skipped" for run_id in range(200, 100, -1)}),
    )
    assert result["reason"] == "no_coverage_anchor"
    assert result["history_depth_inspected"] == 100


@pytest.mark.parametrize(
    "bad_run",
    [
        run(8, conclusion=None),
        run(8, status="in_progress"),
        run(8, head_sha="bad"),
        run(8, id="8"),
    ],
)
def test_malformed_relevant_history_is_unavailable(bad_run: object) -> None:
    with pytest.raises(HistoryUnavailable):
        decide_schedule(
            current_sha=CURRENT,
            current_run_id=99,
            runs=[bad_run],
            jobs_for_run=jobs({}),
        )


def test_history_unavailable_fails_safe_to_full() -> None:
    decision = unavailable_decision(CURRENT)
    assert decision["decision"] == "run_full"
    assert decision["reason"] == "history_unavailable"
    validate_schedule_decision(decision)


def test_scheduled_json_and_summary_are_deterministic(tmp_path: Path) -> None:
    decision = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=[run(8, sha=OLDER)],
        jobs_for_run=jobs({8: "success"}),
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    dump_schedule_decision(decision, first)
    dump_schedule_decision(decision, second)

    assert first.read_bytes() == second.read_bytes()
    assert "`sha_changed`" in schedule_summary(decision)
    assert "Authorization" not in first.read_text(encoding="utf-8")


def test_latest_attempt_jobs_are_paginated_without_exposing_token() -> None:
    requested: list[tuple[str, Mapping[str, str]]] = []

    def fetch(url: str, headers: Mapping[str, str]):
        requested.append((url, headers))
        page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["page"][0])
        if page == 1:
            return {"total_count": 101, "jobs": [{"name": f"job-{i}"} for i in range(100)]}
        return {
            "total_count": 101,
            "jobs": [{"name": "full-regression", "conclusion": "success", "run_id": 8}],
        }

    client = GitHubActionsClient(
        api_url="https://api.github.invalid",
        repository="owner/repo",
        token="not-logged-token",
        fetch_json=fetch,
    )
    result = client.list_latest_jobs(8)

    assert len(result) == 101
    assert len(requested) == 2
    assert all("filter=latest" in url for url, _headers in requested)
    assert all("not-logged-token" not in url for url, _headers in requested)
    assert all(headers["Authorization"] == "Bearer not-logged-token" for _url, headers in requested)
    assert all(headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION for _url, headers in requested)


def test_run_history_request_is_scoped_and_bounded() -> None:
    requested: list[str] = []

    def fetch(url: str, _headers: Mapping[str, str]):
        requested.append(url)
        return {"total_count": 0, "workflow_runs": []}

    client = GitHubActionsClient(
        api_url="https://api.github.invalid",
        repository="owner/repo",
        token="token",
        fetch_json=fetch,
    )
    assert client.list_runs("scheduled-full-regression.yml") == []
    assert len(requested) == 1
    parsed = urllib.parse.urlsplit(requested[0])
    assert parsed.path.endswith("/actions/workflows/scheduled-full-regression.yml/runs")
    assert urllib.parse.parse_qs(parsed.query) == {
        "branch": ["main"],
        "event": ["schedule"],
        "page": ["1"],
        "per_page": ["100"],
        "status": ["completed"],
    }


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"total_count": "1", "workflow_runs": []},
        {"total_count": 1, "workflow_runs": []},
        {"total_count": 0, "workflow_runs": [run(8)]},
    ],
)
def test_run_api_malformed_or_incomplete_payload_fails_safe(payload: object) -> None:
    client = GitHubActionsClient(
        api_url="https://api.github.invalid",
        repository="owner/repo",
        token="token",
        fetch_json=lambda _url, _headers: payload,
    )
    with pytest.raises(HistoryUnavailable):
        client.list_runs("scheduled-full-regression.yml")
