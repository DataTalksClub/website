from __future__ import annotations

import pytest

from ci.schedule import HistoryUnavailable, decide_schedule, unavailable_decision

CURRENT = "a" * 40
ANCHOR = "b" * 40
CURRENT_STATE = "1" * 64
ANCHOR_STATE = "2" * 64


def run(run_id: int) -> dict[str, object]:
    return {
        "id": run_id,
        "run_attempt": 1,
        "event": "schedule",
        "head_branch": "main",
        "path": ".github/workflows/scheduled-full-regression.yml",
        "status": "completed",
        "conclusion": "success",
        "head_sha": ANCHOR,
    }


def marker(run_id: int):
    return (
        {
            "name": "full-regression",
            "conclusion": "success",
            "run_id": run_id,
            "status": "completed",
        },
    )


def test_identical_verification_state_skips_even_when_commit_sha_differs() -> None:
    decision = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=(run(8),),
        jobs_for_run=marker,
        current_state_digest=CURRENT_STATE,
        state_for_run=lambda _run: CURRENT_STATE,
    )
    assert decision["decision"] == "skip"
    assert decision["reason"] == "unchanged_state"
    assert decision["coverage_anchor_sha"] == ANCHOR


def test_changed_verification_state_runs_full() -> None:
    decision = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=(run(8),),
        jobs_for_run=marker,
        current_state_digest=CURRENT_STATE,
        state_for_run=lambda _run: ANCHOR_STATE,
    )
    assert decision["decision"] == "run_full"
    assert decision["reason"] == "state_changed"


@pytest.mark.parametrize(
    "lookup",
    [
        lambda _sha: "malformed",
        lambda _sha: (_ for _ in ()).throw(ValueError("missing tree")),
    ],
)
def test_unavailable_or_malformed_anchor_state_fails_closed(lookup) -> None:
    with pytest.raises(HistoryUnavailable):
        decide_schedule(
            current_sha=CURRENT,
            current_run_id=99,
            runs=(run(8),),
            jobs_for_run=marker,
            current_state_digest=CURRENT_STATE,
            state_for_run=lookup,
        )
    assert unavailable_decision(CURRENT, CURRENT_STATE)["decision"] == "run_full"


def test_anchor_uses_archived_historical_environment_not_current_environment() -> None:
    looked_up: list[dict[str, object]] = []

    def archived_state(anchor: dict[str, object]) -> str:
        looked_up.append(anchor)
        return CURRENT_STATE

    decision = decide_schedule(
        current_sha=CURRENT,
        current_run_id=99,
        runs=(run(8),),
        jobs_for_run=marker,
        current_state_digest=CURRENT_STATE,
        state_for_run=archived_state,
    )
    assert decision["decision"] == "skip"
    assert looked_up == [
        {
            "id": 8,
            "conclusion": "success",
            "head_sha": ANCHOR,
            "run_attempt": 1,
        }
    ]
