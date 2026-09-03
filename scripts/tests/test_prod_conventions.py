"""The `scripts/prod` directory has to stay readable without a README.

A person opening it must be able to tell from a filename whether a script is
re-synchronized against a moving upstream or read once from frozen history, and
which scripts can populate an empty database.  Both facts are declared in the
modules and in ``scripts/prod/__init__.py``; these tests keep the declarations and
the code from drifting apart.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from django.test import TestCase

import scripts.prod
from scripts.prod import (
    BOOTSTRAPPING_ENTRY_POINTS,
    COURSE_CATALOGUE_ORDER,
    SYNC_MODELS,
)

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent


def _entry_point_names() -> list[str]:
    return sorted(
        module.name for module in pkgutil.iter_modules([str(PROD_ROOT)]) if not module.ispkg
    )


class ProdEntryPointConventionTests(TestCase):
    """The filename, the declared sync model and the docstring must agree."""

    def test_every_entry_point_declares_a_known_sync_model(self) -> None:
        names = _entry_point_names()
        self.assertTrue(names, "scripts/prod must expose at least one entry point")
        for name in names:
            with self.subTest(module=name):
                module = importlib.import_module(f"scripts.prod.{name}")
                sync_model = getattr(module, "SYNC_MODEL", None)
                self.assertIn(
                    sync_model,
                    SYNC_MODELS,
                    f"scripts/prod/{name}.py must declare SYNC_MODEL",
                )

    def test_the_filename_prefix_matches_the_declared_sync_model(self) -> None:
        expected_prefix = {
            "git-synchronized": "sync_",
            "one-time": "import_",
        }
        for name in _entry_point_names():
            with self.subTest(module=name):
                module = importlib.import_module(f"scripts.prod.{name}")
                prefix = expected_prefix[module.SYNC_MODEL]
                self.assertTrue(
                    name.startswith(prefix),
                    f"a {module.SYNC_MODEL} entry point must be named {prefix}*",
                )

    def test_every_bootstrapping_module_is_declared(self) -> None:
        """A reconciler run first is a silent no-op, so which is which is checked.

        The check runs in the direction that matters: a module that *can* populate an
        empty database must say so in the package.  A declared name whose module is not
        present is not a failure -- an entry point can be in flight in another branch --
        so this cannot break simply because someone has not landed their script yet.
        """

        bootstrapping = {
            name
            for name in _entry_point_names()
            if getattr(
                importlib.import_module(f"scripts.prod.{name}"), "BOOTSTRAPS_EMPTY_DATABASE", False
            )
        }
        undeclared = bootstrapping - set(BOOTSTRAPPING_ENTRY_POINTS)
        self.assertEqual(undeclared, set())

    def test_a_declared_bootstrapping_module_really_bootstraps(self) -> None:
        present = set(_entry_point_names())
        for name in sorted(set(BOOTSTRAPPING_ENTRY_POINTS) & present):
            with self.subTest(module=name):
                module = importlib.import_module(f"scripts.prod.{name}")
                self.assertTrue(module.BOOTSTRAPS_EMPTY_DATABASE)

    def test_cmp_is_last_in_the_declared_course_catalogue_order(self) -> None:
        """It reconciles against what the repositories and the legacy import wrote."""

        self.assertEqual(COURSE_CATALOGUE_ORDER[-1], "import_cmp_content")
        self.assertEqual(COURSE_CATALOGUE_ORDER[0], "import_legacy_zoomcamp")

    def test_no_entry_point_imports_a_placeholder_seeder(self) -> None:
        """The split is by what data a module touches; seeders invent rows."""

        forbidden = ("local_course_seed", "local_question_seed", "local_project_review_seed")
        for path in sorted(PROD_ROOT.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for seeder in forbidden:
                with self.subTest(module=path.name, seeder=seeder):
                    self.assertNotIn(f"import {seeder}", source)
