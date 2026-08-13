from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ci.provenance import EvidenceError, load_resolution, selection_digest
from ci.schedule import load_schedule_decision
from ci.selection import SCHEMA_VERSION, load_selection
from ci.verification import load_plan, validate_report

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
    "screenshots",
)
SCHEDULE_COMPONENTS = (
    "quality",
    "factories",
    "migrations",
    "django",
    "playwright",
    "container",
)


def normal_gate(
    selection_path: str | Path,
    outcomes: Mapping[str, str],
    *,
    evidence_path: str | Path | None = None,
    expected_profile: str | None = None,
    expected_reason: str | None = None,
    expected_controller_sha: str | None = None,
    expected_release_sha: str | None = None,
    expected_run_id: str | None = None,
    expected_attempt: int | None = None,
    expected_event: str | None = None,
    expected_source_after_sha: str | None = None,
    expected_source_before_sha: str | None = None,
    expected_selection_sha256: str | None = None,
    verification_plan_path: str | Path | None = None,
    verification_report_path: str | Path | None = None,
    verification_evidence_directory: str | Path | None = None,
) -> dict[str, Any]:
    selection: dict[str, Any] | None
    selection_status = "valid"
    selection_rejection_reason: str | None = None
    try:
        selection = load_selection(selection_path)
    except (OSError, ValueError, json.JSONDecodeError):
        selection = None
        selection_status = "invalid"
        selection_rejection_reason = "selection_invalid"

    resolution: dict[str, Any] | None = None
    if evidence_path is None:
        selection_status = "invalid"
        selection_rejection_reason = "evidence_missing"
    else:
        try:
            resolution = load_resolution(evidence_path)
            if selection is None:
                raise EvidenceError("selection_invalid")
            digest = selection_digest(Path(selection_path).read_bytes())
            if resolution["selection_sha256"] != digest:
                raise EvidenceError("selection_digest_mismatch")
            if selection["profile"] != resolution["profile"]:
                raise EvidenceError("classifier_profile_mismatch")
            if selection["reason"] != resolution["reason"]:
                raise EvidenceError("classifier_reason_mismatch")
            if expected_profile is not None and resolution["profile"] != expected_profile:
                raise EvidenceError("classifier_profile_mismatch")
            if expected_reason is not None and resolution["reason"] != expected_reason:
                raise EvidenceError("classifier_reason_mismatch")
            if (
                expected_selection_sha256 is not None
                and resolution["selection_sha256"] != expected_selection_sha256
            ):
                raise EvidenceError("classifier_digest_mismatch")
            if expected_controller_sha is not None and (
                resolution["controller_sha"] != expected_controller_sha
            ):
                raise EvidenceError("controller_sha_mismatch")
            if expected_release_sha is not None and (
                resolution["release_sha"] != expected_release_sha
            ):
                raise EvidenceError("release_sha_mismatch")
            if expected_run_id is not None and resolution["run_id"] != expected_run_id:
                raise EvidenceError("run_id_mismatch")
            if expected_attempt is not None:
                if resolution["mode"] == "current_attempt" and (
                    resolution["resolved_attempt"] != expected_attempt
                ):
                    raise EvidenceError("resolved_attempt_mismatch")
                if resolution["mode"] == "reused_attempt_1" and (
                    expected_attempt <= 1 or resolution["resolved_attempt"] != 1
                ):
                    raise EvidenceError("fallback_attempt_invalid")
            if expected_event is not None and expected_event != selection["event"]:
                raise EvidenceError("selection_event_mismatch")
            if expected_source_after_sha is not None or expected_source_before_sha is not None:
                source_after = expected_source_after_sha or None
                source_before = expected_source_before_sha or None
                if selection["event"] == "push":
                    if resolution["source_after_sha"] != source_after:
                        raise EvidenceError("source_after_sha_mismatch")
                    if resolution["source_before_sha"] != source_before:
                        raise EvidenceError("source_before_sha_mismatch")
                elif source_after is not None or source_before is not None:
                    raise EvidenceError("manual_source_identity_unexpected")
        except (OSError, ValueError, json.JSONDecodeError, EvidenceError) as exc:
            resolution = None
            selection_status = "invalid"
            selection_rejection_reason = _safe_reason(exc)
    normalized = _normalize_outcomes(outcomes, NORMAL_REQUIRED_JOBS)
    verification_report: dict[str, Any] | None = None
    verification_status = "invalid"
    try:
        if (
            verification_plan_path is None
            or verification_report_path is None
            or verification_evidence_directory is None
        ):
            raise ValueError("verification plan, report, and evidence must be supplied together")
        plan = load_plan(verification_plan_path)
        report_payload = json.loads(Path(verification_report_path).read_text(encoding="utf-8"))
        verification_report = validate_report(
            report_payload,
            plan=plan,
            evidence_directory=verification_evidence_directory,
            allow_pending=False,
        )
        if verification_report["verdict"] == "success" and verification_report["phase"] == "ci":
            verification_status = "valid"
    except (OSError, ValueError, json.JSONDecodeError):
        verification_report = None
    passed = (
        selection is not None
        and resolution is not None
        and verification_status == "valid"
        and all(value == "success" for value in normalized.values())
    )
    evidence = None
    if resolution is not None:
        evidence = {
            "created_attempt": resolution["created_attempt"],
            "mode": resolution["mode"],
            "profile": resolution["profile"],
            "reason": resolution["reason"],
            "resolved_attempt": resolution["resolved_attempt"],
            "run_id": resolution["run_id"],
            "selection_sha256": resolution["selection_sha256"],
            "source_after_sha": resolution["source_after_sha"],
            "source_before_sha": resolution["source_before_sha"],
        }
    return {
        "gate": "normal_ci",
        "required_job_outcomes": normalized,
        "schema_version": SCHEMA_VERSION,
        "selection": selection,
        "selection_evidence": evidence,
        "selection_rejection_reason": selection_rejection_reason,
        "selection_status": selection_status,
        "verification_report": verification_report,
        "verification_status": verification_status,
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
    evidence = payload.get("selection_evidence")
    if isinstance(evidence, dict):
        lines.append(
            f"- Selection evidence: `{evidence['mode']}` (attempt `{evidence['resolved_attempt']}`)"
        )
    if payload.get("selection_rejection_reason"):
        lines.append(f"- Selection evidence rejection: `{payload['selection_rejection_reason']}`")
    if "verification_status" in payload:
        lines.append(f"- Verification evidence: `{payload['verification_status']}`")
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


def _safe_reason(error: BaseException) -> str:
    reason = getattr(error, "reason", None)
    if (
        isinstance(reason, str)
        and reason
        and all(character.isalnum() or character == "_" for character in reason)
    ):
        return reason[:80]
    return "evidence_invalid"


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
        with summary_path.open("a", encoding="utf-8") as summary_file:
            summary_file.write(gate_summary(payload))


def _outcome_arguments(parser: argparse.ArgumentParser, names: tuple[str, ...]) -> None:
    for name in names:
        parser.add_argument(f"--{name}", required=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    normal = commands.add_parser("normal")
    normal.add_argument("--selection", required=True)
    normal.add_argument("--evidence", required=True)
    normal.add_argument("--expected-profile")
    normal.add_argument("--expected-reason")
    normal.add_argument("--expected-controller-sha")
    normal.add_argument("--expected-release-sha")
    normal.add_argument("--expected-run-id")
    normal.add_argument("--expected-attempt", type=int)
    normal.add_argument("--expected-event")
    normal.add_argument("--expected-source-after-sha")
    normal.add_argument("--expected-source-before-sha")
    normal.add_argument("--expected-selection-sha256")
    normal.add_argument("--output", required=True)
    normal.add_argument("--summary")
    normal.add_argument("--verification-plan")
    normal.add_argument("--verification-report")
    normal.add_argument("--verification-evidence-directory")
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
        payload = normal_gate(
            args.selection,
            outcomes,
            evidence_path=args.evidence,
            expected_profile=args.expected_profile,
            expected_reason=args.expected_reason,
            expected_controller_sha=args.expected_controller_sha,
            expected_release_sha=args.expected_release_sha,
            expected_run_id=args.expected_run_id,
            expected_attempt=args.expected_attempt,
            expected_event=args.expected_event,
            expected_source_after_sha=args.expected_source_after_sha,
            expected_source_before_sha=args.expected_source_before_sha,
            expected_selection_sha256=args.expected_selection_sha256,
            verification_plan_path=args.verification_plan,
            verification_report_path=args.verification_report,
            verification_evidence_directory=args.verification_evidence_directory,
        )
    else:
        names = ("selector", *SCHEDULE_COMPONENTS, "full-regression")
        outcomes = {name: values[name.replace("-", "_")] for name in names}
        payload = scheduled_gate(args.decision, outcomes)
    _write(payload, args.output, args.summary)
    if payload["verdict"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
