from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

FLAKE_POLICY_SCHEMA_VERSION = 1
FLAKE_POLICY_LINE = "DTC_FLAKE_POLICY_V1"
FLAKE_POLICY_COMMAND = "make test-playwright-quarantined"
FLAKE_POLICY_COUNT_FIELDS = (
    "attempted",
    "passed",
    "failed",
    "skipped",
    "rerun",
    "quarantined",
)


class FlakePolicyError(ValueError):
    """A Playwright flake-policy summary is missing or internally inconsistent."""


def parse_policy_output(body: bytes) -> dict[str, int | bool]:
    """Parse the single summary emitted by the Playwright flake-policy plugin."""
    try:
        text = body.decode("utf-8")
    except UnicodeError as exc:
        raise FlakePolicyError("flake-policy output must be UTF-8") from exc
    lines = [
        line.strip() for line in text.splitlines() if line.strip().startswith(FLAKE_POLICY_LINE)
    ]
    if len(lines) != 1:
        raise FlakePolicyError("flake-policy output must contain exactly one policy summary")
    fields = lines[0].split()
    if not fields or fields[0] != FLAKE_POLICY_LINE:
        raise FlakePolicyError("flake-policy summary has an invalid prefix")
    values: dict[str, int | bool] = {}
    for field in fields[1:]:
        key, separator, raw_value = field.partition("=")
        if not separator or not key or key in values:
            raise FlakePolicyError("flake-policy summary has duplicate or malformed fields")
        if key == "complete":
            if raw_value not in {"0", "1"}:
                raise FlakePolicyError("flake-policy completion flag is invalid")
            values[key] = raw_value == "1"
            continue
        if key not in FLAKE_POLICY_COUNT_FIELDS or not raw_value.isdigit():
            raise FlakePolicyError("flake-policy summary has an unknown or invalid count")
        values[key] = int(raw_value)
    if set(values) != {*FLAKE_POLICY_COUNT_FIELDS, "complete"}:
        raise FlakePolicyError("flake-policy summary omits a required field")
    attempted = values["attempted"]
    passed = values["passed"]
    failed = values["failed"]
    skipped = values["skipped"]
    assert isinstance(attempted, int)
    assert isinstance(passed, int)
    assert isinstance(failed, int)
    assert isinstance(skipped, int)
    if attempted != passed + failed + skipped:
        raise FlakePolicyError("flake-policy attempted count does not equal outcomes")
    return values


def policy_counts_for_evidence(parsed: dict[str, int | bool]) -> dict[str, int]:
    if set(parsed) != {*FLAKE_POLICY_COUNT_FIELDS, "complete"}:
        raise FlakePolicyError("flake-policy counts have an invalid shape")
    counts = {field: parsed[field] for field in FLAKE_POLICY_COUNT_FIELDS}
    if any(not isinstance(value, int) for value in counts.values()):
        raise FlakePolicyError("flake-policy counts are not integers")
    attempted = counts["attempted"]
    return {
        "assertions": attempted,
        "attempted": attempted,
        "failed": counts["failed"],
        "passed": counts["passed"],
        "quarantined": counts["quarantined"],
        "rerun": counts["rerun"],
        "skipped": counts["skipped"],
        "tests": attempted,
    }


def _artifact(path: Path) -> dict[str, Any]:
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise FlakePolicyError(f"flake-policy output is unreadable: {path}") from exc
    return {
        "path": str(path),
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body),
    }


def build_report(*, output_path: str | Path, command: str, exit_code: int) -> dict[str, Any]:
    if command != FLAKE_POLICY_COMMAND:
        raise FlakePolicyError("flake-policy report command is not allowlisted")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code < 0:
        raise FlakePolicyError("flake-policy exit code is invalid")
    path = Path(output_path)
    artifact = _artifact(path)
    reason = ""
    try:
        parsed = parse_policy_output(path.read_bytes())
        evidence_counts = policy_counts_for_evidence(parsed)
        counts = {field: evidence_counts[field] for field in FLAKE_POLICY_COUNT_FIELDS}
        complete = parsed["complete"]
        assert isinstance(complete, bool)
        if not complete:
            reason = "partial_run"
        elif counts["rerun"]:
            reason = "rerun_not_allowed"
        elif exit_code:
            reason = "quarantined_test_failed" if counts["failed"] else "command_failed"
        else:
            reason = "complete"
    except (FlakePolicyError, OSError):
        parsed = None
        counts = {
            "attempted": 0,
            "failed": 0,
            "passed": 0,
            "quarantined": 0,
            "rerun": 0,
            "skipped": 0,
        }
        complete = False
        reason = "invalid_output"
    report = {
        "artifact": artifact,
        "command": command,
        "complete": complete,
        "counts": counts,
        "exit_code": exit_code,
        "policy": "quarantine",
        "reason": reason,
        "schema_version": FLAKE_POLICY_SCHEMA_VERSION,
        "verdict": "success"
        if complete and not exit_code and not counts["failed"] and not counts["rerun"]
        else "failure",
    }
    return validate_report(report)


def validate_report(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FlakePolicyError("flake-policy report must be an object")
    expected = {
        "artifact",
        "command",
        "complete",
        "counts",
        "exit_code",
        "policy",
        "reason",
        "schema_version",
        "verdict",
    }
    if set(payload) != expected or payload["schema_version"] != FLAKE_POLICY_SCHEMA_VERSION:
        raise FlakePolicyError("flake-policy report has an unsupported shape or schema")
    if payload["command"] != FLAKE_POLICY_COMMAND or payload["policy"] != "quarantine":
        raise FlakePolicyError("flake-policy report command or policy is invalid")
    if payload["verdict"] not in {"failure", "success"}:
        raise FlakePolicyError("flake-policy report verdict is invalid")
    if not isinstance(payload["complete"], bool):
        raise FlakePolicyError("flake-policy report completion flag is invalid")
    if (
        not isinstance(payload["exit_code"], int)
        or isinstance(payload["exit_code"], bool)
        or payload["exit_code"] < 0
    ):
        raise FlakePolicyError("flake-policy report exit code is invalid")
    artifact = payload["artifact"]
    if (
        not isinstance(artifact, dict)
        or set(artifact) != {"path", "sha256", "size"}
        or not isinstance(artifact["path"], str)
        or not artifact["path"]
        or not isinstance(artifact["sha256"], str)
        or len(artifact["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in artifact["sha256"])
        or not isinstance(artifact["size"], int)
        or isinstance(artifact["size"], bool)
        or artifact["size"] < 0
    ):
        raise FlakePolicyError("flake-policy report artifact is invalid")
    counts = payload["counts"]
    if (
        not isinstance(counts, dict)
        or set(counts) != set(FLAKE_POLICY_COUNT_FIELDS)
        or any(
            not isinstance(counts[field], int)
            or isinstance(counts[field], bool)
            or counts[field] < 0
            for field in FLAKE_POLICY_COUNT_FIELDS
        )
        or counts["attempted"] != counts["passed"] + counts["failed"] + counts["skipped"]
    ):
        raise FlakePolicyError("flake-policy report counts are invalid")
    if not isinstance(payload["reason"], str) or not payload["reason"]:
        raise FlakePolicyError("flake-policy report reason is invalid")
    expected_verdict = (
        "success"
        if payload["complete"]
        and payload["exit_code"] == 0
        and counts["failed"] == 0
        and counts["rerun"] == 0
        else "failure"
    )
    if payload["verdict"] != expected_verdict:
        raise FlakePolicyError("flake-policy report verdict contradicts its evidence")
    return payload


def report_summary(report: dict[str, Any]) -> str:
    validate_report(report)
    counts = report["counts"]
    return "\n".join(
        [
            "## Playwright quarantine report",
            "",
            f"- Verdict: `{report['verdict']}` (`{report['reason']}`)",
            f"- Complete: `{report['complete']}`; exit code: `{report['exit_code']}`",
            "- Counts: "
            + "; ".join(f"{field}={counts[field]}" for field in FLAKE_POLICY_COUNT_FIELDS),
            f"- Output: `{report['artifact']['path']}@sha256:{report['artifact']['sha256']}`",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    command = parser.add_subparsers(dest="command", required=True)
    report_parser = command.add_parser("report")
    report_parser.add_argument("--output-log", required=True)
    report_parser.add_argument("--exit-code", required=True, type=int)
    report_parser.add_argument("--output", required=True)
    report_parser.add_argument("--summary")
    args = parser.parse_args()
    if args.command != "report":
        raise FlakePolicyError("unknown flake-policy command")
    report = build_report(
        output_path=args.output_log,
        command=FLAKE_POLICY_COMMAND,
        exit_code=args.exit_code,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.summary:
        summary = Path(args.summary)
        summary.parent.mkdir(parents=True, exist_ok=True)
        with summary.open("a", encoding="utf-8") as stream:
            stream.write(report_summary(report))
    if report["verdict"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
