"""Catch a first-party import or script path that names a deleted file.

Deleting a module while a test or script still imports it does not fail any
focused gate: the Django runner only notices at discovery, where the whole
package collapses into one failed-import placeholder and the full suite turns
red (issue #427, after the staged-import pipeline removal left
``scripts/tests/test_sync_content_verify.py`` importing
``scripts.prod.sync_content_verify`` and ``content_sync.tests.helpers``).

This guard runs in the pure-Python CI contract tier, before the Django suite
is collected. It reads the git index and the files on disk only -- nothing is
imported, so a dangling reference is reported by name instead of by a
collection traceback. It checks two reference shapes:

* every absolute or relative ``import``/``from ... import`` whose top-level
  name is a first-party package or module must resolve to a file on disk, and
  a ``from package import name`` must name a submodule or a name the
  package's ``__init__`` binds; and
* every ``scripts/....py`` path literal in a non-test command script (the
  shape ``scripts/content.py`` and ``scripts/production_data.py`` dispatch
  through ``uv run``) must name a tracked file.
"""

from __future__ import annotations

import ast
import re
import subprocess
from functools import cache
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = re.compile(r"scripts/[A-Za-z0-9_/]+\.py")


@cache
def _tracked() -> frozenset[str]:
    listing = subprocess.run(
        ("git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"),
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return frozenset(entry for entry in listing.split("\0") if entry and (ROOT / entry).exists())


def _python_files() -> list[str]:
    return sorted(
        path
        for path in _tracked()
        if path.endswith(".py") and "/fixtures/" not in f"/{path}" and "/migrations/" not in path
    )


@cache
def _first_party() -> frozenset[str]:
    names = set()
    for path in _tracked():
        parts = PurePosixPath(path).parts
        if len(parts) == 1 and path.endswith(".py"):
            names.add(path[:-3])
        elif len(parts) == 2 and parts[1] == "__init__.py":
            names.add(parts[0])
    return frozenset(names)


def _module_file(dotted: str) -> str | None:
    relative = dotted.replace(".", "/")
    for candidate in (f"{relative}.py", f"{relative}/__init__.py"):
        if candidate in _tracked():
            return candidate
    # A namespace directory (no __init__) is importable when it holds tracked modules.
    if any(path.startswith(f"{relative}/") for path in _tracked()):
        return relative
    return None


@cache
def _bound_names(init_file: str) -> frozenset[str] | None:
    """Names a package ``__init__`` binds, or ``None`` when it cannot be known statically."""
    if not init_file.endswith("__init__.py"):
        return frozenset()
    tree = ast.parse((ROOT / init_file).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
            return None
    if "__getattr__" in names:
        return None
    return frozenset(names)


def _package_of(path: str) -> list[str]:
    parts = list(PurePosixPath(path).parts)
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts.pop()
        return parts
    return parts[:-1]


def _dangling_references(path: str) -> list[str]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _first_party() and not _module_file(alias.name):
                    problems.append(f"{path}:{node.lineno} imports missing {alias.name}")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            package = _package_of(path)
            if node.level > 1:
                package = package[: len(package) - (node.level - 1)]
            dotted = ".".join([*package, *([node.module] if node.module else [])])
        else:
            dotted = node.module or ""
        if dotted.split(".")[0] not in _first_party():
            continue
        module = _module_file(dotted)
        if module is None:
            problems.append(f"{path}:{node.lineno} imports missing {dotted}")
            continue
        bound = _bound_names(module)
        for alias in node.names:
            if alias.name == "*" or _module_file(f"{dotted}.{alias.name}"):
                continue
            if bound is not None and module.endswith("__init__.py") and alias.name not in bound:
                problems.append(f"{path}:{node.lineno} imports missing {dotted}.{alias.name}")
    return problems


def _is_test_module(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return "tests" in parts or "tests_ci" in parts or parts[-1].startswith("test_")


def _dangling_script_paths(path: str) -> list[str]:
    # Only a literal passed straight to a call is a dispatch; a path inside a
    # set/tuple literal is data (for example a reviewed list of retired files).
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
    return [
        f"{path}:{argument.lineno} dispatches missing {argument.value}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for argument in node.args
        if isinstance(argument, ast.Constant)
        and isinstance(argument.value, str)
        and SCRIPT_PATH.fullmatch(argument.value)
        and argument.value not in _tracked()
    ]


def test_first_party_imports_resolve_to_files_on_disk() -> None:
    problems = [problem for path in _python_files() for problem in _dangling_references(path)]
    assert not problems, "Dangling first-party imports:\n" + "\n".join(problems)


def test_command_scripts_dispatch_only_existing_scripts() -> None:
    problems = [
        problem
        for path in _python_files()
        if path.startswith("scripts/") and not _is_test_module(path)
        for problem in _dangling_script_paths(path)
    ]
    assert not problems, "Command scripts dispatch missing files:\n" + "\n".join(problems)


def test_the_guard_reports_the_issue_427_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard names both regression shapes rather than passing vacuously."""
    sample = "scripts/tests/_dangling_guard_probe.py"
    source = (
        "from content_sync.tests.helpers import write_tree\n"
        "from scripts.prod import sync_content_verify\n"
        "import scripts.prod.sync_content_verify\n"
        "from scripts.prod import sync_course_repositories\n"
    )
    real = Path.read_text

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == ROOT / sample:
            return source
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", read_text)
    problems = _dangling_references(sample)
    assert problems == [
        f"{sample}:1 imports missing content_sync.tests.helpers",
        f"{sample}:2 imports missing scripts.prod.sync_content_verify",
        f"{sample}:3 imports missing scripts.prod.sync_content_verify",
    ]

    script = "scripts/_dangling_guard_probe.py"
    command = '_python("scripts/prod/sync_content_verify.py", "--checkout-plan")\n'
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **k: command if self == ROOT / script else real(self, *a, **k),
    )
    assert _dangling_script_paths(script) == [
        f"{script}:1 dispatches missing scripts/prod/sync_content_verify.py"
    ]
