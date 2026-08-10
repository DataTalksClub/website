from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .factories.context import canonical_json_bytes


class MigrationContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MigrationSeed:
    path: Path
    seed_version: str
    start: tuple[str, str]
    target: tuple[str, str]
    payload: dict[str, Any]
    expected: dict[str, Any]
    reversible: bool


def load_migration_seed(path: Path) -> MigrationSeed:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MigrationContractError("migration seed is malformed") from error
    expected_keys = {
        "expected",
        "payload",
        "payload_sha256",
        "reversible",
        "schema_version",
        "seed_version",
        "start",
        "target",
    }
    if not isinstance(raw, dict) or set(raw) != expected_keys or raw["schema_version"] != 1:
        raise MigrationContractError("migration seed fields or schema are invalid")
    digest = hashlib.sha256(canonical_json_bytes(raw["payload"])).hexdigest()
    if digest != raw["payload_sha256"]:
        raise MigrationContractError("migration seed payload checksum changed; add a new version")
    if (
        not isinstance(raw["start"], list)
        or len(raw["start"]) != 2
        or not all(isinstance(value, str) and value for value in raw["start"])
        or not isinstance(raw["target"], list)
        or len(raw["target"]) != 2
        or not all(isinstance(value, str) and value for value in raw["target"])
        or not isinstance(raw["payload"], dict)
        or not isinstance(raw["expected"], dict)
        or not isinstance(raw["expected"].get("unresolved"), list)
        or not isinstance(raw["reversible"], bool)
    ):
        raise MigrationContractError("migration seed contract is invalid")
    return MigrationSeed(
        path=path,
        seed_version=raw["seed_version"],
        start=tuple(raw["start"]),
        target=tuple(raw["target"]),
        payload=raw["payload"],
        expected=raw["expected"],
        reversible=raw["reversible"],
    )


def data_migration_functions(path: Path) -> tuple[ast.FunctionDef, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    run_python_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "RunPython" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id != "noop":
            run_python_names.add(first.id)
    return tuple(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in run_python_names
    )


def assert_data_migration_isolation(path: Path) -> None:
    forbidden_roots = {
        "boto3",
        "datetime",
        "factory_boy",
        "httpx",
        "random",
        "requests",
        "socket",
        "time",
        "urllib",
    }
    forbidden_application_suffixes = {"factories", "models", "services"}
    application_roots = {
        "accounts",
        "content",
        "core",
        "courses",
        "email_app",
        "events",
        "jobs",
        "management_auth",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] in forbidden_roots or (
                parts[0] in application_roots
                and len(parts) > 1
                and parts[-1] in forbidden_application_suffixes
            ):
                raise MigrationContractError(
                    f"data migration {path.name} imports non-historical runtime code"
                )


def assert_stable_migration_module_isolation(path: Path) -> None:
    """Reject application/runtime imports from a module loaded by historical migrations.

    The check intentionally follows the migration's first local import boundary: a migration-
    owned helper may use Python and Django primitives, but may not transitively pull current app
    code into a historical migration replay.
    """

    application_roots = {
        "accounts",
        "content",
        "core",
        "courses",
        "email_app",
        "events",
        "jobs",
        "management_auth",
    }
    forbidden_runtime_roots = {
        "boto3",
        "datetime",
        "factory_boy",
        "httpx",
        "random",
        "requests",
        "socket",
        "time",
        "urllib",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = name.split(".", 1)[0]
            if root in application_roots or root in forbidden_runtime_roots:
                raise MigrationContractError(
                    f"stable migration module {path.name} imports mutable runtime code"
                )
