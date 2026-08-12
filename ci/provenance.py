"""Code-owned provenance for the run-scoped CI selection artifact.

The selector payload remains the small, reviewed ``ci.selection`` document.  This module
adds a deterministic sidecar and the only permitted resolution rule: use this attempt's
artifact, or (on a failed-job rerun where the classifier was reused) attempt one of this
same workflow run.  It deliberately has no API for looking up another run or a latest
artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ci.selection import REASONS, SCHEMA_VERSION, dump_selection, validate_selection

PROVENANCE_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SELECTION_FILENAME = "ci-selection.json"
PROVENANCE_FILENAME = "ci-selection-provenance.json"
RESOLUTION_FILENAME = "ci-selection-resolution.json"


class EvidenceError(ValueError):
    """A bounded, safe diagnostic for rejected classifier evidence."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def canonical_selection_bytes(payload: object) -> bytes:
    """Return exactly the bytes written by :func:`ci.selection.dump_selection`."""

    validated = validate_selection(payload)
    return (json.dumps(validated, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def selection_digest(payload_or_bytes: object) -> str:
    """Hash canonical selection bytes, rejecting alternate JSON serializations."""

    if isinstance(payload_or_bytes, bytes):
        raw = payload_or_bytes
        payload = _load_canonical_selection_bytes(raw)
        if canonical_selection_bytes(payload) != raw:
            raise EvidenceError("selection_bytes_not_canonical")
    else:
        raw = canonical_selection_bytes(payload_or_bytes)
    return hashlib.sha256(raw).hexdigest()


def artifact_name(run_id: str | int, attempt: int) -> str:
    normalized_run = _run_id(run_id)
    normalized_attempt = _attempt(attempt)
    return f"ci-selection-{normalized_run}-attempt-{normalized_attempt}"


def build_provenance(
    selection: Mapping[str, Any],
    *,
    run_id: str | int,
    created_attempt: int,
    controller_sha: str,
    release_sha: str,
    source_after_sha: str | None,
    source_before_sha: str | None,
    artifact: str | None = None,
) -> dict[str, Any]:
    """Build the bounded sidecar and validate its source identity immediately."""

    validated = _validated_selection(selection)
    normalized_run = _run_id(run_id)
    normalized_attempt = _attempt(created_attempt)
    normalized_controller = _sha(controller_sha, "controller_sha")
    normalized_release = _sha(release_sha, "release_sha")
    expected_artifact = artifact_name(normalized_run, normalized_attempt)
    if artifact is not None and artifact != expected_artifact:
        raise EvidenceError("artifact_name_mismatch")
    if validated["head"] != normalized_release:
        raise EvidenceError("selection_release_sha_mismatch")
    event = validated["event"]
    if event == "push":
        if source_after_sha is None or (
            _sha(source_after_sha, "source_after_sha") != normalized_release
        ):
            raise EvidenceError("push_after_sha_mismatch")
        source_after = normalized_release
        source_before = _optional_sha(source_before_sha, "source_before_sha")
        if source_before is None or source_before != validated["base"]:
            raise EvidenceError("push_before_sha_mismatch")
    else:
        if source_after_sha is not None or source_before_sha is not None:
            raise EvidenceError("manual_source_identity_unexpected")
        source_after = None
        source_before = None
    return {
        "artifact_name": expected_artifact,
        "controller_sha": normalized_controller,
        "created_attempt": normalized_attempt,
        "profile": validated["profile"],
        "reason": validated["reason"],
        "release_sha": normalized_release,
        "run_id": normalized_run,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "selection_schema_version": SCHEMA_VERSION,
        "selection_sha256": selection_digest(validated),
        "source_after_sha": source_after,
        "source_before_sha": source_before,
    }


def dump_provenance(payload: Mapping[str, Any], path: str | Path) -> None:
    validated = validate_provenance_shape(payload)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(validated, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_provenance(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("provenance_malformed") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("provenance_shape_invalid")
    try:
        canonical = (
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceError("provenance_shape_invalid") from exc
    if canonical != raw:
        raise EvidenceError("provenance_bytes_not_canonical")
    return validate_provenance_shape(payload)


def validate_provenance_shape(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "artifact_name",
        "controller_sha",
        "created_attempt",
        "profile",
        "reason",
        "release_sha",
        "run_id",
        "schema_version",
        "selection_schema_version",
        "selection_sha256",
        "source_after_sha",
        "source_before_sha",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise EvidenceError("provenance_keys_invalid")
    if payload["schema_version"] != PROVENANCE_SCHEMA_VERSION:
        raise EvidenceError("provenance_schema_invalid")
    if payload["selection_schema_version"] != SCHEMA_VERSION:
        raise EvidenceError("selection_schema_invalid")
    run_id = _run_id(payload["run_id"])
    attempt = _attempt(payload["created_attempt"])
    controller = _sha(payload["controller_sha"], "controller_sha")
    release = _sha(payload["release_sha"], "release_sha")
    digest = payload["selection_sha256"]
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise EvidenceError("selection_digest_invalid")
    if payload["profile"] not in {"focused", "full"} or payload["reason"] not in REASONS:
        raise EvidenceError("provenance_selection_identity_invalid")
    expected_name = artifact_name(run_id, attempt)
    if payload["artifact_name"] != expected_name:
        raise EvidenceError("artifact_name_mismatch")
    source_after = _optional_sha(payload["source_after_sha"], "source_after_sha")
    source_before = _optional_sha(payload["source_before_sha"], "source_before_sha")
    return {
        "artifact_name": expected_name,
        "controller_sha": controller,
        "created_attempt": attempt,
        "profile": payload["profile"],
        "reason": payload["reason"],
        "release_sha": release,
        "run_id": run_id,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "selection_schema_version": SCHEMA_VERSION,
        "selection_sha256": digest,
        "source_after_sha": source_after,
        "source_before_sha": source_before,
    }


@dataclass(frozen=True)
class EvidenceResolution:
    selection: dict[str, Any]
    provenance: dict[str, Any]
    mode: str
    resolved_attempt: int

    def payload(self) -> dict[str, Any]:
        return {
            "artifact_name": self.provenance["artifact_name"],
            "controller_sha": self.provenance["controller_sha"],
            "created_attempt": self.provenance["created_attempt"],
            "mode": self.mode,
            "profile": self.provenance["profile"],
            "reason": self.provenance["reason"],
            "release_sha": self.provenance["release_sha"],
            "resolved_attempt": self.resolved_attempt,
            "run_id": self.provenance["run_id"],
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "selection_sha256": self.provenance["selection_sha256"],
            "source_after_sha": self.provenance["source_after_sha"],
            "source_before_sha": self.provenance["source_before_sha"],
        }


def validate_artifact(
    directory: str | Path,
    *,
    expected_run_id: str | int,
    expected_attempt: int,
    controller_sha: str,
    release_sha: str,
    event: str,
    source_after_sha: str | None,
    source_before_sha: str | None,
    expected_profile: str | None = None,
    expected_reason: str | None = None,
    expected_selection_sha256: str | None = None,
) -> EvidenceResolution:
    root = Path(directory)
    selection_path = root / SELECTION_FILENAME
    provenance_path = root / PROVENANCE_FILENAME
    if root.exists() and root.is_dir():
        names = {entry.name for entry in root.iterdir()}
        if names - {SELECTION_FILENAME, PROVENANCE_FILENAME}:
            raise EvidenceError("artifact_files_ambiguous")
    if not selection_path.is_file() or not provenance_path.is_file():
        raise EvidenceError("artifact_files_missing")
    raw_selection = selection_path.read_bytes()
    selection = _load_canonical_selection_bytes(raw_selection)
    provenance = load_provenance(provenance_path)
    run_id = _run_id(expected_run_id)
    attempt = _attempt(expected_attempt)
    if provenance["run_id"] != run_id:
        raise EvidenceError("run_id_mismatch")
    if provenance["created_attempt"] != attempt:
        raise EvidenceError("created_attempt_mismatch")
    if provenance["controller_sha"] != _sha(controller_sha, "controller_sha"):
        raise EvidenceError("controller_sha_mismatch")
    if provenance["release_sha"] != _sha(release_sha, "release_sha"):
        raise EvidenceError("release_sha_mismatch")
    if selection["event"] != event:
        raise EvidenceError("selection_event_mismatch")
    if selection["profile"] != provenance["profile"] or selection["reason"] != provenance["reason"]:
        raise EvidenceError("selection_provenance_mismatch")
    if expected_profile is not None and provenance["profile"] != expected_profile:
        raise EvidenceError("classifier_profile_mismatch")
    if expected_reason is not None and provenance["reason"] != expected_reason:
        raise EvidenceError("classifier_reason_mismatch")
    expected_digest = hashlib.sha256(raw_selection).hexdigest()
    if provenance["selection_sha256"] != expected_digest:
        raise EvidenceError("selection_digest_mismatch")
    if (
        expected_selection_sha256 is not None
        and provenance["selection_sha256"] != expected_selection_sha256
    ):
        raise EvidenceError("classifier_digest_mismatch")
    if selection["head"] != provenance["release_sha"]:
        raise EvidenceError("selection_head_mismatch")
    if event == "push":
        if provenance["source_after_sha"] != _sha(source_after_sha or "", "source_after_sha"):
            raise EvidenceError("source_after_sha_mismatch")
        if provenance["source_before_sha"] != _optional_sha(source_before_sha, "source_before_sha"):
            raise EvidenceError("source_before_sha_mismatch")
        if selection["base"] != provenance["source_before_sha"]:
            raise EvidenceError("selection_base_mismatch")
    else:
        if (
            provenance["source_after_sha"] is not None
            or provenance["source_before_sha"] is not None
        ):
            raise EvidenceError("manual_source_identity_present")
        if source_after_sha not in (None, "") or source_before_sha not in (None, ""):
            raise EvidenceError("manual_source_identity_unexpected")
    return EvidenceResolution(
        selection=selection,
        provenance=provenance,
        mode="current_attempt",
        resolved_attempt=attempt,
    )


def resolve_artifact(
    *,
    current_directory: str | Path,
    fallback_directory: str | Path,
    expected_run_id: str | int,
    current_attempt: int,
    classifier_created_attempt: int,
    controller_sha: str,
    release_sha: str,
    event: str,
    source_after_sha: str | None,
    source_before_sha: str | None,
    expected_profile: str | None = None,
    expected_reason: str | None = None,
    expected_selection_sha256: str | None = None,
) -> EvidenceResolution:
    """Resolve current evidence, or only a proven same-run attempt-one reuse."""

    current = Path(current_directory)
    fallback = Path(fallback_directory)
    current_has_bytes = any(
        (current / filename).exists() for filename in (SELECTION_FILENAME, PROVENANCE_FILENAME)
    )
    attempt = _attempt(current_attempt)
    classifier_attempt = _attempt(classifier_created_attempt)
    if current_has_bytes:
        # Presence of any current-attempt evidence is authoritative: parse and validate it
        # before considering classifier metadata.  A malformed current artifact must never
        # become a reason to use an older fallback.
        resolved = validate_artifact(
            current,
            expected_run_id=expected_run_id,
            expected_attempt=attempt,
            controller_sha=controller_sha,
            release_sha=release_sha,
            event=event,
            source_after_sha=source_after_sha,
            source_before_sha=source_before_sha,
            expected_profile=expected_profile,
            expected_reason=expected_reason,
            expected_selection_sha256=expected_selection_sha256,
        )
        if classifier_attempt != attempt:
            raise EvidenceError("current_classifier_attempt_mismatch")
        return resolved
    if attempt <= 1 or classifier_attempt != 1:
        raise EvidenceError("current_attempt_evidence_missing")
    resolved = validate_artifact(
        fallback,
        expected_run_id=expected_run_id,
        expected_attempt=1,
        controller_sha=controller_sha,
        release_sha=release_sha,
        event=event,
        source_after_sha=source_after_sha,
        source_before_sha=source_before_sha,
        expected_profile=expected_profile,
        expected_reason=expected_reason,
        expected_selection_sha256=expected_selection_sha256,
    )
    return EvidenceResolution(
        selection=resolved.selection,
        provenance=resolved.provenance,
        mode="reused_attempt_1",
        resolved_attempt=1,
    )


def dump_resolution(resolution: EvidenceResolution, output_directory: str | Path) -> None:
    destination = Path(output_directory)
    destination.mkdir(parents=True, exist_ok=True)
    dump_selection(resolution.selection, destination / SELECTION_FILENAME)
    dump_provenance(resolution.provenance, destination / PROVENANCE_FILENAME)
    (destination / RESOLUTION_FILENAME).write_text(
        json.dumps(resolution.payload(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_resolution(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("resolution_malformed") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("resolution_shape_invalid")
    try:
        canonical = (
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceError("resolution_shape_invalid") from exc
    if canonical != raw:
        raise EvidenceError("resolution_bytes_not_canonical")
    expected = {
        "artifact_name",
        "controller_sha",
        "created_attempt",
        "mode",
        "profile",
        "reason",
        "release_sha",
        "resolved_attempt",
        "run_id",
        "schema_version",
        "selection_sha256",
        "source_after_sha",
        "source_before_sha",
    }
    if set(payload) != expected:
        raise EvidenceError("resolution_keys_invalid")
    if payload["schema_version"] != PROVENANCE_SCHEMA_VERSION:
        raise EvidenceError("resolution_schema_invalid")
    if payload["mode"] not in {"current_attempt", "reused_attempt_1"}:
        raise EvidenceError("resolution_mode_invalid")
    run_id = _run_id(payload["run_id"])
    created_attempt = _attempt(payload["created_attempt"])
    resolved_attempt = _attempt(payload["resolved_attempt"])
    controller_sha = _sha(payload["controller_sha"], "controller_sha")
    release_sha = _sha(payload["release_sha"], "release_sha")
    if payload["artifact_name"] != artifact_name(run_id, created_attempt):
        raise EvidenceError("artifact_name_mismatch")
    if payload["profile"] not in {"focused", "full"} or payload["reason"] not in REASONS:
        raise EvidenceError("resolution_selection_identity_invalid")
    _optional_sha(payload["source_after_sha"], "source_after_sha")
    _optional_sha(payload["source_before_sha"], "source_before_sha")
    if not isinstance(payload["selection_sha256"], str) or not SHA256_RE.fullmatch(
        payload["selection_sha256"]
    ):
        raise EvidenceError("resolution_digest_invalid")
    if payload["created_attempt"] != payload["resolved_attempt"]:
        raise EvidenceError("resolution_attempt_mismatch")
    if payload["mode"] == "reused_attempt_1" and payload["resolved_attempt"] != 1:
        raise EvidenceError("resolution_fallback_attempt_invalid")
    if payload["mode"] == "current_attempt" and payload["resolved_attempt"] < 1:
        raise EvidenceError("resolution_current_attempt_invalid")
    return {
        **payload,
        "artifact_name": artifact_name(run_id, created_attempt),
        "controller_sha": controller_sha,
        "created_attempt": created_attempt,
        "release_sha": release_sha,
        "resolved_attempt": resolved_attempt,
        "run_id": run_id,
    }


def write_rejection(path: str | Path, *, reason: str) -> None:
    """Write bounded failure evidence so aggregate gates can publish a safe diagnostic."""

    safe_reason = reason if re.fullmatch(r"[a-z0-9_]{1,80}", reason) else "evidence_rejected"
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "reason": safe_reason,
                "schema_version": PROVENANCE_SCHEMA_VERSION,
                "status": "rejected",
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _load_canonical_selection_bytes(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("selection_malformed") from exc
    validated = _validated_selection(payload)
    if canonical_selection_bytes(validated) != raw:
        raise EvidenceError("selection_bytes_not_canonical")
    return validated


def _validated_selection(payload: object) -> dict[str, Any]:
    try:
        return validate_selection(payload)
    except (TypeError, ValueError) as exc:
        raise EvidenceError("selection_invalid") from exc


def _run_id(value: str | int) -> str:
    normalized = str(value)
    if RUN_ID_RE.fullmatch(normalized) is None:
        raise EvidenceError("run_id_invalid")
    return normalized


def _attempt(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EvidenceError("attempt_invalid")
    return value


def _sha(value: str, field: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise EvidenceError(f"{field}_invalid")
    return value


def _optional_sha(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _sha(value, field)


def _resolution_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--current-directory", required=True)
    parser.add_argument("--fallback-directory", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--current-attempt", required=True, type=int)
    parser.add_argument("--classifier-created-attempt", required=True, type=int)
    parser.add_argument("--controller-sha", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--source-after-sha")
    parser.add_argument("--source-before-sha")
    parser.add_argument("--expected-profile")
    parser.add_argument("--expected-reason")
    parser.add_argument("--expected-selection-sha256")
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--rejection-output", required=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve")
    _resolution_args(resolve)
    args = parser.parse_args()
    if args.command != "resolve":
        raise SystemExit(2)
    try:
        resolution = resolve_artifact(
            current_directory=args.current_directory,
            fallback_directory=args.fallback_directory,
            expected_run_id=args.run_id,
            current_attempt=args.current_attempt,
            classifier_created_attempt=args.classifier_created_attempt,
            controller_sha=args.controller_sha,
            release_sha=args.release_sha,
            event=args.event,
            source_after_sha=args.source_after_sha,
            source_before_sha=args.source_before_sha,
            expected_profile=args.expected_profile,
            expected_reason=args.expected_reason,
            expected_selection_sha256=args.expected_selection_sha256,
        )
        dump_resolution(resolution, args.output_directory)
    except (EvidenceError, OSError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, EvidenceError) else "evidence_invalid"
        write_rejection(args.rejection_output, reason=reason)
        print(f"CI selection evidence rejected: {reason}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
