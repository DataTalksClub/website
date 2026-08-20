from __future__ import annotations

import ast
import json
from collections import defaultdict
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
SOURCE_EXCLUDED_PARTS = frozenset({"e2e", "migrations", "playwright_tests", "tests", "tests_ci"})


def _is_excluded_source(relative_path: Path) -> bool:
    return any(
        part in SOURCE_EXCLUDED_PARTS or part.startswith(("test_", "tests_"))
        for part in relative_path.parts
    )


def _source_files(package: str) -> tuple[Path, ...]:
    package_root = ROOT / package
    assert package_root.is_dir(), f"verification package is missing: {package}"
    root = ROOT.resolve()
    paths = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root)
        if _is_excluded_source(relative):
            continue
        assert not path.is_symlink(), f"symlinked source is ambiguous: {path}"
        assert path.resolve().is_relative_to(root), f"source escaped repository: {path}"
        paths.append(path)
    return tuple(paths)


def _dynamic_import_root(call: ast.Call, *, path: Path) -> str | None:
    function = call.func
    function_name = (
        function.id if isinstance(function, ast.Name) else getattr(function, "attr", None)
    )
    if function_name not in {"__import__", "import_module"}:
        return None
    if (
        not call.args
        or not isinstance(call.args[0], ast.Constant)
        or not isinstance(call.args[0].value, str)
    ):
        raise AssertionError(f"ambiguous dynamic import at {path}:{call.lineno}")
    return call.args[0].value.split(".", 1)[0]


def _top_level_import_roots(path: Path, *, known_packages: set[str]) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.level == 0 and statement.module:
            imported.add(statement.module.split(".", 1)[0])

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        dynamic_root = _dynamic_import_root(call, path=path)
        if dynamic_root in known_packages:
            imported.add(dynamic_root)
    return tuple(sorted(imported & known_packages))


def _reverse_imports(
    graph: dict[str, object],
) -> tuple[dict[str, tuple[str, ...]], dict[str, dict]]:
    verification_nodes = {
        node["id"].removeprefix("django."): node
        for node in graph["nodes"]
        if node["kind"] == "verification"
    }
    owner_roots = {
        node["id"].removeprefix("app.") for node in graph["nodes"] if node["id"].startswith("app.")
    }
    known_packages = set(verification_nodes)
    reverse: dict[str, set[str]] = defaultdict(set)
    for importer in sorted(verification_nodes):
        for path in _source_files(importer):
            for imported in _top_level_import_roots(path, known_packages=known_packages):
                if imported in owner_roots and imported != importer:
                    reverse[imported].add(importer)
    return (
        {root: tuple(sorted(importers)) for root, importers in sorted(reverse.items())},
        verification_nodes,
    )


def test_graph_is_valid_deterministic_and_preserves_reviewed_closures() -> None:
    graph = load_graph()
    assert graph_digest(graph) == graph_digest(deepcopy(graph))
    assert application_test_labels(graph) == {
        "api": ("api",),
        "studio_courses": ("studio_courses",),
        "content": ("accounts", "content.tests", "content_sync", "core"),
        "courses": (
            "accounts",
            "api",
            "content.tests",
            "core",
            "courses",
            "data",
            "management_api",
            "studio",
            "studio_courses",
        ),
        "data": ("api", "courses", "data", "studio_courses"),
        "jobs": ("events", "jobs"),
        "management_api": ("api", "management_api", "studio"),
        "management_auth": (
            "accounts",
            "api",
            "core",
            "management_api",
            "management_auth",
            "studio",
        ),
        "review_import": ("accounts", "courses", "review_import"),
        "studio": ("accounts", "core", "events", "studio"),
    }


def test_event_qna_studio_reverse_closure_is_explicit() -> None:
    graph = load_graph()
    studio = next(node for node in graph["nodes"] if node["id"] == "app.studio")
    assert "django.events" in studio["downstream"]

    reverse, _ = _reverse_imports(graph)
    assert "events" in reverse["studio"]


def test_top_level_reverse_import_closures_are_complete_and_deterministic() -> None:
    graph = load_graph()
    first, verification_nodes = _reverse_imports(graph)
    second, second_nodes = _reverse_imports(deepcopy(graph))

    assert first == second
    assert verification_nodes == second_nodes

    closures = application_test_labels(graph)
    missing = {
        (changed, importer, label)
        for changed, importers in first.items()
        for importer in importers
        for label in verification_nodes[importer]["test_labels"]
        if label not in closures[changed]
    }
    assert not missing, "reverse imports are outside the changed app closure: " + repr(
        sorted(missing)
    )


def test_graph_schema_policy_version_matches_the_active_graph() -> None:
    graph = load_graph()
    schema = json.loads((ROOT / "ci" / "ownership.schema.json").read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"] == {"const": graph["schema_version"]}
    assert schema["properties"]["policy_version"] == {"const": graph["policy_version"]}


def test_graph_owns_root_configuration_classification_rules() -> None:
    graph = load_graph()
    rules = graph["risk_rules"]
    assert "." in rules["configuration_hidden_prefixes"]
    assert "requirements" in rules["configuration_prefixes"]
    assert ".toml" in rules["configuration_suffixes"]

    incomplete = deepcopy(graph)
    del incomplete["risk_rules"]["configuration_suffixes"]
    with pytest.raises(OwnershipGraphError):
        validate_graph(incomplete)


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
        "management_api",
        "studio",
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


def test_legacy_cadmin_and_shared_test_support_roots_are_explicitly_owned() -> None:
    cadmin = impact_for_paths(("cadmin/legacy_urls.py",))
    assert cadmin.owners == ("surface.cadmin",)
    assert cadmin.risk_flags == ("compatibility_contract",)
    assert not cadmin.unknown_paths

    test_support = impact_for_paths(("test_support/factories/context.py",))
    assert test_support.owners == ("surface.test_support",)
    assert test_support.risk_flags == ("test_infrastructure",)
    assert not test_support.unknown_paths


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
