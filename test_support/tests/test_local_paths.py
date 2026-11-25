"""Ensure no Django test reads ~/prod/dtc-data or other machine-local prod paths."""

from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from test_support.local_paths import (
    LOCAL_PATH_GUARD_ENV,
    LocalPathGuard,
    MachineLocalPathDenied,
)

REPO_ROOT = Path(settings.BASE_DIR).resolve()

# Non-reading references to Path.home() / "prod" that are explicitly permitted:
# - test_local_paths.py tests the guard itself.
# - test_import_events.py asserts EVENT_CONTENT_PATH.is_relative_to(...) in memory.
# - test_public_media_synced_inventory.py installs its own ~/prod read refusal.
_ALLOWED_HOME_PROD_TEST_FILES = frozenset(
    {
        "test_support/tests/test_local_paths.py",
        "scripts/tests/test_import_events.py",
        "content/tests/test_public_media_synced_inventory.py",
    }
)


def _django_test_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(REPO_ROOT.rglob("test_*.py")):
        rel = path.relative_to(REPO_ROOT)
        if any(part.startswith(".") or part in {"node_modules", "tests"} for part in rel.parts[:1]):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) for node in tree.body):
            files.append(path)
    return files


def _uses_home_prod_path(source: str) -> bool:
    """True when executable Python code constructs a path under Path.home() / 'prod'."""

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Ignore docstrings: ast.Expr(value=ast.Constant(...))
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if isinstance(right, ast.Constant) and right.value in {"prod", "dtc-data"}:
                return True
    return False


class LocalPathGuardTests(SimpleTestCase):
    def test_guard_is_active_under_the_django_test_runner(self) -> None:
        self.assertEqual(os.environ.get(LOCAL_PATH_GUARD_ENV), "1")

    def test_open_and_path_reads_under_home_prod_are_denied(self) -> None:
        target = (
            Path.home()
            / "prod"
            / "dtc-data"
            / "content-staging"
            / "public_projection"
            / "events.json"
        )
        with LocalPathGuard():
            with self.assertRaises(MachineLocalPathDenied):
                open(target, encoding="utf-8")
            with self.assertRaises(MachineLocalPathDenied):
                target.read_text(encoding="utf-8")
            with self.assertRaises(MachineLocalPathDenied):
                target.read_bytes()
            with self.assertRaises(MachineLocalPathDenied):
                os.listdir(Path.home() / "prod")
            with self.assertRaises(MachineLocalPathDenied):
                os.scandir(Path.home() / "prod" / "dtc-data")

    def test_repo_fixtures_and_temp_files_remain_readable(self) -> None:
        with LocalPathGuard(), tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "fixture.txt"
            sample.write_text("ok", encoding="utf-8")
            self.assertEqual(sample.read_text(encoding="utf-8"), "ok")

    def test_no_django_test_file_constructs_home_prod_paths(self) -> None:
        offenders: list[str] = []
        for path in _django_test_files():
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in _ALLOWED_HOME_PROD_TEST_FILES:
                continue
            source = path.read_text(encoding="utf-8")
            if _uses_home_prod_path(source):
                offenders.append(rel)
        self.assertEqual(
            offenders,
            [],
            f"Django tests must use checked-in fixtures or temp dirs, never ~/prod: {offenders}",
        )
