from __future__ import annotations

import ast
from pathlib import Path


class MigrationContractError(ValueError):
    pass


APPLICATION_ROOTS = frozenset(
    {
        "accounts",
        "content",
        "core",
        "courses",
        "data",
        "email_app",
        "events",
        "jobs",
        "management_auth",
    }
)


def migration_application_imports(path: Path) -> tuple[str, ...]:
    """Application modules a migration file imports, deduplicated and sorted.

    A migration that names ``courses.models.testimonial.some_validator`` in a
    field carries ``import courses.models.testimonial`` at the top, so reading
    the imports is enough to find what a replay would load.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            if name.split(".", 1)[0] in APPLICATION_ROOTS:
                modules.add(name)
    return tuple(sorted(modules))


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
