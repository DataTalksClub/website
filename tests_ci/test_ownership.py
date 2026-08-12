from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from ci.ownership import (
    OwnershipGraphError,
    application_test_labels,
    graph_digest,
    impact_for_paths,
    load_graph,
    matches_any,
    validate_graph,
)

ROOT = Path(__file__).resolve().parents[1]


def test_graph_is_valid_deterministic_and_preserves_reviewed_closures() -> None:
    graph = load_graph()
    assert graph_digest(graph) == graph_digest(deepcopy(graph))
    assert application_test_labels(graph) == {
        "api": ("api",),
        "studio_courses": ("studio_courses",),
        "content": ("accounts", "content.tests", "core"),
        "courses": (
            "accounts",
            "api",
            "content.tests",
            "core",
            "courses",
            "data",
            "studio_courses",
        ),
        "data": ("api", "courses", "data", "studio_courses"),
        "jobs": ("jobs",),
        "management_api": ("api", "management_api"),
        "management_auth": ("api", "core", "management_api", "management_auth"),
        "review_import": ("accounts", "review_import"),
        "studio": ("accounts", "core", "studio"),
    }


def test_graph_schema_policy_version_matches_the_active_graph() -> None:
    graph = load_graph()
    schema = json.loads((ROOT / "ci" / "ownership.schema.json").read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"] == {"const": graph["schema_version"]}
    assert schema["properties"]["policy_version"] == {"const": graph["policy_version"]}


@pytest.mark.parametrize(
    "mutation", ["duplicate", "ambiguous", "dangling", "cycle", "unknown_field"]
)
def test_invalid_graph_shapes_fail_closed(mutation: str) -> None:
    graph = deepcopy(load_graph())
    if mutation == "duplicate":
        graph["nodes"].append(deepcopy(graph["nodes"][0]))
    elif mutation == "ambiguous":
        graph["nodes"][3]["exact"].append("api/special.py")
    elif mutation == "dangling":
        graph["nodes"][0]["downstream"].append("missing.node")
    elif mutation == "cycle":
        graph["nodes"][0]["downstream"].append("app.studio_courses")
        graph["nodes"][1]["downstream"].append("app.api")
    else:
        graph["unexpected"] = True
    with pytest.raises(OwnershipGraphError):
        validate_graph(graph)


def test_impact_resolves_transitive_test_nodes_and_hostile_filenames() -> None:
    impact = impact_for_paths(("courses/service.py", "courses/- strange\tline\n.py"))
    assert impact.owners == ("app.courses",)
    assert impact.test_labels == (
        "accounts",
        "api",
        "content.tests",
        "core",
        "courses",
        "data",
        "studio_courses",
    )
    assert not impact.unknown_paths


def test_shared_render_documentation_and_unknown_impacts_are_explicit() -> None:
    shared = impact_for_paths(("accounts/services.py",))
    assert set(shared.risk_flags) == {"auth_security_privacy", "shared_runtime"}

    rendered = impact_for_paths(("api/templates/api/detail.html", "api/views.py"))
    assert rendered.render_impact
    assert "extension:.html" in rendered.render_reasons
    assert "basename:views.py" in rendered.render_reasons

    docs = impact_for_paths(("_docs/notes.md", "README.md"))
    assert docs.documentation_only
    policy = impact_for_paths(("_docs/PROCESS.md",))
    assert not policy.documentation_only
    assert "test_infrastructure" in policy.risk_flags

    unknown = impact_for_paths(("new_package/module.py", "../escape"))
    assert unknown.unknown_paths == ("../escape", "new_package/module.py")


def test_recursive_component_patterns_match_nested_fixtures_and_templates() -> None:
    assert matches_any("api/fixtures/example.json", ("**/fixtures/**",))
    assert matches_any("courses/templates/courses/detail.html", ("**/templates/**",))


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_app_closure",
        "unknown_render_flag",
        "empty_component_inputs",
        "empty_direct_components",
        "empty_environment_dimensions",
        "unknown_screenshot_route_node",
    ],
)
def test_incomplete_or_unknown_graph_policy_fails_closed(mutation: str) -> None:
    graph = deepcopy(load_graph())
    if mutation == "empty_app_closure":
        graph["nodes"][0]["downstream"] = []
    elif mutation == "unknown_render_flag":
        graph["nodes"][0]["render_flags"] = ["unknown_render_flag"]
    elif mutation == "empty_component_inputs":
        graph["components"]["django"]["relevant_patterns"] = []
    elif mutation == "empty_direct_components":
        graph["nodes"][0]["components"] = []
    elif mutation == "empty_environment_dimensions":
        graph["nodes"][0]["environment_dimensions"] = []
    else:
        graph["screenshot_contract"]["routes"][0]["nodes"] = ["missing.node"]
    with pytest.raises(OwnershipGraphError):
        validate_graph(graph)
