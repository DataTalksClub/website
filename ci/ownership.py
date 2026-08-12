from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

GRAPH_PATH = Path(__file__).with_name("ownership.json")
GRAPH_SCHEMA_VERSION = 2
NODE_KINDS = frozenset({"owner", "verification"})
RENDER_FLAGS = frozenset({"browser_harness", "data_shape", "navigation", "template"})
ENVIRONMENT_DIMENSIONS = frozenset(
    {
        "allowlisted_config",
        "architecture",
        "browser",
        "database",
        "django",
        "operating_system",
        "playwright",
        "python",
        "runner_image",
        "uv",
    }
)
RISK_FLAGS = frozenset(
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


class OwnershipGraphError(ValueError):
    """The code-owned impact graph is malformed or cannot own a path safely."""


@dataclass(frozen=True)
class Impact:
    owners: tuple[str, ...]
    downstream: tuple[str, ...]
    components: tuple[str, ...]
    test_labels: tuple[str, ...]
    risk_flags: tuple[str, ...]
    render_impact: bool
    render_reasons: tuple[str, ...]
    documentation_only: bool
    unknown_paths: tuple[str, ...]


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_graph(path: str | Path = GRAPH_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_graph(payload)


def validate_graph(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise OwnershipGraphError("ownership graph must be an object")
    expected = {
        "components",
        "large_content",
        "nodes",
        "policy_version",
        "render_rules",
        "risk_rules",
        "schema_version",
        "screenshot_contract",
        "validity_classes",
    }
    if set(payload) != expected:
        raise OwnershipGraphError("ownership graph has unexpected or missing fields")
    if payload["schema_version"] != GRAPH_SCHEMA_VERSION:
        raise OwnershipGraphError("unsupported ownership graph schema")
    if not isinstance(payload["policy_version"], int) or isinstance(
        payload["policy_version"], bool
    ):
        raise OwnershipGraphError("policy_version must be an integer")

    components = payload["components"]
    if not isinstance(components, dict) or not components:
        raise OwnershipGraphError("components must be a non-empty object")
    for component_id, component in components.items():
        if not _identifier(component_id) or not isinstance(component, dict):
            raise OwnershipGraphError("component identifiers must be stable strings")
        if set(component) != {
            "command",
            "environment_dimensions",
            "relevant_patterns",
            "validity_class",
        }:
            raise OwnershipGraphError(f"component {component_id} has an invalid shape")
        if not isinstance(component["command"], str) or not component["command"]:
            raise OwnershipGraphError(f"component {component_id} has no command")
        patterns = _string_list(
            component["relevant_patterns"], f"component {component_id} patterns"
        )
        if not patterns:
            raise OwnershipGraphError(f"component {component_id} has an empty input set")
        dimensions = _string_list(
            component["environment_dimensions"],
            f"component {component_id} environment dimensions",
        )
        if not dimensions or any(item not in ENVIRONMENT_DIMENSIONS for item in dimensions):
            raise OwnershipGraphError(
                f"component {component_id} has invalid environment dimensions"
            )

    validity = payload["validity_classes"]
    if not isinstance(validity, dict) or not validity:
        raise OwnershipGraphError("validity classes must be a non-empty object")
    for name, seconds in validity.items():
        if (
            not _identifier(name)
            or not isinstance(seconds, int)
            or isinstance(seconds, bool)
            or seconds <= 0
        ):
            raise OwnershipGraphError("validity classes require positive integer seconds")
    for component_id, component in components.items():
        if component["validity_class"] not in validity:
            raise OwnershipGraphError(f"component {component_id} uses an unknown validity class")

    nodes = payload["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise OwnershipGraphError("nodes must be a non-empty list")
    by_id: dict[str, dict[str, Any]] = {}
    owner_prefixes: dict[str, str] = {}
    owner_exact: dict[str, str] = {}
    expected_node_keys = {
        "components",
        "downstream",
        "environment_dimensions",
        "exact",
        "id",
        "kind",
        "prefixes",
        "render_flags",
        "risk_flags",
        "validity_class",
    }
    for raw in nodes:
        if not isinstance(raw, dict):
            raise OwnershipGraphError("every node must be an object")
        allowed_keys = expected_node_keys | {"test_labels"}
        if not expected_node_keys.issubset(raw) or set(raw) - allowed_keys:
            raise OwnershipGraphError("node has unexpected or missing fields")
        node_id = raw["id"]
        if not _identifier(node_id) or node_id in by_id:
            raise OwnershipGraphError("node identifiers must be unique stable strings")
        if raw["kind"] not in NODE_KINDS:
            raise OwnershipGraphError(f"node {node_id} has an unsupported kind")
        prefixes = _string_list(raw["prefixes"], f"node {node_id} prefixes")
        exact = _string_list(raw["exact"], f"node {node_id} exact paths")
        downstream = _string_list(raw["downstream"], f"node {node_id} downstream")
        node_components = _string_list(raw["components"], f"node {node_id} components")
        if not node_components or any(item not in components for item in node_components):
            raise OwnershipGraphError(f"node {node_id} has an empty or unknown component closure")
        dimensions = _string_list(
            raw["environment_dimensions"], f"node {node_id} environment dimensions"
        )
        if not dimensions or any(item not in ENVIRONMENT_DIMENSIONS for item in dimensions):
            raise OwnershipGraphError(f"node {node_id} has invalid environment dimensions")
        if raw["validity_class"] not in validity:
            raise OwnershipGraphError(f"node {node_id} has an unknown validity class")
        risk_flags = _string_list(raw["risk_flags"], f"node {node_id} risk flags")
        render_flags = _string_list(raw["render_flags"], f"node {node_id} render flags")
        if any(flag not in RISK_FLAGS for flag in risk_flags):
            raise OwnershipGraphError(f"node {node_id} has an unknown risk flag")
        if any(flag not in RENDER_FLAGS for flag in render_flags):
            raise OwnershipGraphError(f"node {node_id} has an unknown render flag")
        labels = _string_list(raw.get("test_labels", []), f"node {node_id} test labels")
        if raw["kind"] == "verification":
            if prefixes or exact or downstream or not labels:
                raise OwnershipGraphError("verification nodes are virtual leaves with labels")
        elif labels or (not prefixes and not exact):
            raise OwnershipGraphError("owner nodes need paths and cannot carry direct labels")
        for prefix in prefixes:
            if not prefix.endswith("/") or not _valid_path(prefix.removesuffix("/")):
                raise OwnershipGraphError(f"node {node_id} has an invalid prefix")
            if prefix in owner_prefixes:
                raise OwnershipGraphError("ambiguous duplicate owner prefix")
            owner_prefixes[prefix] = node_id
        for path in exact:
            if not _valid_path(path) or path in owner_exact:
                raise OwnershipGraphError("ambiguous duplicate exact ownership")
            owner_exact[path] = node_id
        by_id[node_id] = raw

    prefixes = sorted(owner_prefixes)
    for index, prefix in enumerate(prefixes):
        if any(other.startswith(prefix) for other in prefixes[index + 1 :]):
            raise OwnershipGraphError("nested owner prefixes are ambiguous")
    for path, exact_owner in owner_exact.items():
        if any(
            path.startswith(prefix) and owner_prefixes[prefix] != exact_owner for prefix in prefixes
        ):
            raise OwnershipGraphError("exact path overlaps another owner prefix")
    for node_id, node in by_id.items():
        for downstream in node["downstream"]:
            if downstream not in by_id:
                raise OwnershipGraphError(f"node {node_id} has a dangling downstream edge")
    _reject_cycles(by_id)
    for node_id in by_id:
        if node_id.startswith("app."):
            labels = [
                label
                for target in _downstream(node_id, by_id)
                for label in by_id[target].get("test_labels", [])
            ]
            if not labels:
                raise OwnershipGraphError(
                    f"application node {node_id} has an empty verification closure"
                )

    render_rules = payload["render_rules"]
    risk_rules = payload["risk_rules"]
    if not isinstance(render_rules, dict) or set(render_rules) != {
        "basenames",
        "extensions",
        "segments",
    }:
        raise OwnershipGraphError("render rules have an invalid shape")
    if not isinstance(risk_rules, dict) or set(risk_rules) != {
        "global_fixture_segments",
        "migration_segments",
        "policy_doc_exact",
        "policy_doc_prefixes",
        "test_basenames",
        "test_segments",
    }:
        raise OwnershipGraphError("risk rules have an invalid shape")
    for key, values in (*render_rules.items(), *risk_rules.items()):
        _string_list(values, f"rule {key}")
    large_content = payload["large_content"]
    if not isinstance(large_content, dict) or set(large_content) != {
        "identity_fields",
        "metadata_fields",
        "patterns",
        "structured_extensions",
        "url_fields",
    }:
        raise OwnershipGraphError("large-content rules have an invalid shape")
    for key, values in large_content.items():
        if not _string_list(values, f"large-content rule {key}"):
            raise OwnershipGraphError(f"large-content rule {key} cannot be empty")
    screenshot = payload["screenshot_contract"]
    if not isinstance(screenshot, dict) or set(screenshot) != {"routes", "viewports"}:
        raise OwnershipGraphError("screenshot contract has an invalid shape")
    routes = screenshot["routes"]
    viewports = screenshot["viewports"]
    if (
        not isinstance(routes, list)
        or not routes
        or any(
            not isinstance(item, dict)
            or set(item) != {"nodes", "route", "state"}
            or not isinstance(item["nodes"], list)
            or not item["nodes"]
            or item["nodes"] != sorted(set(item["nodes"]))
            or any(node not in by_id for node in item["nodes"])
            or not isinstance(item["route"], str)
            or not item["route"].startswith("/")
            or not isinstance(item["state"], str)
            or not item["state"]
            for item in routes
        )
        or len({(item["route"], item["state"]) for item in routes}) != len(routes)
    ):
        raise OwnershipGraphError("screenshot routes are invalid or duplicated")
    if (
        not isinstance(viewports, list)
        or len(viewports) < 2
        or any(
            not isinstance(item, dict)
            or set(item) != {"height", "name", "width"}
            or not isinstance(item["name"], str)
            or not item["name"]
            or not isinstance(item["width"], int)
            or isinstance(item["width"], bool)
            or item["width"] <= 0
            or not isinstance(item["height"], int)
            or isinstance(item["height"], bool)
            or item["height"] <= 0
            for item in viewports
        )
        or len({item["name"] for item in viewports}) != len(viewports)
        or not {"desktop", "mobile"}.issubset({item["name"] for item in viewports})
    ):
        raise OwnershipGraphError("screenshot viewports must include unique desktop and mobile")
    return payload


def graph_digest(graph: Mapping[str, Any]) -> str:
    validate_graph(graph)
    return sha256_json(graph)


def application_test_labels(graph: Mapping[str, Any] | None = None) -> dict[str, tuple[str, ...]]:
    graph = validate_graph(dict(graph)) if graph is not None else load_graph()
    by_id = {node["id"]: node for node in graph["nodes"]}
    result: dict[str, tuple[str, ...]] = {}
    for node in graph["nodes"]:
        if node["id"].startswith("app."):
            labels = sorted(
                label
                for downstream in _downstream(node["id"], by_id)
                for label in by_id[downstream].get("test_labels", [])
            )
            result[node["id"].removeprefix("app.")] = tuple(labels)
    return dict(sorted(result.items()))


def impact_for_paths(paths: Iterable[str], graph: Mapping[str, Any] | None = None) -> Impact:
    graph = validate_graph(dict(graph)) if graph is not None else load_graph()
    path_values = tuple(paths)
    by_id = {node["id"]: node for node in graph["nodes"]}
    owner_nodes = [node for node in graph["nodes"] if node["kind"] == "owner"]
    owners: set[str] = set()
    direct_components: set[str] = set()
    unknown: set[str] = set()
    risk_flags: set[str] = set()
    render_reasons: set[str] = set()
    documentation_only = bool(path_values)

    for path in path_values:
        if not _valid_path(path):
            unknown.add(path)
            documentation_only = False
            continue
        matches = [node for node in owner_nodes if _owns(node, path)]
        if len(matches) != 1:
            unknown.add(path)
            documentation_only = False
            continue
        owner = matches[0]
        owners.add(owner["id"])
        direct_components.update(owner["components"])
        risk_flags.update(owner["risk_flags"])
        render_reasons.update(owner["render_flags"])
        if owner["id"] != "surface.documentation":
            documentation_only = False
        parts = PurePosixPath(path).parts
        name = parts[-1]
        rules = graph["risk_rules"]
        if set(parts) & set(rules["migration_segments"]):
            risk_flags.add("schema_migration")
        if set(parts) & set(rules["global_fixture_segments"]):
            risk_flags.add("global_fixture")
        if name in rules["test_basenames"] or set(parts) & set(rules["test_segments"]):
            risk_flags.add("test_infrastructure")
        if path in rules["policy_doc_exact"] or any(
            path.startswith(prefix) for prefix in rules["policy_doc_prefixes"]
        ):
            risk_flags.add("test_infrastructure")
            documentation_only = False
        render = graph["render_rules"]
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in render["extensions"]:
            render_reasons.add(f"extension:{suffix}")
        if name in render["basenames"]:
            render_reasons.add(f"basename:{name}")
        for segment in sorted(set(parts) & set(render["segments"])):
            render_reasons.add(f"segment:{segment}")

    downstream = sorted(
        target for owner in owners for target in _downstream(owner, by_id) if target != owner
    )
    test_labels = sorted(
        label for node_id in downstream for label in by_id[node_id].get("test_labels", [])
    )
    return Impact(
        owners=tuple(sorted(owners)),
        downstream=tuple(sorted(set(downstream))),
        components=tuple(sorted(direct_components)),
        test_labels=tuple(sorted(set(test_labels))),
        risk_flags=tuple(sorted(risk_flags)),
        render_impact=bool(render_reasons),
        render_reasons=tuple(sorted(render_reasons)),
        documentation_only=documentation_only and not unknown,
        unknown_paths=tuple(sorted(unknown)),
    )


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(_match_pattern(path, pattern) for pattern in patterns)


def _match_pattern(path: str, pattern: str) -> bool:
    if pattern.endswith("/**") and not any(
        character in pattern.removesuffix("/**") for character in "*?["
    ):
        prefix = pattern.removesuffix("**")
        return path.startswith(prefix)
    if pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern.removeprefix("**/")):
        return True
    return fnmatch.fnmatchcase(path, pattern) or PurePosixPath(path).match(pattern)


def _owns(node: Mapping[str, Any], path: str) -> bool:
    return path in node["exact"] or any(path.startswith(prefix) for prefix in node["prefixes"])


def _downstream(node_id: str, by_id: Mapping[str, Mapping[str, Any]]) -> set[str]:
    found: set[str] = set()
    pending = list(by_id[node_id]["downstream"])
    while pending:
        candidate = pending.pop()
        if candidate in found:
            continue
        found.add(candidate)
        pending.extend(by_id[candidate]["downstream"])
    return found


def _reject_cycles(by_id: Mapping[str, Mapping[str, Any]]) -> None:
    active: set[str] = set()
    complete: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in active:
            raise OwnershipGraphError("ownership graph contains an unreviewed cycle")
        if node_id in complete:
            return
        active.add(node_id)
        for downstream in by_id[node_id]["downstream"]:
            visit(downstream)
        active.remove(node_id)
        complete.add(node_id)

    for node_id in sorted(by_id):
        visit(node_id)


def _identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(character.isalnum() or character in {"_", "-", "."} for character in value)
    )


def _string_list(value: object, description: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise OwnershipGraphError(f"{description} must contain unique strings")
    return value


def _valid_path(path: str) -> bool:
    parts = path.split("/")
    return bool(
        path
        and not path.startswith("/")
        and "\x00" not in path
        and all(part not in {"", ".", ".."} for part in parts)
    )
