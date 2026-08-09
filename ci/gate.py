from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ci.schedule import load_schedule_decision
from ci.selection import SCHEMA_VERSION, load_selection

JOB_RESULTS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "success",
        "timed_out",
    }
)
NORMAL_REQUIRED_JOBS = (
    "resolve-release",
    "classification",
    "quality",
    "django",
    "playwright",
    "container",
)
SCHEDULE_COMPONENTS = ("quality", "django", "playwright", "container")


def normal_gate(selection_path: str | Path, outcomes: Mapping[str, str]) -> dict[str, Any]:
    selection: dict[str, Any] | None
    selection_status = "valid"
    try:
        selection = load_selection(selection_path)
    except (OSError, ValueError, json.JSONDecodeError):
        selection = None
        selection_status = "invalid"
    normalized = _normalize_outcomes(outcomes, NORMAL_REQUIRED_JOBS)
    passed = selection is not None and all(value == "success" for value in normalized.values())
    return {
        "gate": "normal_ci",
        "required_job_outcomes": normalized,
        "schema_version": SCHEMA_VERSION,
        "selection": selection,
        "selection_status": selection_status,
        "verdict": "success" if passed else "failure",
    }


def marker_gate(outcomes: Mapping[str, str]) -> bool:
    normalized = _normalize_outcomes(outcomes, SCHEDULE_COMPONENTS)
    return all(value == "success" for value in normalized.values())


def scheduled_gate(decision_path: str | Path, outcomes: Mapping[str, str]) -> dict[str, Any]:
    decision: dict[str, Any] | None
    decision_status = "valid"
    try:
        decision = load_schedule_decision(decision_path)
    except (OSError, ValueError, json.JSONDecodeError):
        decision = None
        decision_status = "invalid"
    required = ("selector", *SCHEDULE_COMPONENTS, "full-regression")
    normalized = _normalize_outcomes(outcomes, required)
    passed = False
    if decision is not None and normalized["selector"] == "success":
        if decision["decision"] == "run_full":
            passed = all(normalized[name] == "success" for name in required)
        else:
            passed = all(
                normalized[name] == "skipped" for name in (*SCHEDULE_COMPONENTS, "full-regression")
            )
    return {
        "decision": decision,
        "decision_status": decision_status,
        "gate": "scheduled_regression",
        "required_job_outcomes": normalized,
        "schema_version": SCHEMA_VERSION,
        "verdict": "success" if passed else "failure",
    }


def gate_summary(payload: Mapping[str, Any]) -> str:
    outcomes = payload["required_job_outcomes"]
    lines = [
        f"## {payload['gate'].replace('_', ' ').title()} gate",
        "",
        f"- Verdict: `{payload['verdict']}`",
    ]
    for name in sorted(outcomes):
        lines.append(f"- `{name}`: `{outcomes[name]}`")
    selection = payload.get("selection") or payload.get("decision")
    if isinstance(selection, dict):
        lines.append(f"- Selection reason: `{selection['reason']}`")
    lines.append("")
    return "\n".join(lines)


def _normalize_outcomes(outcomes: Mapping[str, str], required: tuple[str, ...]) -> dict[str, str]:
    if set(outcomes) != set(required):
        raise ValueError("gate outcomes have unexpected or missing jobs")
    normalized: dict[str, str] = {}
    for name in required:
        result = outcomes[name]
        normalized[name] = result if result in JOB_RESULTS else "unknown"
    return dict(sorted(normalized.items()))


def _write(payload: Mapping[str, Any], output: str, summary: str | None) -> None:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if summary:
        summary_path = Path(summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(gate_summary(payload), encoding="utf-8")


def _outcome_arguments(parser: argparse.ArgumentParser, names: tuple[str, ...]) -> None:
    for name in names:
        parser.add_argument(f"--{name}", required=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    normal = commands.add_parser("normal")
    normal.add_argument("--selection", required=True)
    normal.add_argument("--output", required=True)
    normal.add_argument("--summary")
    _outcome_arguments(normal, NORMAL_REQUIRED_JOBS)
    marker = commands.add_parser("marker")
    _outcome_arguments(marker, SCHEDULE_COMPONENTS)
    scheduled = commands.add_parser("scheduled")
    scheduled.add_argument("--decision", required=True)
    scheduled.add_argument("--output", required=True)
    scheduled.add_argument("--summary")
    _outcome_arguments(scheduled, ("selector", *SCHEDULE_COMPONENTS, "full-regression"))
    args = parser.parse_args()

    values = vars(args)
    if args.command == "marker":
        outcomes = {name: values[name.replace("-", "_")] for name in SCHEDULE_COMPONENTS}
        if not marker_gate(outcomes):
            raise SystemExit(1)
        return
    if args.command == "normal":
        outcomes = {name: values[name.replace("-", "_")] for name in NORMAL_REQUIRED_JOBS}
        payload = normal_gate(args.selection, outcomes)
    else:
        names = ("selector", *SCHEDULE_COMPONENTS, "full-regression")
        outcomes = {name: values[name.replace("-", "_")] for name in names}
        payload = scheduled_gate(args.decision, outcomes)
    _write(payload, args.output, args.summary)
    if payload["verdict"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
