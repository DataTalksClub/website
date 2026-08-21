from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ci.evidence import (
    ALLOWED_COMPONENT_COMMANDS,
    RESULTS,
    SHA256_RE,
    SHA_RE,
    VALIDITY_SECONDS,
    EvidenceError,
    artifact_records,
    build_envelope,
    choose_reusable_evidence,
    component_inputs,
    environment_fingerprint,
    environment_matches_plan,
    git_manifest,
    isoformat,
    load_envelopes,
    machine_output_claim,
    sha256_json,
    utc_now,
    validate_artifact_files,
    validate_envelope,
    validate_environment_fingerprint,
    validate_machine_output_files,
    validate_origin_trust,
    validate_screenshot_for_plan,
    worktree_manifest,
)
from ci.ownership import Impact, graph_digest, impact_for_paths, load_graph, matches_any
from ci.selection import ChangeRecord, load_selection, parse_name_status

PLAN_SCHEMA_VERSION = 3
REPORT_SCHEMA_VERSION = 3
SCHEDULED_STATE_SCHEMA_VERSION = 1
PLAN_COMPONENTS = (
    "compatibility",
    "container",
    "content_invariants",
    "django",
    "evidence_validation",
    "playwright",
    "quality",
    "screenshots",
    "selector",
)
AUTOMATED_COMPONENTS = frozenset(PLAN_COMPONENTS) - {"screenshots"}
COMPONENT_REQUIRED_CONFIG = {
    "django": {"DJANGO_SETTINGS_MODULE": "website.settings.test"},
    "playwright": {
        "DJANGO_ALLOW_ASYNC_UNSAFE": "true",
        "DJANGO_SETTINGS_MODULE": "website.settings.test",
    },
    "screenshots": {
        "DJANGO_ALLOW_ASYNC_UNSAFE": "true",
        "DJANGO_SETTINGS_MODULE": "website.settings.test",
    },
}
FULL_RISK_FLAGS = frozenset(
    {
        "auth_security_privacy",
        "compatibility_contract",
        "dependency_toolchain",
        "deployment_runtime",
        "global_fixture",
        "schema_migration",
        "shared_runtime",
        "test_infrastructure",
    }
)
INVALID_EVIDENCE_REASONS = frozenset(
    {
        "artifact_digest_mismatch",
        "component_contract_changed",
        "environment_changed",
        "expired",
        "latest_result_action_required",
        "latest_result_cancelled",
        "latest_result_failure",
        "latest_result_skipped",
        "latest_result_stale",
        "latest_result_timed_out",
        "later_non_successful_run",
        "machine_output_mismatch",
        "missing_artifact",
        "no_matching_evidence",
        "policy_changed",
        "relevant_inputs_changed",
        "render_inputs_changed",
        "screenshot_coverage_incomplete",
        "superseded",
        "untrusted_origin",
    }
)


class VerificationError(ValueError):
    """A verification plan, component, or report failed closed validation."""


def component_environment_fingerprint(
    component: str, environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Return the exact environment authorized for one component execution."""
    if component not in PLAN_COMPONENTS:
        raise VerificationError("unknown verification component")
    resolved = dict(os.environ if environ is None else environ)
    resolved.update(COMPONENT_REQUIRED_CONFIG.get(component, {}))
    return environment_fingerprint(resolved)


def read_change_records(repository: str | Path, base: str, head: str) -> tuple[ChangeRecord, ...]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repository),
                "diff",
                "--name-status",
                "-z",
                "--find-renames",
                "--find-copies",
                base,
                head,
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VerificationError("the canonical Git change range is unavailable") from exc
    if result.stdout == b"":
        return ()
    try:
        return parse_name_status(result.stdout)
    except ValueError as exc:
        raise VerificationError("the canonical Git change range is malformed") from exc


def read_worktree_change_records(
    repository: str | Path, base: str, head: str
) -> tuple[ChangeRecord, ...]:
    repository = Path(repository)
    try:
        actual_head = subprocess.run(
            ["git", "-C", os.fspath(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if actual_head != head:
            raise VerificationError("worktree HEAD does not match the requested head")
        changed = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repository),
                "diff",
                "--name-status",
                "-z",
                "--find-renames",
                "--find-copies",
                base,
            ],
            check=True,
            capture_output=True,
        ).stdout
        untracked = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repository),
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VerificationError("the canonical worktree change range is unavailable") from exc
    try:
        records = list(parse_name_status(changed))
        records.extend(
            ChangeRecord("A", (raw.decode("utf-8", errors="surrogateescape"),))
            for raw in untracked[:-1].split(b"\0")
            if raw
        )
    except ValueError as exc:
        raise VerificationError("the canonical worktree change range is malformed") from exc
    return tuple(records)


def build_plan(
    *,
    repository: str | Path,
    repository_id: str,
    base: str,
    head: str,
    selection: Mapping[str, Any],
    records: Sequence[ChangeRecord] | None = None,
    evidence_directory: str | Path | None = None,
    consumer: str = "engineer",
    now: datetime | None = None,
    graph: Mapping[str, Any] | None = None,
    history: Sequence[Mapping[str, Any]] = (),
    release_requires_image: bool = False,
    include_worktree: bool = False,
) -> dict[str, Any]:
    graph = dict(graph) if graph is not None else load_graph()
    digest = graph_digest(graph)
    now = now or utc_now()
    if records is None:
        records = (
            read_worktree_change_records(repository, base, head)
            if include_worktree
            else read_change_records(repository, base, head)
        )
    paths = tuple(path for record in records for path in record.paths)
    impact = impact_for_paths(paths, graph)
    impact = _with_status_risks(impact, records, graph)
    manifest, source_tree = (
        worktree_manifest(repository, head) if include_worktree else git_manifest(repository, head)
    )
    environment = environment_fingerprint()
    force_full, reason = _profile(selection, impact)
    browser_profile = (
        "full" if impact.render_impact or "surface.playwright" in impact.owners else "core"
    )
    render_inputs = component_inputs(
        manifest, graph["components"]["screenshots"]["relevant_patterns"]
    )
    render_payload = {
        "impact": impact.render_impact,
        "reasons": list(impact.render_reasons),
        "required_captures": (
            _required_screenshot_captures(
                graph,
                environment,
                impacted_nodes=(*impact.owners, *impact.downstream),
            )
            if impact.render_impact
            else []
        ),
        "sha256": sha256_json(
            {
                "browser": environment["browser"],
                "inputs": render_inputs["aggregate_sha256"],
                "playwright": environment["playwright"],
            }
        ),
    }
    changed_paths = sorted(
        ({"path": path, "status": record.status} for record in records for path in record.paths),
        key=lambda item: (item["path"], item["status"]),
    )
    plan: dict[str, Any] = {
        "base": base,
        "browser_profile": browser_profile,
        "changed_paths": changed_paths,
        "components": {},
        "created_at": isoformat(now),
        "direct_components": list(impact.components),
        "direct_nodes": list(impact.owners),
        "documentation_only": impact.documentation_only,
        "downstream_nodes": list(impact.downstream),
        "environment": environment,
        "graph_sha256": digest,
        "head": head,
        "legacy_selection": dict(selection),
        "large_content": _large_content_decision(paths, graph, manifest),
        "policy_version": graph["policy_version"],
        "profile": (
            "full" if force_full else "documentation" if impact.documentation_only else "focused"
        ),
        "reason": reason,
        "release_requires_image": release_requires_image,
        "render": render_payload,
        "repository": repository_id,
        "risk_flags": list(impact.risk_flags),
        "schema_version": PLAN_SCHEMA_VERSION,
        "source_tree": source_tree,
        "source_mode": "worktree" if include_worktree else "commit",
        "test_labels": list(impact.test_labels),
        "unknown_paths": list(impact.unknown_paths),
    }
    evidence_root = (
        Path(evidence_directory) if evidence_directory is not None else Path(".tmp/no-evidence")
    )
    candidates = load_envelopes(evidence_root) if evidence_directory is not None else []
    for component in PLAN_COMPONENTS:
        component_policy = graph["components"][component]
        inputs = component_inputs(manifest, component_policy["relevant_patterns"])
        required, fresh, component_reason = _component_requirement(
            component,
            force_full=force_full,
            impact=impact,
            browser_profile=browser_profile,
            large_content_impact=plan["large_content"]["impact"],
            release_requires_image=release_requires_image,
        )
        component_plan = {
            "command": _component_command(
                component,
                component_policy["command"],
                browser_profile=browser_profile,
                profile=plan["profile"],
                release_requires_image=release_requires_image,
                source_mode=plan["source_mode"],
            ),
            "disposition": "not_applicable",
            "environment": component_environment_fingerprint(component),
            "evidence": None,
            "inputs": inputs,
            "reason": component_reason,
            "required": required,
            "validity_class": component_policy["validity_class"],
            "validity_seconds": graph["validity_classes"][component_policy["validity_class"]],
        }
        plan["components"][component] = component_plan
        if not required:
            continue
        if fresh:
            component_plan["disposition"] = "rerun"
            continue
        reusable, reuse_reason = choose_reusable_evidence(
            plan=plan,
            component=component,
            candidates=candidates,
            evidence_root=evidence_root,
            consumer=consumer,
            now=now,
            history=history,
        )
        if reusable is not None:
            component_plan["disposition"] = "reused"
            component_plan["evidence"] = _reuse_reference(reusable)
            component_plan["reason"] = reuse_reason
        elif component == "screenshots" and not impact.render_impact:
            component_plan["disposition"] = "not_applicable"
            component_plan["required"] = False
            component_plan["reason"] = "render_inputs_unchanged_without_reusable_visual"
        else:
            component_plan["disposition"] = "rerun"
            component_plan["reason"] = reuse_reason
    return validate_plan(plan)


def validate_plan(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise VerificationError("verification plan must be an object")
    expected = {
        "base",
        "browser_profile",
        "changed_paths",
        "components",
        "created_at",
        "direct_components",
        "direct_nodes",
        "documentation_only",
        "downstream_nodes",
        "environment",
        "graph_sha256",
        "head",
        "legacy_selection",
        "large_content",
        "policy_version",
        "profile",
        "reason",
        "release_requires_image",
        "render",
        "repository",
        "risk_flags",
        "schema_version",
        "source_tree",
        "source_mode",
        "test_labels",
        "unknown_paths",
    }
    if set(payload) != expected or payload["schema_version"] != PLAN_SCHEMA_VERSION:
        raise VerificationError("verification plan has an unsupported shape or schema")
    if payload["profile"] not in {"documentation", "focused", "full"}:
        raise VerificationError("verification profile is unsupported")
    if payload["browser_profile"] not in {"core", "full"}:
        raise VerificationError("browser profile is unsupported")
    for field in ("base", "head", "repository", "graph_sha256", "reason", "created_at"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise VerificationError(f"{field} must be a non-empty string")
    for field in (
        "direct_components",
        "direct_nodes",
        "downstream_nodes",
        "risk_flags",
        "test_labels",
        "unknown_paths",
    ):
        values = payload[field]
        if (
            not isinstance(values, list)
            or values != sorted(set(values))
            or any(not isinstance(item, str) for item in values)
        ):
            raise VerificationError(f"{field} must be sorted unique strings")
    if not isinstance(payload["documentation_only"], bool):
        raise VerificationError("documentation_only must be boolean")
    if not isinstance(payload["release_requires_image"], bool):
        raise VerificationError("release_requires_image must be boolean")
    try:
        validate_environment_fingerprint(payload["environment"])
    except ValueError as exc:
        raise VerificationError("planner environment fingerprint is invalid") from exc
    if payload["source_mode"] not in {"commit", "worktree"}:
        raise VerificationError("source_mode is unsupported")
    changes = payload["changed_paths"]
    if not isinstance(changes, list) or any(
        not isinstance(item, dict)
        or set(item) != {"path", "status"}
        or not isinstance(item["path"], str)
        or not isinstance(item["status"], str)
        for item in changes
    ):
        raise VerificationError("changed paths have an invalid shape")
    components = payload["components"]
    if not isinstance(components, dict) or tuple(sorted(components)) != PLAN_COMPONENTS:
        raise VerificationError("verification plan components are incomplete")
    for component, item in components.items():
        _validate_component_plan(component, item)
    render = payload["render"]
    if not isinstance(render, dict) or set(render) != {
        "impact",
        "reasons",
        "required_captures",
        "sha256",
    }:
        raise VerificationError("render decision has an invalid shape")
    if not isinstance(render["impact"], bool) or not isinstance(render["reasons"], list):
        raise VerificationError("render decision values are invalid")
    _validate_required_captures(render["required_captures"], impact=render["impact"])
    large_content = payload["large_content"]
    if (
        not isinstance(large_content, dict)
        or set(large_content) != {"impact", "paths", "sha256"}
        or not isinstance(large_content["impact"], bool)
        or not isinstance(large_content["paths"], list)
        or large_content["paths"] != sorted(set(large_content["paths"]))
        or large_content["impact"] != bool(large_content["paths"])
        or not isinstance(large_content["sha256"], str)
    ):
        raise VerificationError("large-content decision is invalid")
    if payload["profile"] == "full":
        for component in ("container", "django", "playwright", "quality"):
            if components[component]["disposition"] != "rerun":
                raise VerificationError("full profile automated components must rerun fresh")
    if render["impact"] and components["screenshots"]["disposition"] != "rerun":
        raise VerificationError("render impact requires fresh screenshot work")
    return payload


def dump_json(payload: object, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_plan(path: str | Path) -> dict[str, Any]:
    return validate_plan(json.loads(Path(path).read_text(encoding="utf-8")))


def plan_summary(plan: Mapping[str, Any]) -> str:
    validate_plan(dict(plan))
    lines = [
        "## Risk-based verification plan",
        "",
        f"- Base/head: `{plan['base']}` / `{plan['head']}`",
        f"- Profile: `{plan['profile']}` (`{plan['reason']}`)",
        f"- Graph: `{plan['graph_sha256']}` (policy `{plan['policy_version']}`)",
        f"- Nodes: `{', '.join(plan['direct_nodes']) or 'none'}`",
        f"- Downstream: `{', '.join(plan['downstream_nodes']) or 'none'}`",
        f"- Risk flags: `{', '.join(plan['risk_flags']) or 'none'}`",
        f"- Render impact: `{str(plan['render']['impact']).lower()}`",
        "",
        "| Component | Disposition | Environment | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for component in PLAN_COMPONENTS:
        item = plan["components"][component]
        lines.append(
            f"| `{component}` | `{item['disposition']}` | "
            f"`{item['environment']['sha256']}` | `{item['reason']}` |"
        )
    lines.append("")
    return "\n".join(lines)


def component_mode(plan: Mapping[str, Any], component: str) -> str:
    validated = validate_plan(dict(plan))
    if component not in validated["components"]:
        raise VerificationError("component is not in the plan")
    return validated["components"][component]["disposition"]


def create_report(
    *,
    plan: Mapping[str, Any],
    result_directory: str | Path | None = None,
    phase: str = "engineer",
) -> dict[str, Any]:
    plan = validate_plan(dict(plan))
    if phase not in {"ci", "engineer", "tester"}:
        raise VerificationError("report phase is unsupported")
    result_envelopes = load_envelopes(result_directory) if result_directory is not None else []
    latest_by_component: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path, envelope in result_envelopes:
        current = latest_by_component.get(envelope["component"])
        if current is None or envelope["produced_at"] > current[1]["produced_at"]:
            latest_by_component[envelope["component"]] = (path, envelope)
    buckets: dict[str, list[dict[str, Any]]] = {
        "not_applicable": [],
        "rerun": [],
        "reused": [],
        "skipped": [],
    }
    verdict = "success"
    state_sha256 = _plan_verification_state_sha256(plan)
    for component in PLAN_COMPONENTS:
        item = plan["components"][component]
        disposition = item["disposition"]
        entry: dict[str, Any] = {
            "component": component,
            "proof": _component_report_proof(plan, component, state_sha256=state_sha256),
            "reason": item["reason"],
        }
        if disposition == "not_applicable":
            buckets["not_applicable"].append(entry)
        elif disposition == "reused":
            entry["evidence"] = item["evidence"]
            buckets["reused"].append(entry)
        elif disposition == "rerun":
            result_record = latest_by_component.get(component)
            result_path, result_envelope = (
                result_record if result_record is not None else (None, None)
            )
            evidence_valid = False
            if result_envelope is not None and result_path is not None:
                try:
                    evidence_valid = _envelope_matches_plan(
                        result_envelope, plan=plan, component=component, rerun=True
                    )
                    validate_origin_trust(
                        result_envelope["origin"],
                        consumer=phase,
                        plan=plan,
                        component=component,
                    )
                    validate_artifact_files(
                        result_envelope["artifacts"],
                        evidence_root=Path(result_directory or result_path.parent),
                        envelope_path=result_path,
                    )
                    validate_machine_output_files(
                        result_envelope,
                        evidence_root=Path(result_directory or result_path.parent),
                        envelope_path=result_path,
                        plan=plan,
                    )
                    if component == "screenshots":
                        validate_screenshot_for_plan(result_envelope, plan=plan)
                except (OSError, ValueError):
                    evidence_valid = False
            if result_envelope is not None and evidence_valid:
                entry["evidence"] = _reuse_reference(result_envelope)
                entry["result"] = result_envelope["result"]
                if result_envelope["result"] != "success":
                    verdict = "failure"
                buckets["rerun"].append(entry)
            elif component == "screenshots" and phase == "engineer":
                entry["reason"] = "independent_tester_required"
                buckets["skipped"].append(entry)
                # A failed rerun component keeps the failure verdict: the pending
                # tester screenshot gate never downgrades evidence of a failure,
                # matching the precedence `validate_report` derives from the buckets.
                if verdict != "failure":
                    verdict = "pending_independent_tester"
            else:
                entry["reason"] = (
                    "required_result_plan_mismatch"
                    if result_envelope is not None
                    else "required_result_missing"
                )
                buckets["skipped"].append(entry)
                verdict = "failure"
        else:
            raise VerificationError("plan disposition is unsupported")
    report = {
        "base": plan["base"],
        "buckets": buckets,
        "direct_nodes": plan["direct_nodes"],
        "downstream_nodes": plan["downstream_nodes"],
        "graph_sha256": plan["graph_sha256"],
        "head": plan["head"],
        "invalid_evidence": [
            {"component": component, "reason": item["reason"]}
            for component, item in sorted(plan["components"].items())
            if item["disposition"] == "rerun" and item["reason"] in INVALID_EVIDENCE_REASONS
        ],
        "phase": phase,
        "plan_sha256": sha256_json(plan),
        "policy_version": plan["policy_version"],
        "profile": plan["profile"],
        "reason": plan["reason"],
        "render": plan["render"],
        "repository": plan["repository"],
        "risk_flags": plan["risk_flags"],
        "schema_version": REPORT_SCHEMA_VERSION,
        "verdict": verdict,
        "verification_state_sha256": state_sha256,
    }
    return validate_report(
        report,
        plan=plan,
        evidence_directory=result_directory,
        allow_pending=phase == "engineer",
    )


def validate_evidence_directory(
    *, directory: str | Path, plan: Mapping[str, Any], consumer: str
) -> list[tuple[Path, dict[str, Any]]]:
    plan = validate_plan(dict(plan))
    root = Path(directory)
    paths = sorted(path for path in root.rglob("*-evidence.json") if path.is_file())
    if not paths:
        raise VerificationError("evidence directory contains no component envelopes")
    identities: set[str] = set()
    components: set[str] = set()
    validated: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            envelope = validate_envelope(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise VerificationError("evidence directory contains an invalid envelope") from exc
        component = envelope["component"]
        if path.name != f"{component}-evidence.json":
            raise VerificationError("evidence envelope filename does not match its component")
        if envelope["evidence_id"] in identities or component in components:
            raise VerificationError("evidence directory contains duplicate evidence")
        item = plan["components"].get(component)
        if item is None or item["disposition"] != "rerun":
            raise VerificationError("evidence directory contains an unplanned rerun")
        if not _envelope_matches_plan(envelope, plan=plan, component=component, rerun=True):
            raise VerificationError("evidence envelope does not match the plan")
        validate_origin_trust(envelope["origin"], consumer=consumer, plan=plan, component=component)
        validate_artifact_files(envelope["artifacts"], evidence_root=root, envelope_path=path)
        validate_machine_output_files(
            envelope,
            evidence_root=root,
            envelope_path=path,
            plan=plan,
        )
        if component == "screenshots":
            validate_screenshot_for_plan(envelope, plan=plan)
        identities.add(envelope["evidence_id"])
        components.add(component)
        validated.append((path, envelope))
    return validated


def validate_report(
    payload: object,
    *,
    plan: Mapping[str, Any] | None = None,
    evidence_directory: str | Path | None = None,
    allow_pending: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise VerificationError("verification report must be an object")
    expected = {
        "base",
        "buckets",
        "direct_nodes",
        "downstream_nodes",
        "graph_sha256",
        "head",
        "invalid_evidence",
        "phase",
        "plan_sha256",
        "policy_version",
        "profile",
        "reason",
        "render",
        "repository",
        "risk_flags",
        "schema_version",
        "verdict",
        "verification_state_sha256",
    }
    if set(payload) != expected or payload["schema_version"] != REPORT_SCHEMA_VERSION:
        raise VerificationError("verification report has an unsupported shape or schema")
    if payload["verdict"] not in {"failure", "pending_independent_tester", "success"}:
        raise VerificationError("verification report verdict is unsupported")
    if payload["phase"] not in {"ci", "engineer", "tester"}:
        raise VerificationError("verification report phase is unsupported")
    for field in ("graph_sha256", "plan_sha256", "verification_state_sha256"):
        if not isinstance(payload[field], str) or not SHA256_RE.fullmatch(payload[field]):
            raise VerificationError("verification report digest is invalid")
    if not isinstance(payload["policy_version"], int) or isinstance(
        payload["policy_version"], bool
    ):
        raise VerificationError("verification report policy version is invalid")
    for field in ("direct_nodes", "downstream_nodes", "risk_flags"):
        if (
            not isinstance(payload[field], list)
            or payload[field] != sorted(set(payload[field]))
            or any(not isinstance(item, str) for item in payload[field])
        ):
            raise VerificationError("verification report graph metadata is invalid")
    invalid_evidence = payload["invalid_evidence"]
    if (
        not isinstance(invalid_evidence, list)
        or any(
            not isinstance(item, dict)
            or set(item) != {"component", "reason"}
            or item["component"] not in PLAN_COMPONENTS
            or not isinstance(item["reason"], str)
            or not item["reason"]
            for item in invalid_evidence
        )
        or invalid_evidence
        != sorted(invalid_evidence, key=lambda item: (item["component"], item["reason"]))
    ):
        raise VerificationError("verification report invalid-evidence records are malformed")
    if payload["verdict"] == "pending_independent_tester" and not allow_pending:
        raise VerificationError("pending human evidence cannot pass final validation")
    buckets = payload["buckets"]
    if not isinstance(buckets, dict) or set(buckets) != {
        "not_applicable",
        "rerun",
        "reused",
        "skipped",
    }:
        raise VerificationError("verification report buckets are incomplete")
    seen: set[str] = set()
    for bucket, entries in buckets.items():
        if not isinstance(entries, list):
            raise VerificationError("verification report bucket must be a list")
        for entry in entries:
            expected_entry = {"component", "proof", "reason"}
            if bucket in {"rerun", "reused"}:
                expected_entry.add("evidence")
            if bucket == "rerun":
                expected_entry.add("result")
            if not isinstance(entry, dict) or set(entry) != expected_entry:
                raise VerificationError("verification report entry is malformed")
            component = entry["component"]
            if component not in PLAN_COMPONENTS or component in seen:
                raise VerificationError("verification report component is missing or duplicated")
            seen.add(component)
            if bucket == "rerun" and entry["result"] not in RESULTS:
                raise VerificationError("rerun report entry has an unsupported result")
            if bucket in {"rerun", "reused"}:
                _validate_evidence_reference(entry["evidence"])
                if (
                    component == "playwright"
                    and (bucket == "reused" or entry["result"] == "success")
                    and not {"attempted", "quarantined", "rerun"}.issubset(
                        entry["evidence"]["counts"]
                    )
                ):
                    raise VerificationError(
                        "successful Playwright evidence omits the flake-policy counts"
                    )
            _validate_component_report_proof(entry["proof"])
    if seen != set(PLAN_COMPONENTS):
        raise VerificationError("verification report does not classify every component")
    if plan is not None:
        plan = validate_plan(dict(plan))
        if payload["plan_sha256"] != sha256_json(plan):
            raise VerificationError("verification report plan digest does not match")
        for field in (
            "base",
            "direct_nodes",
            "downstream_nodes",
            "graph_sha256",
            "head",
            "policy_version",
            "profile",
            "reason",
            "render",
            "repository",
            "risk_flags",
        ):
            if payload[field] != plan[field]:
                raise VerificationError("verification report metadata does not match its plan")
        expected_state = _plan_verification_state_sha256(plan)
        if payload["verification_state_sha256"] != expected_state:
            raise VerificationError("verification report state digest does not match its plan")
        expected_invalid = [
            {"component": component, "reason": item["reason"]}
            for component, item in sorted(plan["components"].items())
            if item["disposition"] == "rerun" and item["reason"] in INVALID_EVIDENCE_REASONS
        ]
        if payload["invalid_evidence"] != expected_invalid:
            raise VerificationError("verification report invalid-evidence records do not match")
        bucket_for_component = {
            entry["component"]: bucket for bucket, entries in buckets.items() for entry in entries
        }
        for component, item in plan["components"].items():
            bucket = bucket_for_component[component]
            entry = next(
                candidate
                for entries in buckets.values()
                for candidate in entries
                if candidate["component"] == component
            )
            if entry["proof"] != _component_report_proof(
                plan, component, state_sha256=expected_state
            ):
                raise VerificationError("verification report component proof does not match")
            if (
                item["disposition"] in {"not_applicable", "reused"}
                and bucket != item["disposition"]
            ):
                raise VerificationError("verification report bucket contradicts the plan")
            if item["disposition"] == "rerun" and bucket not in {"rerun", "skipped"}:
                raise VerificationError("verification report rerun disposition is unproved")
        evidence_entries = [
            (name, entry) for name in ("rerun", "reused") for entry in buckets[name]
        ]
        if evidence_entries:
            if evidence_directory is None:
                raise VerificationError("report evidence directory is required")
            root = Path(evidence_directory)
            envelopes = load_envelopes(root)
            by_id: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
            for path, envelope in envelopes:
                by_id.setdefault(envelope["evidence_id"], []).append((path, envelope))
            for evidence_bucket, entry in evidence_entries:
                matches = by_id.get(entry["evidence"]["evidence_id"], [])
                if len(matches) != 1:
                    raise VerificationError("report evidence identity is missing or ambiguous")
                envelope_path, envelope = matches[0]
                component = entry["component"]
                if (
                    not _envelope_matches_plan(
                        envelope,
                        plan=plan,
                        component=component,
                        rerun=evidence_bucket == "rerun",
                    )
                    or _reuse_reference(envelope) != entry["evidence"]
                ):
                    raise VerificationError("report evidence does not match its envelope or plan")
                validate_origin_trust(
                    envelope["origin"],
                    consumer=payload["phase"],
                    plan=plan,
                    component=component,
                )
                validate_artifact_files(
                    envelope["artifacts"], evidence_root=root, envelope_path=envelope_path
                )
                validate_machine_output_files(
                    envelope,
                    evidence_root=root,
                    envelope_path=envelope_path,
                    plan=plan,
                )
                if component == "screenshots":
                    validate_screenshot_for_plan(envelope, plan=plan)
        for entry in buckets["skipped"]:
            component = entry["component"]
            if plan["components"][component]["required"] and not (
                payload["verdict"] == "failure"
                or (
                    allow_pending
                    and component == "screenshots"
                    and entry["reason"] == "independent_tester_required"
                )
            ):
                raise VerificationError("a required skipped component must fail the report")
        failing_rerun = any(entry["result"] != "success" for entry in buckets["rerun"])
        pending_screenshot = any(
            entry["component"] == "screenshots" and entry["reason"] == "independent_tester_required"
            for entry in buckets["skipped"]
        )
        blocking_required_skip = any(
            plan["components"][entry["component"]]["required"]
            and not (
                entry["component"] == "screenshots"
                and entry["reason"] == "independent_tester_required"
            )
            for entry in buckets["skipped"]
        )
        expected_verdict = (
            "failure"
            if failing_rerun or blocking_required_skip
            else "pending_independent_tester"
            if pending_screenshot
            else "success"
        )
        if payload["verdict"] != expected_verdict:
            raise VerificationError("verification report verdict contradicts its evidence")
    return payload


def report_summary(report: Mapping[str, Any]) -> str:
    validate_report(dict(report), allow_pending=True)
    lines = [
        "## Verification evidence report",
        "",
        f"- Verdict: `{report['verdict']}`",
        f"- Profile: `{report['profile']}` (`{report['reason']}`)",
        f"- Plan: `{report['plan_sha256']}`",
        f"- Policy: `{report['policy_version']}` / graph `{report['graph_sha256']}`",
        f"- Direct nodes: `{', '.join(report['direct_nodes']) or 'none'}`",
        f"- Downstream nodes: `{', '.join(report['downstream_nodes']) or 'none'}`",
        f"- Risk flags: `{', '.join(report['risk_flags']) or 'none'}`",
        f"- Verification state: `{report['verification_state_sha256']}`",
        "- Invalid evidence: "
        + (
            ", ".join(
                f"`{item['component']}:{item['reason']}`" for item in report["invalid_evidence"]
            )
            or "none"
        ),
        "",
    ]
    for bucket in ("rerun", "reused", "skipped", "not_applicable"):
        lines.append(f"### {bucket.replace('_', ' ').title()}")
        lines.append("")
        entries = report["buckets"][bucket]
        if not entries:
            lines.append("- None")
        for entry in entries:
            proof = entry["proof"]
            evidence = entry.get("evidence")
            lines.append(f"- `{entry['component']}`: {entry['reason']}")
            lines.append(f"  - Command: `{proof['command']}`")
            lines.append(
                "  - Input/environment/state: "
                f"`{proof['input_sha256']}` / `{proof['environment_sha256']}` / "
                f"`{proof['state_sha256']}`"
            )
            if evidence is None:
                lines.append("  - Evidence/counts/origin/expiry/artifacts: `not applicable`")
                continue
            counts = ", ".join(
                f"{key}={value}" for key, value in sorted(evidence["counts"].items())
            )
            origin = json.dumps(evidence["origin"], sort_keys=True, separators=(",", ":"))
            artifacts = ", ".join(
                f"{item['path']}@sha256:{item['sha256']}" for item in evidence["artifacts"]
            )
            lines.append(f"  - Result/counts: `{entry.get('result', 'success')}` / `{counts}`")
            if entry["component"] == "playwright" and {
                "attempted",
                "quarantined",
                "rerun",
            }.issubset(evidence["counts"]):
                lines.append(
                    "  - Flake-policy counts: "
                    f"attempted={evidence['counts']['attempted']}, "
                    f"passed={evidence['counts']['passed']}, "
                    f"failed={evidence['counts']['failed']}, "
                    f"rerun={evidence['counts']['rerun']}, "
                    f"quarantined={evidence['counts']['quarantined']}"
                )
            lines.append(
                f"  - Evidence: `{evidence['evidence_id']}`; output "
                f"`{evidence['output']['format']}@sha256:{evidence['output']['artifact']['sha256']}`"
            )
            lines.append(f"  - Origin/expiry: `{origin}` / `{evidence['expires_at']}`")
            lines.append(f"  - Artifacts: `{artifacts}`")
        lines.append("")
    return "\n".join(lines)


def _plan_verification_state_sha256(plan: Mapping[str, Any]) -> str:
    return sha256_json(
        {
            "component_environment_sha256": {
                component: item["environment"]["sha256"]
                for component, item in sorted(plan["components"].items())
            },
            "environment_sha256": plan["environment"]["sha256"],
            "graph_sha256": plan["graph_sha256"],
            "policy_version": plan["policy_version"],
            "source_manifest_sha256": plan["source_tree"]["manifest_sha256"],
            "tree_oid": plan["source_tree"]["tree_oid"],
        }
    )


def _component_report_proof(
    plan: Mapping[str, Any], component: str, *, state_sha256: str
) -> dict[str, Any]:
    item = plan["components"][component]
    return {
        "command": item["command"],
        "direct_nodes": plan["direct_nodes"],
        "downstream_nodes": plan["downstream_nodes"],
        "environment_sha256": item["environment"]["sha256"],
        "input_sha256": item["inputs"]["aggregate_sha256"],
        "policy_version": plan["policy_version"],
        "risk_flags": plan["risk_flags"],
        "state_sha256": state_sha256,
    }


def _validate_component_report_proof(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "command",
        "direct_nodes",
        "downstream_nodes",
        "environment_sha256",
        "input_sha256",
        "policy_version",
        "risk_flags",
        "state_sha256",
    }:
        raise VerificationError("verification report component proof is malformed")
    for field in ("environment_sha256", "input_sha256", "state_sha256"):
        if not isinstance(value[field], str) or not SHA256_RE.fullmatch(value[field]):
            raise VerificationError("verification report component proof digest is invalid")
    if not isinstance(value["command"], str) or not value["command"]:
        raise VerificationError("verification report component command is invalid")
    if not isinstance(value["policy_version"], int) or isinstance(value["policy_version"], bool):
        raise VerificationError("verification report component policy version is invalid")
    for field in ("direct_nodes", "downstream_nodes", "risk_flags"):
        if (
            not isinstance(value[field], list)
            or value[field] != sorted(set(value[field]))
            or any(not isinstance(item, str) for item in value[field])
        ):
            raise VerificationError("verification report component graph proof is invalid")


def repository_state(
    repository: str | Path, revision: str, graph: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    graph = dict(graph) if graph is not None else load_graph()
    _manifest, source_tree = git_manifest(repository, revision)
    environment = validate_environment_fingerprint(environment_fingerprint())
    component_environment = {
        component: validate_environment_fingerprint(component_environment_fingerprint(component))
        for component in PLAN_COMPONENTS
    }
    payload = {
        "component_environment": component_environment,
        "component_environment_sha256": {
            component: fingerprint["sha256"]
            for component, fingerprint in component_environment.items()
        },
        "environment": environment,
        "environment_sha256": environment["sha256"],
        "graph_sha256": graph_digest(graph),
        "policy_version": graph["policy_version"],
        "source_manifest_sha256": source_tree["manifest_sha256"],
        "tree_oid": source_tree["tree_oid"],
    }
    return _validate_repository_state(payload | {"verification_state_sha256": sha256_json(payload)})


def _validate_repository_state(value: object) -> dict[str, Any]:
    """Validate the aggregate state retained for scheduled coverage decisions."""
    expected = {
        "component_environment",
        "component_environment_sha256",
        "environment",
        "environment_sha256",
        "graph_sha256",
        "policy_version",
        "source_manifest_sha256",
        "tree_oid",
        "verification_state_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise VerificationError("repository state has an invalid shape")
    try:
        environment = validate_environment_fingerprint(value["environment"])
        component_environment = value["component_environment"]
        if not isinstance(component_environment, dict) or set(component_environment) != set(
            PLAN_COMPONENTS
        ):
            raise VerificationError("repository state component environments are incomplete")
        validated_components = {
            component: validate_environment_fingerprint(component_environment[component])
            for component in PLAN_COMPONENTS
        }
    except (TypeError, ValueError) as exc:
        raise VerificationError("repository state environment fingerprint is invalid") from exc

    if value["environment_sha256"] != environment["sha256"]:
        raise VerificationError("repository state global environment digest does not match")
    component_digests = value["component_environment_sha256"]
    if (
        not isinstance(component_digests, dict)
        or set(component_digests) != set(PLAN_COMPONENTS)
        or component_digests
        != {
            component: fingerprint["sha256"]
            for component, fingerprint in validated_components.items()
        }
    ):
        raise VerificationError("repository state component environment digests do not match")
    for field in ("graph_sha256", "source_manifest_sha256", "verification_state_sha256"):
        if not isinstance(value[field], str) or not SHA256_RE.fullmatch(value[field]):
            raise VerificationError("repository state digest is invalid")
    if not isinstance(value["tree_oid"], str) or not SHA_RE.fullmatch(value["tree_oid"]):
        raise VerificationError("repository state tree identity is invalid")
    if not isinstance(value["policy_version"], int) or isinstance(value["policy_version"], bool):
        raise VerificationError("repository state policy version is invalid")

    identity = dict(value)
    digest = identity.pop("verification_state_sha256")
    if digest != sha256_json(identity):
        raise VerificationError("repository state digest does not match its contents")
    return value


def _scheduled_environment_matches(
    actual: Mapping[str, Any],
    planned: Mapping[str, Any],
    *,
    allow_hosted_runner_drift: bool,
) -> bool:
    """Apply exact state identity by default; opt into hosted-runner drift explicitly."""
    if not allow_hosted_runner_drift:
        return actual == planned
    return environment_matches_plan(
        actual,
        planned,
        allow_hosted_runner_drift=True,
    )


def build_scheduled_state_envelope(
    *,
    plan: Mapping[str, Any],
    report: Mapping[str, Any],
    state: Mapping[str, Any],
    run_id: int,
    run_attempt: int,
    workflow: str = ".github/workflows/scheduled-full-regression.yml",
    allow_hosted_runner_drift: bool = False,
) -> dict[str, Any]:
    plan = validate_plan(dict(plan))
    report = validate_report(dict(report))
    state = _validate_repository_state(dict(state))
    if report["verdict"] != "success" or report["plan_sha256"] != sha256_json(plan):
        raise VerificationError("scheduled state requires a successful plan-bound report")
    planned_component_environment = {
        component: item["environment"] for component, item in plan["components"].items()
    }
    if (
        state["source_manifest_sha256"] != plan["source_tree"]["manifest_sha256"]
        or state["tree_oid"] != plan["source_tree"]["tree_oid"]
        or state["graph_sha256"] != plan["graph_sha256"]
        or state["policy_version"] != plan["policy_version"]
        or not _scheduled_environment_matches(
            state["environment"],
            plan["environment"],
            allow_hosted_runner_drift=allow_hosted_runner_drift,
        )
        or set(state["component_environment"]) != set(planned_component_environment)
        or any(
            not _scheduled_environment_matches(
                state["component_environment"][component],
                planned_environment,
                allow_hosted_runner_drift=allow_hosted_runner_drift,
            )
            for component, planned_environment in planned_component_environment.items()
        )
    ):
        raise VerificationError("scheduled repository state does not match the plan")
    evidence_ids = sorted(
        entry["evidence"]["evidence_id"]
        for bucket in ("rerun", "reused")
        for entry in report["buckets"][bucket]
    )
    envelope = {
        "evidence_ids": evidence_ids,
        "plan_sha256": sha256_json(plan),
        "report_sha256": sha256_json(report),
        "repository": plan["repository"],
        "result": "success",
        "run_attempt": run_attempt,
        "run_id": run_id,
        "schema_version": SCHEDULED_STATE_SCHEMA_VERSION,
        "source_sha": plan["head"],
        "verification_state_sha256": state["verification_state_sha256"],
        "workflow": workflow,
    }
    envelope["envelope_sha256"] = sha256_json(envelope)
    return validate_scheduled_state_envelope(envelope)


def validate_scheduled_state_envelope(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "envelope_sha256",
        "evidence_ids",
        "plan_sha256",
        "report_sha256",
        "repository",
        "result",
        "run_attempt",
        "run_id",
        "schema_version",
        "source_sha",
        "verification_state_sha256",
        "workflow",
    }:
        raise VerificationError("scheduled state envelope has an invalid shape")
    identity = dict(payload)
    digest = identity.pop("envelope_sha256")
    if not isinstance(digest, str) or digest != sha256_json(identity):
        raise VerificationError("scheduled state envelope digest does not match")
    if (
        payload["schema_version"] != SCHEDULED_STATE_SCHEMA_VERSION
        or payload["result"] != "success"
    ):
        raise VerificationError("scheduled state envelope is not a successful supported record")
    for field in ("envelope_sha256", "plan_sha256", "report_sha256", "verification_state_sha256"):
        if not isinstance(payload[field], str) or not SHA256_RE.fullmatch(payload[field]):
            raise VerificationError("scheduled state envelope digest is invalid")
    if not isinstance(payload["source_sha"], str) or not SHA_RE.fullmatch(payload["source_sha"]):
        raise VerificationError("scheduled state source SHA is invalid")
    for field in ("run_id", "run_attempt"):
        value = payload[field]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise VerificationError("scheduled state run identity is invalid")
    if (
        not isinstance(payload["evidence_ids"], list)
        or payload["evidence_ids"] != sorted(set(payload["evidence_ids"]))
        or not payload["evidence_ids"]
        or any(
            not isinstance(item, str) or not SHA256_RE.fullmatch(item)
            for item in payload["evidence_ids"]
        )
    ):
        raise VerificationError("scheduled state component evidence is incomplete")
    if (
        not isinstance(payload["repository"], str)
        or not payload["repository"]
        or payload["workflow"] != ".github/workflows/scheduled-full-regression.yml"
    ):
        raise VerificationError("scheduled state provenance is invalid")
    return payload


def materialize_reused_evidence(
    *,
    plan: Mapping[str, Any],
    evidence_directory: str | Path,
    output_directory: str | Path,
) -> int:
    plan = validate_plan(dict(plan))
    evidence_root = Path(evidence_directory).resolve()
    candidates = load_envelopes(evidence_root)
    output = Path(output_directory)
    copied = 0
    for component, item in plan["components"].items():
        if item["disposition"] != "reused":
            continue
        evidence_id = item["evidence"]["evidence_id"]
        matches = [
            (path, envelope)
            for path, envelope in candidates
            if envelope["evidence_id"] == evidence_id
        ]
        if len(matches) != 1:
            raise VerificationError("reused evidence identity is missing or ambiguous")
        envelope_path, envelope = matches[0]
        destination = output / component
        destination.mkdir(parents=True, exist_ok=True)
        for artifact in envelope["artifacts"]:
            source: Path | None = None
            for root in (evidence_root, envelope_path.parent.resolve()):
                candidate = (root / artifact["path"]).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    continue
                if candidate.is_file() and not candidate.is_symlink():
                    source = candidate
                    break
            if source is None:
                raise VerificationError("reused evidence artifact is missing")
            body = source.read_bytes()
            if (
                len(body) != artifact["size"]
                or hashlib.sha256(body).hexdigest() != artifact["sha256"]
            ):
                raise VerificationError("reused evidence artifact digest does not match")
            target = destination / artifact["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        dump_json(envelope, destination / f"{component}-evidence.json")
        copied += 1
    return copied


def _required_screenshot_captures(
    graph: Mapping[str, Any],
    environment: Mapping[str, Any],
    *,
    impacted_nodes: Sequence[str],
) -> list[dict[str, Any]]:
    impacted = set(impacted_nodes)
    routes = [
        route
        for route in graph["screenshot_contract"]["routes"]
        if impacted.intersection(route["nodes"])
    ]
    # A render rule without a route mapping is ambiguous. Fail closed to every
    # critical route/state rather than silently producing an empty visual gate.
    if not routes:
        routes = graph["screenshot_contract"]["routes"]
    captures = [
        {
            "browser": environment["browser"],
            "height": viewport["height"],
            "route": route["route"],
            "route_state": route["state"],
            "viewport": viewport["name"],
            "width": viewport["width"],
        }
        for route in routes
        for viewport in graph["screenshot_contract"]["viewports"]
    ]
    return sorted(
        captures,
        key=lambda item: (
            item["route"],
            item["route_state"],
            item["viewport"],
            item["width"],
            item["height"],
        ),
    )


def _validate_required_captures(value: object, *, impact: bool) -> None:
    expected = {"browser", "height", "route", "route_state", "viewport", "width"}
    if not isinstance(value, list) or (impact and not value) or (not impact and value):
        raise VerificationError("render screenshot coverage is incomplete")
    identities: set[tuple[object, ...]] = set()
    viewport_names: set[str] = set()
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != expected
            or not isinstance(item["browser"], str)
            or not item["browser"]
            or not isinstance(item["route"], str)
            or not item["route"].startswith("/")
            or not isinstance(item["route_state"], str)
            or not item["route_state"]
            or not isinstance(item["viewport"], str)
            or not item["viewport"]
            or not isinstance(item["width"], int)
            or isinstance(item["width"], bool)
            or item["width"] <= 0
            or not isinstance(item["height"], int)
            or isinstance(item["height"], bool)
            or item["height"] <= 0
        ):
            raise VerificationError("render screenshot identity is invalid")
        identity = tuple(item[key] for key in sorted(expected))
        if identity in identities:
            raise VerificationError("render screenshot identity is duplicated")
        identities.add(identity)
        viewport_names.add(item["viewport"])
    if impact and not {"desktop", "mobile"}.issubset(viewport_names):
        raise VerificationError("render impact requires desktop and mobile screenshots")


def _large_content_decision(
    paths: Sequence[str], graph: Mapping[str, Any], manifest: Sequence[Mapping[str, str]]
) -> dict[str, Any]:
    rules = graph["large_content"]
    extensions = set(rules["structured_extensions"])
    selected_paths = sorted(
        {
            path
            for path in paths
            if PurePosixPath(path).suffix.lower() in extensions
            and matches_any(path, rules["patterns"])
        }
    )
    selected_entries = [entry for entry in manifest if entry["path"] in selected_paths]
    return {
        "impact": bool(selected_paths),
        "paths": selected_paths,
        "sha256": sha256_json(
            {
                "entries": selected_entries,
                "rules": rules,
            }
        ),
    }


def _envelope_matches_plan(
    envelope: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    component: str,
    rerun: bool,
) -> bool:
    component_plan = plan["components"][component]
    shared_match = bool(
        envelope["component"] == component
        and envelope["command"] == component_plan["command"]
        and envelope["input_sha256"] == component_plan["inputs"]["aggregate_sha256"]
        and envelope["input_manifest"] == component_plan["inputs"]["manifest"]
        and environment_matches_plan(
            envelope["environment"],
            component_plan["environment"],
            allow_hosted_runner_drift=(
                rerun and envelope.get("origin", {}).get("kind") == "github_actions"
            ),
        )
        and envelope["validity_class"] == component_plan["validity_class"]
        and envelope["validity_seconds"] == component_plan["validity_seconds"]
        and envelope["policy"]
        == {
            "graph_sha256": plan["graph_sha256"],
            "policy_version": plan["policy_version"],
        }
    )
    if not shared_match or not rerun:
        return shared_match
    return bool(
        envelope["source_tree"] == plan["source_tree"]
        and envelope["selection"]
        == {
            "direct_nodes": plan["direct_nodes"],
            "downstream_nodes": plan["downstream_nodes"],
            "reason": component_plan["reason"],
            "risk_flags": plan["risk_flags"],
        }
    )


def _profile(selection: Mapping[str, Any], impact: Impact) -> tuple[bool, str]:
    if impact.unknown_paths:
        return True, "unknown_impact"
    app_owners = [node for node in impact.owners if node.startswith("app.")]
    if len(app_owners) > 1:
        return True, "cross_application"
    if impact.documentation_only:
        return False, "documentation_only"
    if impact.render_impact:
        return True, "render_impact"
    if set(impact.risk_flags) & FULL_RISK_FLAGS:
        return True, _risk_reason(impact.risk_flags)
    if len(app_owners) == 1 and len(impact.owners) == 1:
        return False, "single_application"
    if selection.get("profile") == "full":
        return True, str(selection.get("reason") or "legacy_full_fallback")
    return True, "unknown_impact"


def _risk_reason(flags: Sequence[str]) -> str:
    priority = (
        "auth_security_privacy",
        "schema_migration",
        "dependency_toolchain",
        "global_fixture",
        "test_infrastructure",
        "deployment_runtime",
        "compatibility_contract",
        "shared_runtime",
    )
    return next(flag for flag in priority if flag in flags)


def _with_status_risks(
    impact: Impact, records: Sequence[ChangeRecord], graph: Mapping[str, Any]
) -> Impact:
    risk_flags = set(impact.risk_flags)
    test_segments = set(graph["risk_rules"]["test_segments"])
    test_basenames = set(graph["risk_rules"]["test_basenames"])
    for record in records:
        for path in record.paths:
            parts = PurePosixPath(path).parts
            if (
                set(parts) & test_segments
                or parts[-1] in test_basenames
                or parts[-1].startswith("test_")
            ):
                risk_flags.add("test_infrastructure")
            if record.status.startswith(("R", "C")) or record.status == "D":
                if set(parts) & test_segments or parts[-1].startswith("test_"):
                    risk_flags.add("test_infrastructure")
    return Impact(
        owners=impact.owners,
        downstream=impact.downstream,
        components=impact.components,
        test_labels=impact.test_labels,
        risk_flags=tuple(sorted(risk_flags)),
        render_impact=impact.render_impact,
        render_reasons=impact.render_reasons,
        documentation_only=impact.documentation_only,
        unknown_paths=impact.unknown_paths,
    )


def _component_requirement(
    component: str,
    *,
    force_full: bool,
    impact: Impact,
    browser_profile: str,
    large_content_impact: bool,
    release_requires_image: bool,
) -> tuple[bool, bool, str]:
    if component in {"selector", "evidence_validation"}:
        return True, True, "always_fresh_control_plane"
    if component == "container" and release_requires_image:
        return True, True, "exact_release_image_required"
    if impact.documentation_only:
        return False, False, "prose_only_non_policy_documentation"
    if component == "compatibility":
        required = "compatibility_contract" in impact.risk_flags
        reason = "compatibility_contract_changed" if required else "outside_impact_closure"
        return required, required, reason
    if component == "content_invariants":
        return (
            large_content_impact,
            large_content_impact,
            "large_content_invariants_required"
            if large_content_impact
            else "outside_large_content_closure",
        )
    if component == "screenshots":
        if impact.render_impact:
            return True, True, "render_inputs_changed"
        return True, False, "render_inputs_unchanged"
    if force_full:
        return True, True, "fresh_full_regression"
    if component in {"quality", "django"}:
        return True, True, "affected_application_closure"
    if component == "playwright":
        return True, browser_profile == "full", "browser_inputs_unchanged"
    if component == "container":
        return True, False, "runtime_inputs_unchanged"
    raise VerificationError("unknown verification component")


def _component_command(
    component: str,
    command: str,
    *,
    browser_profile: str,
    profile: str,
    release_requires_image: bool,
    source_mode: str,
) -> str:
    if component == "selector" and source_mode == "commit":
        return "ci.classifier select and ci.verification plan"
    if component == "django":
        return "make test" if profile == "full" else "make test-ci-focused"
    if component == "playwright":
        return "make test-playwright" if browser_profile == "full" else "make test-playwright-core"
    if component == "container" and release_requires_image:
        return "exact release image verification"
    return command


def _reuse_reference(envelope: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifacts": envelope["artifacts"],
        "command": envelope["command"],
        "counts": envelope["counts"],
        "digest_algorithm": envelope["digest_algorithm"],
        "envelope_sha256": envelope["evidence_id"],
        "evidence_id": envelope["evidence_id"],
        "exit_code": envelope["exit_code"],
        "environment": envelope["environment"],
        "expires_at": envelope["expires_at"],
        "input_sha256": envelope["input_sha256"],
        "origin": envelope["origin"],
        "output": envelope["output"],
        "produced_at": envelope["produced_at"],
        "screenshot": envelope["screenshot"],
        "source_manifest_sha256": envelope["source_tree"]["manifest_sha256"],
        "validity_class": envelope["validity_class"],
    }


def _validate_evidence_reference(value: object) -> None:
    expected = {
        "artifacts",
        "command",
        "counts",
        "digest_algorithm",
        "envelope_sha256",
        "evidence_id",
        "exit_code",
        "environment",
        "expires_at",
        "input_sha256",
        "origin",
        "output",
        "produced_at",
        "screenshot",
        "source_manifest_sha256",
        "validity_class",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise VerificationError("report evidence reference is malformed")
    if value["envelope_sha256"] != value["evidence_id"]:
        raise VerificationError("report envelope digest and evidence id differ")
    if not isinstance(value["counts"], dict) or not value["counts"]:
        raise VerificationError("report evidence counts are missing")
    if not isinstance(value["artifacts"], list) or not value["artifacts"]:
        raise VerificationError("report artifact identities are missing")
    if value["digest_algorithm"] != "sha256":
        raise VerificationError("report evidence digest algorithm is unsupported")
    try:
        validate_environment_fingerprint(value["environment"])
    except ValueError as exc:
        raise VerificationError("report evidence environment is invalid") from exc
    if not isinstance(value["exit_code"], int) or isinstance(value["exit_code"], bool):
        raise VerificationError("report evidence exit status is invalid")


def _validate_component_plan(component: str, value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "command",
        "disposition",
        "environment",
        "evidence",
        "inputs",
        "reason",
        "required",
        "validity_class",
        "validity_seconds",
    }:
        raise VerificationError(f"component {component} has an invalid plan shape")
    try:
        validate_environment_fingerprint(value["environment"])
    except ValueError as exc:
        raise VerificationError(f"component {component} environment is invalid") from exc
    if value["disposition"] not in {"not_applicable", "rerun", "reused"}:
        raise VerificationError(f"component {component} has an unsupported disposition")
    if not isinstance(value["required"], bool):
        raise VerificationError(f"component {component} required flag is invalid")
    if value["required"] and value["disposition"] == "not_applicable":
        raise VerificationError(f"required component {component} cannot be not applicable")
    if value["disposition"] == "reused" and not isinstance(value["evidence"], dict):
        raise VerificationError(f"reused component {component} requires evidence")
    if value["disposition"] != "reused" and value["evidence"] is not None:
        raise VerificationError(f"component {component} has unexpected reuse evidence")
    inputs = value["inputs"]
    if not isinstance(inputs, dict) or set(inputs) != {"aggregate_sha256", "groups", "manifest"}:
        raise VerificationError(f"component {component} inputs are malformed")
    if not isinstance(value["validity_seconds"], int) or isinstance(
        value["validity_seconds"], bool
    ):
        raise VerificationError(f"component {component} validity is malformed")
    if value["command"] not in ALLOWED_COMPONENT_COMMANDS[component]:
        raise VerificationError(f"component {component} command is not allowlisted")
    if (
        value["validity_class"] not in VALIDITY_SECONDS
        or value["validity_seconds"] != VALIDITY_SECONDS[value["validity_class"]]
    ):
        raise VerificationError(f"component {component} validity exceeds policy")


def _origin_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.origin_kind == "github_actions":
        return {
            "artifact_id": args.artifact_id,
            "job_id": args.job_id,
            "kind": "github_actions",
            "ref": args.ref,
            "repository": args.repository_id,
            "run_attempt": args.run_attempt,
            "run_id": args.run_id,
            "workflow": args.workflow,
        }
    return {
        "issue": args.issue,
        "kind": "local",
        "producer_role": args.producer_role,
        "worktree": args.worktree,
    }


def _read_history(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise VerificationError("evidence history must be a list of objects")
    return payload


def _read_screenshot_metadata(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise VerificationError(
            "screenshot capture failed before evidence recording: "
            f"machine output is missing ({path})"
        ) from None
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(
            f"screenshot capture produced unreadable machine output ({path})"
        ) from exc
    if not isinstance(payload, dict):
        raise VerificationError(f"screenshot machine output must be an object ({path})")
    return payload


def _record_machine_output(
    path: str | Path,
    *,
    root: str | Path,
    component: str,
    plan: Mapping[str, Any],
    result: str,
    screenshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return machine_output_claim(
            path,
            root=root,
            component=component,
            plan=plan,
            result=result,
            screenshot=screenshot,
        )
    except FileNotFoundError:
        if component != "screenshots":
            raise
        raise VerificationError(
            "screenshot capture failed before evidence recording: "
            f"machine output is missing ({path})"
        ) from None


def _artifact_diagnostic_path(value: str | Path, *, root: str | Path) -> str:
    path = Path(value)
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def _add_artifact_collection_error(
    errors: list[dict[str, str]],
    value: str | Path,
    error: BaseException,
    *,
    root: str | Path,
) -> None:
    record = {
        "path": _artifact_diagnostic_path(value, root=root),
        "reason": str(error),
    }
    if not any(item["path"] == record["path"] for item in errors):
        errors.append(record)


def _collect_artifact_records(
    paths: Sequence[str | Path],
    *,
    root: str | Path,
    tolerate_errors: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for value in paths:
        try:
            records.extend(artifact_records((value,), root=root))
        except (EvidenceError, OSError) as exc:
            if not tolerate_errors:
                raise
            _add_artifact_collection_error(errors, value, exc, root=root)
    unique_records = {record["path"]: record for record in records}
    return sorted(unique_records.values(), key=lambda item: item["path"]), errors


def _write_artifact_collection_marker(
    *,
    root: str | Path,
    component: str,
    result: str,
    errors: Sequence[dict[str, str]],
) -> Path:
    marker = Path(root) / f"{component}-artifact-collection.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    ordered_errors = list(errors)
    ordered_errors.sort(key=lambda item: (item["path"], item["reason"]))
    dump_json(
        {
            "artifact_collection": {
                "errors": ordered_errors,
                "status": "partial",
            },
            "component": component,
            "result": result,
            "schema_version": 1,
        },
        marker,
    )
    return marker


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command_name", required=True)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--repository", type=Path, default=Path.cwd())
    plan_parser.add_argument("--repository-id", default="DataTalksClub/website")
    plan_parser.add_argument("--base", required=True)
    plan_parser.add_argument("--head", required=True)
    plan_parser.add_argument("--selection", required=True)
    plan_parser.add_argument("--evidence-directory")
    plan_parser.add_argument("--history")
    plan_parser.add_argument("--consumer", choices=("ci", "engineer", "tester"), default="engineer")
    plan_parser.add_argument("--output", required=True)
    plan_parser.add_argument("--summary")
    plan_parser.add_argument("--github-output")
    plan_parser.add_argument("--release-requires-image", action="store_true")
    plan_parser.add_argument("--include-worktree", action="store_true")

    validate_plan_parser = commands.add_parser("validate-plan")
    validate_plan_parser.add_argument("--plan", required=True)
    mode_parser = commands.add_parser("component-mode")
    mode_parser.add_argument("--plan", required=True)
    mode_parser.add_argument("--component", required=True, choices=PLAN_COMPONENTS)
    mode_parser.add_argument("--github-output")

    evidence_parser = commands.add_parser("validate-evidence")
    evidence_parser.add_argument("--evidence", required=True)
    evidence_directory_parser = commands.add_parser("validate-evidence-directory")
    evidence_directory_parser.add_argument("--directory", required=True)
    evidence_directory_parser.add_argument("--plan", required=True)
    evidence_directory_parser.add_argument(
        "--consumer", choices=("ci", "engineer", "tester"), required=True
    )
    materialize_parser = commands.add_parser("materialize-reuse")
    materialize_parser.add_argument("--plan", required=True)
    materialize_parser.add_argument("--evidence-directory", required=True)
    materialize_parser.add_argument("--output-directory", required=True)

    environment_parser = commands.add_parser("environment")
    environment_parser.add_argument("--plan", required=True)
    environment_parser.add_argument("--component", required=True, choices=PLAN_COMPONENTS)
    environment_parser.add_argument("--output", required=True)
    environment_parser.add_argument("--allow-hosted-runner-drift", action="store_true")

    record_parser = commands.add_parser("record")
    record_parser.add_argument("--plan", required=True)
    record_parser.add_argument("--component", required=True, choices=PLAN_COMPONENTS)
    record_parser.add_argument("--result", required=True)
    record_parser.add_argument("--command", required=True)
    record_parser.add_argument("--output", required=True)
    record_parser.add_argument("--artifact", action="append", default=[])
    record_parser.add_argument("--artifact-root", default=".")
    record_parser.add_argument("--machine-output", required=True)
    record_parser.add_argument(
        "--screenshot",
        help="JSON screenshot inspection metadata bound to the machine output",
    )
    record_parser.add_argument("--execution-environment", required=True)
    record_parser.add_argument("--origin-kind", choices=("github_actions", "local"), required=True)
    record_parser.add_argument("--repository-id", default="DataTalksClub/website")
    record_parser.add_argument("--workflow", default=".github/workflows/ci.yml")
    record_parser.add_argument("--ref", default="refs/heads/main")
    record_parser.add_argument("--run-id", type=int, default=1)
    record_parser.add_argument("--run-attempt", type=int, default=1)
    record_parser.add_argument("--job-id", default="")
    record_parser.add_argument("--artifact-id", default="")
    record_parser.add_argument("--issue", type=int)
    record_parser.add_argument(
        "--producer-role", choices=("engineer", "tester"), default="engineer"
    )
    record_parser.add_argument("--worktree", default="local")
    record_parser.add_argument("--allow-hosted-runner-drift", action="store_true")

    report_parser = commands.add_parser("report")
    report_parser.add_argument("--plan", required=True)
    report_parser.add_argument("--result-directory")
    report_parser.add_argument("--phase", choices=("ci", "engineer", "tester"), default="engineer")
    report_parser.add_argument("--output", required=True)
    report_parser.add_argument("--summary")
    report_parser.add_argument("--no-fail-exit", action="store_true")
    validate_report_parser = commands.add_parser("validate-report")
    validate_report_parser.add_argument("--plan", required=True)
    validate_report_parser.add_argument("--report", required=True)
    validate_report_parser.add_argument("--evidence-directory", required=True)
    validate_report_parser.add_argument("--allow-pending", action="store_true")

    state_parser = commands.add_parser("state")
    state_parser.add_argument("--repository", type=Path, default=Path.cwd())
    state_parser.add_argument("--revision", required=True)
    state_parser.add_argument("--output", required=True)
    scheduled_state_parser = commands.add_parser("scheduled-state")
    scheduled_state_parser.add_argument("--plan", required=True)
    scheduled_state_parser.add_argument("--report", required=True)
    scheduled_state_parser.add_argument("--state", required=True)
    scheduled_state_parser.add_argument("--run-id", type=int, required=True)
    scheduled_state_parser.add_argument("--run-attempt", type=int, required=True)
    scheduled_state_parser.add_argument("--allow-hosted-runner-drift", action="store_true")
    scheduled_state_parser.add_argument("--output", required=True)

    args = parser.parse_args()
    if args.command_name == "plan":
        selection = load_selection(args.selection)
        payload = build_plan(
            repository=args.repository,
            repository_id=args.repository_id,
            base=args.base,
            head=args.head,
            selection=selection,
            evidence_directory=args.evidence_directory,
            consumer=args.consumer,
            history=_read_history(args.history),
            release_requires_image=args.release_requires_image,
            include_worktree=args.include_worktree,
        )
        dump_json(payload, args.output)
        if args.summary:
            with Path(args.summary).open("a", encoding="utf-8") as summary:
                summary.write(plan_summary(payload))
        if args.github_output:
            with Path(args.github_output).open("a", encoding="utf-8") as output:
                output.write(f"profile={payload['profile']}\n")
                output.write(f"browser_profile={payload['browser_profile']}\n")
                for component in PLAN_COMPONENTS:
                    output.write(f"{component}_mode={component_mode(payload, component)}\n")
        return
    if args.command_name == "validate-plan":
        load_plan(args.plan)
        return
    if args.command_name == "component-mode":
        mode = component_mode(load_plan(args.plan), args.component)
        print(mode)
        if args.github_output:
            with Path(args.github_output).open("a", encoding="utf-8") as output:
                output.write(f"mode={mode}\n")
        return
    if args.command_name == "validate-evidence":
        validate_envelope(json.loads(Path(args.evidence).read_text(encoding="utf-8")))
        return
    if args.command_name == "validate-evidence-directory":
        validate_evidence_directory(
            directory=args.directory,
            plan=load_plan(args.plan),
            consumer=args.consumer,
        )
        return
    if args.command_name == "materialize-reuse":
        materialize_reused_evidence(
            plan=load_plan(args.plan),
            evidence_directory=args.evidence_directory,
            output_directory=args.output_directory,
        )
        return
    if args.command_name == "environment":
        plan = load_plan(args.plan)
        actual_environment = environment_fingerprint()
        if not environment_matches_plan(
            actual_environment,
            plan["components"][args.component]["environment"],
            allow_hosted_runner_drift=args.allow_hosted_runner_drift,
        ):
            raise VerificationError(
                "executing component environment does not match the authorized plan"
            )
        dump_json(actual_environment, args.output)
        return
    if args.command_name == "record":
        if args.origin_kind == "local" and args.issue is None:
            record_parser.error(
                "--issue is required for --origin-kind local: refusing to attribute "
                "verification evidence to a default issue number"
            )
        plan = load_plan(args.plan)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result_path = output_path.with_name(f"{args.component}-result.json")
        screenshot = None
        if args.screenshot:
            screenshot = _read_screenshot_metadata(args.screenshot)
        collection_errors: list[dict[str, str]] = []
        machine_output_path = Path(args.machine_output)
        try:
            machine_output = _record_machine_output(
                machine_output_path,
                root=args.artifact_root,
                component=args.component,
                plan=plan,
                result=args.result,
                screenshot=screenshot,
            )
        except (EvidenceError, OSError, VerificationError) as exc:
            if args.result == "success":
                raise
            _add_artifact_collection_error(
                collection_errors,
                machine_output_path,
                exc,
                root=args.artifact_root,
            )
            machine_output = None

        expected_artifact_paths = [
            *(Path(item) for item in args.artifact),
            machine_output_path,
            Path(args.execution_environment),
        ]
        collected_artifacts, artifact_errors = _collect_artifact_records(
            expected_artifact_paths,
            root=args.artifact_root,
            tolerate_errors=args.result != "success",
        )
        for error in artifact_errors:
            _add_artifact_collection_error(
                collection_errors,
                error["path"],
                ValueError(error["reason"]),
                root=args.artifact_root,
            )

        marker_path: Path | None = None
        if collection_errors:
            marker_path = _write_artifact_collection_marker(
                root=args.artifact_root,
                component=args.component,
                result=args.result,
                errors=collection_errors,
            )
            if machine_output is None:
                machine_output_path = marker_path
                machine_output = _record_machine_output(
                    machine_output_path,
                    root=args.artifact_root,
                    component=args.component,
                    plan=plan,
                    result=args.result,
                    screenshot=screenshot,
                )
        if machine_output is None:
            raise VerificationError("component machine output could not be recorded")
        result_payload = {
            "command": args.command,
            "component": args.component,
            "counts": machine_output["counts"],
            "output": machine_output["artifact"],
            "recorded_at": isoformat(utc_now()),
            "result": args.result,
        }
        if collection_errors and marker_path is not None:
            result_payload["artifact_collection"] = {
                "errors": sorted(
                    collection_errors, key=lambda item: (item["path"], item["reason"])
                ),
                "marker": _artifact_diagnostic_path(marker_path, root=args.artifact_root),
                "status": "partial",
            }
        dump_json(result_payload, result_path)
        result_records = artifact_records((result_path,), root=args.artifact_root)
        artifacts = collected_artifacts + result_records
        if marker_path is not None:
            artifacts.extend(artifact_records((marker_path,), root=args.artifact_root))
        artifacts = sorted(
            {record["path"]: record for record in artifacts}.values(),
            key=lambda item: item["path"],
        )
        envelope = build_envelope(
            plan=plan,
            component=args.component,
            result=args.result,
            origin=_origin_from_args(args),
            command=args.command,
            execution_environment=json.loads(
                Path(args.execution_environment).read_text(encoding="utf-8")
            ),
            artifacts=artifacts,
            machine_output=machine_output,
            screenshot=screenshot,
            allow_hosted_runner_drift=args.allow_hosted_runner_drift,
        )
        dump_json(envelope, output_path)
        return
    if args.command_name == "report":
        plan = load_plan(args.plan)
        report = create_report(plan=plan, result_directory=args.result_directory, phase=args.phase)
        dump_json(report, args.output)
        if args.summary:
            with Path(args.summary).open("a", encoding="utf-8") as summary:
                summary.write(report_summary(report))
        if report["verdict"] == "failure" and not args.no_fail_exit:
            raise SystemExit(1)
        return
    if args.command_name == "validate-report":
        plan = load_plan(args.plan)
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        validate_report(
            report,
            plan=plan,
            evidence_directory=args.evidence_directory,
            allow_pending=args.allow_pending,
        )
        return
    if args.command_name == "state":
        state = repository_state(args.repository, args.revision)
        dump_json(state, args.output)
        return
    plan = load_plan(args.plan)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    dump_json(
        build_scheduled_state_envelope(
            plan=plan,
            report=report,
            state=state,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            allow_hosted_runner_drift=args.allow_hosted_runner_drift,
        ),
        args.output,
    )


if __name__ == "__main__":
    main()
