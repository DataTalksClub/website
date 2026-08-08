#!/usr/bin/env python3
"""Reject unclassified uses of the former development-environment name."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

FORMER_NAME = "sand" + "box"
FORMER_NAME_RE = re.compile(FORMER_NAME, re.IGNORECASE)
DEFAULT_POLICY = Path("_docs/compatibility/development-terminology-allowlist.json")
ALLOWED_CLASSES = {
    "captured_compatibility",
    "compatibility_note",
    "frozen_historical",
    "legacy_contract_test",
    "legacy_link_notice",
    "legacy_path",
    "legacy_physical_boundary",
    "legacy_physical_identifier",
    "legacy_schema_reader",
}


class TerminologyPolicyError(ValueError):
    pass


def _tracked_paths(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(
        root / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item and (root / item.decode("utf-8")).is_file()
    )


def _load_text(path: Path) -> str | None:
    raw = path.read_bytes()
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _validate_entry(entry: dict[str, Any], *, kind: str) -> None:
    required = {"path", "class", "reason", "follow_up", "expected_count"}
    if not required.issubset(entry):
        raise TerminologyPolicyError(f"{kind} policy entry lacks required fields: {entry!r}")
    if entry["class"] not in ALLOWED_CLASSES:
        raise TerminologyPolicyError(f"unsupported terminology class: {entry['class']!r}")
    if not isinstance(entry["reason"], str) or not entry["reason"].strip():
        raise TerminologyPolicyError("every terminology allowance requires a reason")
    if not isinstance(entry["follow_up"], str) or not entry["follow_up"].strip():
        raise TerminologyPolicyError("every terminology allowance requires a follow-up")
    if type(entry["expected_count"]) is not int or entry["expected_count"] < 1:
        raise TerminologyPolicyError("every terminology allowance requires a positive count")


def check(root: Path, policy_path: Path) -> list[str]:
    policy_absolute = root / policy_path
    policy = json.loads(policy_absolute.read_text())
    if policy.get("version") != 1:
        raise TerminologyPolicyError("terminology policy version differs")
    if not policy.get("self_reason"):
        raise TerminologyPolicyError("terminology policy must explain its self-exclusion")

    whole_entries: dict[str, dict[str, Any]] = {}
    for entry in policy.get("whole_files", []):
        _validate_entry(entry, kind="whole-file")
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            raise TerminologyPolicyError("whole-file allowances require an exact SHA-256")
        if entry["path"] in whole_entries:
            raise TerminologyPolicyError(f"duplicate whole-file allowance: {entry['path']}")
        whole_entries[entry["path"]] = entry

    rules_by_path: dict[str, list[dict[str, Any]]] = {}
    for entry in policy.get("rules", []):
        _validate_entry(entry, kind="rule")
        if not isinstance(entry.get("pattern"), str) or not entry["pattern"]:
            raise TerminologyPolicyError("rule allowances require a regex pattern")
        re.compile(entry["pattern"], re.IGNORECASE)
        rules_by_path.setdefault(entry["path"], []).append(entry)

    path_entries: dict[str, dict[str, Any]] = {}
    for entry in policy.get("legacy_paths", []):
        _validate_entry(entry, kind="legacy-path")
        path_entries[entry["path"]] = entry

    errors: list[str] = []
    seen_paths: set[str] = set()
    seen_whole: set[str] = set()
    seen_rules: set[tuple[str, str]] = set()
    policy_relative = policy_path.as_posix()
    for absolute in _tracked_paths(root):
        relative = absolute.relative_to(root).as_posix()
        path_matches = tuple(FORMER_NAME_RE.finditer(relative))
        if path_matches:
            entry = path_entries.get(relative)
            if entry is None:
                errors.append(f"unclassified legacy term in path: {relative}")
            elif len(path_matches) != entry["expected_count"]:
                errors.append(f"legacy path count differs for {relative}")
            else:
                seen_paths.add(relative)

        text = _load_text(absolute)
        if text is None or relative == policy_relative:
            continue
        occurrences = tuple(FORMER_NAME_RE.finditer(text))
        if not occurrences:
            continue
        whole = whole_entries.get(relative)
        if whole is not None:
            digest = hashlib.sha256(absolute.read_bytes()).hexdigest()
            if digest != whole["sha256"]:
                errors.append(f"frozen/whole-file digest differs for {relative}")
            if len(occurrences) != whole["expected_count"]:
                errors.append(f"whole-file term count differs for {relative}")
            seen_whole.add(relative)
            continue

        coverage: set[int] = set()
        for rule in rules_by_path.get(relative, []):
            key = (relative, rule["pattern"])
            matches = tuple(re.finditer(rule["pattern"], text, re.IGNORECASE))
            if len(matches) != rule["expected_count"]:
                errors.append(
                    f"rule count differs for {relative}: {rule['pattern']!r} "
                    f"expected {rule['expected_count']}, found {len(matches)}"
                )
            else:
                seen_rules.add(key)
            for match in matches:
                if FORMER_NAME_RE.search(match.group()) is None:
                    errors.append(f"rule does not cover the former name in {relative}: {rule!r}")
                coverage.update(
                    occurrence.start()
                    for occurrence in occurrences
                    if match.start() <= occurrence.start() and occurrence.end() <= match.end()
                )
        for occurrence in occurrences:
            if occurrence.start() in coverage:
                continue
            line = text.count("\n", 0, occurrence.start()) + 1
            column = occurrence.start() - text.rfind("\n", 0, occurrence.start())
            errors.append(f"unclassified legacy term: {relative}:{line}:{column}")

    for relative in sorted(set(whole_entries) - seen_whole):
        errors.append(f"stale whole-file allowance: {relative}")
    for relative, rules in rules_by_path.items():
        for rule in rules:
            key = (relative, rule["pattern"])
            if key not in seen_rules:
                errors.append(f"stale rule allowance: {relative}: {rule['pattern']!r}")
    for relative in sorted(set(path_entries) - seen_paths):
        errors.append(f"stale legacy-path allowance: {relative}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Check development terminology inventory")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    arguments = parser.parse_args()
    try:
        errors = check(arguments.root.resolve(), arguments.policy)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Terminology check failed safely: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(1)
    print("Development terminology inventory is classified.")


if __name__ == "__main__":
    main()
