from __future__ import annotations

import configparser
import os
import re
import shlex
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest

from test_support.root_pytest_guard import UNIFIED_COPIED_E2E_DENIAL
from test_support.safety import SAFETY_MARKERS

ROOT = Path(__file__).resolve().parents[2]
ROOT_CONFIG = ROOT / "pyproject.toml"
COPIED_CONFIG = ROOT / "e2e" / "pytest.ini"
MAKEFILE = ROOT / "Makefile"

COPIED_SCENARIO_MARKERS = frozenset(
    {
        "dashboards",
        "email",
        "enrollment",
        "homework",
        "project",
        "provisioning",
        "smoke",
        "teardown",
    }
)
EXISTING_ROOT_MARKERS = {
    "accessibility": "deterministic WCAG A/AA route and state coverage",
    "core": "bounded release-critical local Playwright coverage",
    "full": "additional deterministic local Playwright coverage",
    "live_email": "explicit controlled-recipient live email smoke",
    "live_provider": "explicit provider/webhook integration smoke",
    "remote_mutation": "explicit isolated-development synthetic mutation",
    "remote_readonly": "explicit deployed read-only coverage",
}
EXPECTED_REMOTE_READONLY_NODES = {
    "playwright_tests/test_deployed_smoke.py::"
    "test_deployed_public_and_studio_html_are_exact_and_read_only[chromium-viewport0]",
    "playwright_tests/test_deployed_smoke.py::"
    "test_deployed_public_and_studio_html_are_exact_and_read_only[chromium-viewport1]",
    "playwright_tests/test_deployed_smoke.py::"
    "test_deployed_health_and_anonymous_admin_api_contracts[chromium]",
}
AUTHORITY_ENVIRONMENT = frozenset(
    {
        "DTC_EXPECTED_IMAGE_DIGEST",
        "DTC_EXPECTED_SOURCE_SHA",
        "DTC_EXPECTED_VERSION",
        "DTC_SCREENSHOT_DIR",
        "DTC_TEST_BASE_URL",
        "DTC_TEST_REMOTE_NAMESPACE",
        "DTC_TEST_SAFETY_COMMAND",
        "DTC_TEST_SMOKE_RECIPIENT_REFERENCE",
        "DTC_TEST_TARGET_CLASS",
        "E2E_BASE_URL",
    }
)
COPIED_DIRECT_NODE = "e2e/tests/test_00_availability.py::test_admin_login_page_loads"
_SAFE_MARKER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

MarkerEntry = tuple[str, str]


class MarkerRegistryError(AssertionError):
    """The copied and unified marker registries no longer agree."""


def _parse_marker_lines(lines: Iterable[str]) -> tuple[MarkerEntry, ...]:
    entries: list[MarkerEntry] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        name, separator, description = line.partition(":")
        name = name.strip()
        description = description.strip()
        if not separator or not _SAFE_MARKER_NAME.fullmatch(name) or not description:
            raise MarkerRegistryError("marker registry contains a malformed entry")
        entries.append((name, description))
    return tuple(entries)


def _copied_registry(path: Path = COPIED_CONFIG) -> tuple[MarkerEntry, ...]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(path.read_text(encoding="utf-8"))
    return _parse_marker_lines(parser.get("pytest", "markers").splitlines())


def _root_pytest_options(path: Path = ROOT_CONFIG) -> dict[str, object]:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    return document["tool"]["pytest"]["ini_options"]


def _root_registry(path: Path = ROOT_CONFIG) -> tuple[MarkerEntry, ...]:
    options = _root_pytest_options(path)
    markers = options["markers"]
    if not isinstance(markers, list) or not all(isinstance(item, str) for item in markers):
        raise MarkerRegistryError("unified marker registry is not a string list")
    return _parse_marker_lines(markers)


def _marker_map(entries: Sequence[MarkerEntry], *, registry: str) -> dict[str, str]:
    counts = Counter(name for name, _description in entries)
    duplicates = sorted(name for name, count in counts.items() if count != 1)
    if duplicates:
        raise MarkerRegistryError(f"{registry} registry duplicates: {_bounded_names(duplicates)}")
    return dict(entries)


def _validate_registry_parity(
    copied_entries: Sequence[MarkerEntry],
    root_entries: Sequence[MarkerEntry],
) -> None:
    copied = _marker_map(copied_entries, registry="copied")
    root = _marker_map(root_entries, registry="unified")
    missing = sorted(copied.keys() - root.keys())
    if missing:
        raise MarkerRegistryError(f"unified registry is missing: {_bounded_names(missing)}")
    conflicts = sorted(name for name, description in copied.items() if root[name] != description)
    if conflicts:
        raise MarkerRegistryError(
            f"unified registry descriptions differ: {_bounded_names(conflicts)}"
        )
    if copied.keys() != COPIED_SCENARIO_MARKERS:
        raise MarkerRegistryError("copied scenario marker set changed")


def _bounded_names(names: Sequence[str]) -> str:
    safe = [name if _SAFE_MARKER_NAME.fullmatch(name) else "<invalid>" for name in names[:12]]
    return ",".join(safe)


def _make_recipe(target: str) -> str:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{target}:")
    recipe: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line[0].isspace():
            break
        recipe.append(line.strip())
    return " ".join(recipe)


def _collection_environment(marker: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in AUTHORITY_ENVIRONMENT:
        environment.pop(name, None)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("DTC_TEST_OWNER_TOKEN", None)
    safe_marker = re.sub(r"[^a-z0-9]+", "-", marker.casefold()).strip("-")[:32]
    environment["DTC_TEST_RUN_ID"] = f"issue-121-{os.getpid()}-{safe_marker or 'selection'}"
    return environment


def _collect_with_marker(marker: str) -> subprocess.CompletedProcess[str]:
    return _run_root_pytest("--collect-only", "-q", "-m", marker, label=marker)


def _run_root_pytest(
    *arguments: str,
    label: str,
    environment_updates: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = _collection_environment(label)
    if environment_updates is not None:
        environment.update(environment_updates)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-c", os.fspath(ROOT_CONFIG), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _collected_nodes(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {line for line in result.stdout.splitlines() if "::" in line}


def _assert_copied_e2e_denied(result: subprocess.CompletedProcess[str]) -> None:
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, output
    assert output.count(UNIFIED_COPIED_E2E_DENIAL) == 1
    assert "Page.goto" not in output
    assert "ERR_CONNECTION_REFUSED" not in output
    assert "127.0.0.1" not in output
    assert "web.dtcdev.click" not in output


def _protocol_sentinel_environment(
    tmp_path: Path,
    module_name: str,
) -> tuple[Path, Path, dict[str, str]]:
    plugin = tmp_path / f"{module_name}.py"
    protocol_sentinel = tmp_path / "protocol-started"
    root_conftest_seen = tmp_path / "root-conftest-loaded"
    plugin.write_text(
        """\
import os
from pathlib import Path

import pytest

PROTOCOL_SENTINEL = Path(os.environ["DTC_UNIFIED_E2E_SENTINEL"])
ROOT_CONFTEST_SEEN = Path(os.environ["DTC_ROOT_CONFTEST_SEEN"])
ROOT_CONFTEST = Path(os.environ["DTC_ROOT_CONFTEST"]).resolve()


def pytest_sessionstart(session):
    for registered in session.config.pluginmanager.get_plugins():
        source = getattr(registered, "__file__", None)
        if source is not None and Path(source).resolve() == ROOT_CONFTEST:
            ROOT_CONFTEST_SEEN.write_text("loaded", encoding="utf-8")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    PROTOCOL_SENTINEL.write_text("protocol", encoding="utf-8")
    raise RuntimeError("sentinel protocol reached")


@pytest.fixture
def page():
    PROTOCOL_SENTINEL.write_text("page", encoding="utf-8")
""",
        encoding="utf-8",
    )
    pythonpath = os.pathsep.join(
        part for part in (os.fspath(tmp_path), os.environ.get("PYTHONPATH", "")) if part
    )
    environment = {
        "DTC_ROOT_CONFTEST": os.fspath(ROOT / "conftest.py"),
        "DTC_ROOT_CONFTEST_SEEN": os.fspath(root_conftest_seen),
        "DTC_UNIFIED_E2E_SENTINEL": os.fspath(protocol_sentinel),
        "E2E_BASE_URL": "http://127.0.0.1:65534",
        "PYTHONPATH": pythonpath,
    }
    return protocol_sentinel, root_conftest_seen, environment


def test_copied_marker_registry_has_exact_unified_parity() -> None:
    copied_entries = _copied_registry()
    root_entries = _root_registry()

    _validate_registry_parity(copied_entries, root_entries)

    copied = dict(copied_entries)
    root = dict(root_entries)
    assert {name: root[name] for name in COPIED_SCENARIO_MARKERS} == copied
    assert {name: root[name] for name in EXISTING_ROOT_MARKERS} == EXISTING_ROOT_MARKERS
    assert COPIED_SCENARIO_MARKERS.isdisjoint(SAFETY_MARKERS)
    assert "email" in COPIED_SCENARIO_MARKERS
    assert "live_email" not in COPIED_SCENARIO_MARKERS


@pytest.mark.parametrize("name", sorted(COPIED_SCENARIO_MARKERS))
def test_registry_contract_rejects_each_removed_renamed_or_conflicting_marker(name: str) -> None:
    copied = _copied_registry()
    root = _root_registry()

    removed_from_copied = tuple(entry for entry in copied if entry[0] != name)
    renamed_in_copied = tuple(
        (f"renamed_{entry_name}", description) if entry_name == name else (entry_name, description)
        for entry_name, description in copied
    )
    removed_from_root = tuple(entry for entry in root if entry[0] != name)
    conflicting_root = tuple(
        (entry_name, "conflicting description") if entry_name == name else (entry_name, description)
        for entry_name, description in root
    )

    for copied_fixture, root_fixture in (
        (removed_from_copied, root),
        (renamed_in_copied, root),
        (copied, removed_from_root),
        (copied, conflicting_root),
    ):
        with pytest.raises(MarkerRegistryError):
            _validate_registry_parity(copied_fixture, root_fixture)


def test_registry_contract_rejects_new_and_duplicate_entries() -> None:
    copied = _copied_registry()
    root = _root_registry()
    smoke_description = dict(copied)["smoke"]

    cases = (
        (copied + (("new_scenario", "new copied scenario"),), root),
        (copied + (("smoke", smoke_description),), root),
        (copied + (("smoke", "conflicting description"),), root),
        (copied, root + (("smoke", smoke_description),)),
        (copied, root + (("smoke", "conflicting description"),)),
    )
    for copied_fixture, root_fixture in cases:
        with pytest.raises(MarkerRegistryError):
            _validate_registry_parity(copied_fixture, root_fixture)


def test_both_pytest_roots_remain_strict_and_standalone_e2e_isolated() -> None:
    root_options = _root_pytest_options()
    root_addopts = shlex.split(str(root_options["addopts"]))
    parser = configparser.ConfigParser(interpolation=None)
    copied_text = COPIED_CONFIG.read_text(encoding="utf-8")
    parser.read_string(copied_text)
    copied_addopts = shlex.split(parser.get("pytest", "addopts"))

    assert "--strict-markers" in root_addopts
    assert root_addopts[-2:] == ["-p", "test_support.root_pytest_guard"]
    assert root_options["pythonpath"] == ["."]
    assert "--strict-markers" in copied_addopts
    assert copied_addopts[-2:] == ["-p", "no:django"]
    assert parser.get("pytest", "testpaths") == "tests"
    assert "DJANGO_SETTINGS_MODULE" not in parser["pytest"]


def test_strict_markers_reject_an_unknown_fixture_marker(tmp_path: Path) -> None:
    config = tmp_path / "pytest.ini"
    fixture = tmp_path / "test_unknown_marker.py"
    config.write_text(
        "[pytest]\naddopts = --strict-markers -p no:django -p no:cacheprovider\n",
        encoding="utf-8",
    )
    fixture.write_text(
        "import pytest\n\n@pytest.mark.unknown_synthetic_marker\ndef test_fixture():\n    pass\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", os.fspath(config), "--collect-only", "-q"],
        cwd=tmp_path,
        env=_collection_environment("unknown"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == pytest.ExitCode.INTERRUPTED
    assert "unknown_synthetic_marker" in result.stdout
    assert "not found in `markers` configuration option" in result.stdout


def test_remote_readonly_collection_selects_only_three_deployed_nodes() -> None:
    result = _collect_with_marker("remote_readonly")

    assert result.returncode == pytest.ExitCode.OK, result.stdout
    assert _collected_nodes(result) == EXPECTED_REMOTE_READONLY_NODES
    assert "e2e/" not in result.stdout


def test_ordinary_local_selection_collects_after_copied_e2e_is_deselected() -> None:
    result = _collect_with_marker("core")
    nodes = _collected_nodes(result)

    assert result.returncode == pytest.ExitCode.OK, result.stdout + result.stderr
    assert nodes
    assert all(node.startswith("playwright_tests/") for node in nodes)
    assert "e2e/" not in result.stdout


def test_direct_copied_e2e_node_is_denied_before_playwright_or_network() -> None:
    result = _run_root_pytest(
        COPIED_DIRECT_NODE,
        "-q",
        label="direct-node",
        environment_updates={"E2E_BASE_URL": "http://127.0.0.1:65534"},
    )

    _assert_copied_e2e_denied(result)


def test_ambient_confcutdir_cannot_omit_code_owned_root_guard(tmp_path: Path) -> None:
    protocol_sentinel, root_conftest_seen, environment = _protocol_sentinel_environment(
        tmp_path,
        "issue_121_confcut_sentinel",
    )
    environment["PYTEST_ADDOPTS"] = "--confcutdir=e2e -p issue_121_confcut_sentinel"

    result = _run_root_pytest(
        COPIED_DIRECT_NODE,
        "-q",
        label="ambient-confcutdir",
        environment_updates=environment,
    )

    _assert_copied_e2e_denied(result)
    assert not root_conftest_seen.exists()
    assert not protocol_sentinel.exists()


@pytest.mark.parametrize(
    ("ambient_template", "root_conftest_expected"),
    [
        ("--rootdir=e2e -p {plugin}", True),
        ("-p {plugin} --rootdir={e2e}", True),
        ("--rootdir=. --rootdir=e2e -p {plugin}", True),
        ("--rootdir=e2e -p {plugin} --confcutdir=e2e", False),
    ],
    ids=("relative", "absolute-reordered", "duplicate", "ordered-with-confcutdir"),
)
def test_ambient_rootdir_cannot_change_unified_config_identity_or_omit_guard(
    tmp_path: Path,
    ambient_template: str,
    root_conftest_expected: bool,
) -> None:
    module_name = "issue_121_rootdir_sentinel"
    protocol_sentinel, root_conftest_seen, environment = _protocol_sentinel_environment(
        tmp_path,
        module_name,
    )
    environment["PYTEST_ADDOPTS"] = ambient_template.format(
        plugin=module_name,
        e2e=(ROOT / "e2e").as_posix(),
    )

    result = _run_root_pytest(
        COPIED_DIRECT_NODE,
        "-q",
        label="ambient-rootdir",
        environment_updates=environment,
    )

    _assert_copied_e2e_denied(result)
    assert root_conftest_seen.exists() is root_conftest_expected
    assert not protocol_sentinel.exists()


@pytest.mark.parametrize("spoof_nodeid", [False, True])
def test_item_path_and_marker_spoof_cannot_erase_original_copied_provenance(
    tmp_path: Path,
    spoof_nodeid: bool,
) -> None:
    plugin = tmp_path / "issue_121_provenance_spoof.py"
    sentinel = tmp_path / "protocol-started"
    plugin.write_text(
        """\
import os
from pathlib import Path

import pytest

SENTINEL = Path(os.environ["DTC_UNIFIED_E2E_SENTINEL"])
SPOOFED_PATH = Path(os.environ["DTC_UNIFIED_E2E_SPOOFED_PATH"]).resolve()
SPOOF_NODEID = os.environ["DTC_UNIFIED_E2E_SPOOF_NODEID"] == "yes"


@pytest.hookimpl(tryfirst=True)
def pytest_itemcollected(item):
    if not item.nodeid.startswith("e2e/"):
        return
    original_name = item.nodeid.partition("::")[2]
    item.path = SPOOFED_PATH
    item.add_marker(pytest.mark.core)
    if SPOOF_NODEID:
        item._nodeid = f"playwright_tests/test_foundation_smoke.py::{original_name}"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    SENTINEL.write_text("protocol", encoding="utf-8")
    raise RuntimeError("sentinel protocol reached")


@pytest.fixture
def page():
    SENTINEL.write_text("page", encoding="utf-8")
""",
        encoding="utf-8",
    )
    pythonpath = os.pathsep.join(
        part for part in (os.fspath(tmp_path), os.environ.get("PYTHONPATH", "")) if part
    )

    result = _run_root_pytest(
        "e2e/tests/test_00_availability.py",
        "-m",
        "core",
        "-q",
        "-p",
        "issue_121_provenance_spoof",
        label=f"provenance-spoof-{spoof_nodeid}",
        environment_updates={
            "DTC_UNIFIED_E2E_SENTINEL": os.fspath(sentinel),
            "DTC_UNIFIED_E2E_SPOOFED_PATH": os.fspath(
                ROOT / "playwright_tests" / "test_foundation_smoke.py"
            ),
            "DTC_UNIFIED_E2E_SPOOF_NODEID": "yes" if spoof_nodeid else "no",
            "E2E_BASE_URL": "http://127.0.0.1:65534",
            "PYTHONPATH": pythonpath,
        },
    )

    _assert_copied_e2e_denied(result)
    assert not sentinel.exists()


@pytest.mark.parametrize(
    ("arguments", "environment_updates"),
    [
        (("-o", "addopts="), {}),
        ((), {"PYTEST_ADDOPTS": "-p no:test_support.root_pytest_guard"}),
    ],
)
def test_explicit_root_guard_removal_is_outside_supported_boundary_but_collect_only_is_safe(
    arguments: tuple[str, ...],
    environment_updates: dict[str, str],
) -> None:
    result = _run_root_pytest(
        *arguments,
        COPIED_DIRECT_NODE,
        "--collect-only",
        "-q",
        label="explicit-guard-removal",
        environment_updates=environment_updates,
    )
    output = result.stdout + result.stderr

    assert result.returncode == pytest.ExitCode.OK, output
    assert UNIFIED_COPIED_E2E_DENIAL not in output
    assert "1 test collected" in output


@pytest.mark.parametrize("marker", sorted(COPIED_SCENARIO_MARKERS))
def test_each_copied_marker_alias_is_denied_under_unified_root(marker: str) -> None:
    result = _collect_with_marker(marker)

    _assert_copied_e2e_denied(result)


def test_boolean_marker_expression_cannot_mix_copied_and_root_scenarios() -> None:
    result = _collect_with_marker("remote_readonly or smoke")

    _assert_copied_e2e_denied(result)


@pytest.mark.parametrize("hook_order", ["tryfirst", "trylast"])
def test_dynamic_safety_marker_injection_cannot_bypass_unified_root_guard(
    tmp_path: Path,
    hook_order: str,
) -> None:
    plugin = tmp_path / "issue_121_marker_plugin.py"
    sentinel = tmp_path / "runtest-started"
    hook_argument = f"{hook_order}=True"
    plugin.write_text(
        f"""\
import os
from pathlib import Path

import pytest

SENTINEL = Path(os.environ["DTC_UNIFIED_E2E_SENTINEL"])
DESELECTED_E2E = []


def _record(phase):
    SENTINEL.write_text(phase, encoding="utf-8")


def pytest_deselected(items):
    DESELECTED_E2E.extend(
        item
        for item in items
        if "/e2e/" in f"/{{Path(str(item.path)).resolve().as_posix()}}"
    )


@pytest.hookimpl({hook_argument})
def pytest_collection_modifyitems(items):
    candidates = [*items, *DESELECTED_E2E]
    for item in candidates:
        if "/e2e/" in f"/{{Path(str(item.path)).resolve().as_posix()}}":
            item.add_marker(pytest.mark.remote_readonly)
            if item not in items:
                items.append(item)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    _record("protocol")
    raise RuntimeError("sentinel protocol reached")


@pytest.fixture(autouse=True)
def _sentinel_fixture():
    _record("fixture")


@pytest.fixture
def page():
    _record("page")
""",
        encoding="utf-8",
    )
    pythonpath = os.pathsep.join(
        part for part in (os.fspath(tmp_path), os.environ.get("PYTHONPATH", "")) if part
    )

    result = _run_root_pytest(
        "-q",
        "-m",
        "remote_readonly",
        "-p",
        "issue_121_marker_plugin",
        label=f"dynamic-{hook_order}",
        environment_updates={
            "DTC_TEST_BASE_URL": "https://web.dtcdev.click",
            "DTC_TEST_REMOTE_NAMESPACE": "issue-121-dynamic-marker",
            "DTC_TEST_SAFETY_COMMAND": "remote_readonly",
            "DTC_TEST_TARGET_CLASS": "isolated_development",
            "DTC_UNIFIED_E2E_SENTINEL": os.fspath(sentinel),
            "E2E_BASE_URL": "http://127.0.0.1:65534",
            "PYTHONPATH": pythonpath,
        },
    )

    _assert_copied_e2e_denied(result)
    assert not sentinel.exists()


def test_standalone_copied_e2e_configuration_still_collects_all_nodes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            os.fspath(COPIED_CONFIG),
            "--collect-only",
            "-q",
        ],
        cwd=COPIED_CONFIG.parent,
        env=_collection_environment("standalone"),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == pytest.ExitCode.OK, result.stdout + result.stderr
    assert result.stdout.count("<Function ") == 48
    assert "48 tests collected" in result.stdout


@pytest.mark.parametrize("marker", sorted(SAFETY_MARKERS - {"remote_readonly"}))
def test_other_safety_selectors_do_not_alias_copied_scenarios(marker: str) -> None:
    result = _collect_with_marker(marker)

    assert result.returncode == pytest.ExitCode.NO_TESTS_COLLECTED, result.stdout
    assert _collected_nodes(result) == set()
    assert "e2e/" not in result.stdout


def test_make_targets_keep_local_and_safety_marker_families_separate() -> None:
    safety_exclusions = (
        "not remote_readonly and not remote_mutation and not live_email and not live_provider"
    )
    assert f"core and {safety_exclusions}" in _make_recipe("test-playwright-core")
    assert f"(core or full) and {safety_exclusions}" in _make_recipe("test-playwright")

    for marker in sorted(SAFETY_MARKERS):
        recipe = _make_recipe(f"test-{marker.replace('_', '-')}")
        assert f"DTC_TEST_SAFETY_COMMAND={marker}" in recipe
        assert f"pytest -m {marker} -v" in recipe
