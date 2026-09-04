from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from test_support.migrations import (
    MigrationContractError,
    assert_stable_migration_module_isolation,
    migration_application_imports,
)

ROOT = Path(__file__).resolve().parents[2]


class StableMigrationModuleTests(unittest.TestCase):
    """A module a migration imports must not drag current app code into a replay."""

    def test_content_migration_validators_import_nothing_mutable(self) -> None:
        assert_stable_migration_module_isolation(ROOT / "content" / "migration_validators.py")

    def test_every_module_a_migration_imports_is_stable(self) -> None:
        """The trap: a field validator that serializes as an import, not a value.

        A named validator object in a models module makes the migration name
        ``courses.models.<x>.<validator>``, which imports live model code on
        every replay.  Building the validator inline from plain constants makes
        Django serialize it by value instead.
        """

        unstable: dict[str, str] = {}
        for path in sorted(ROOT.glob("*/migrations/[0-9]*.py")):
            for module in migration_application_imports(path):
                candidate = ROOT / Path(*module.split(".")).with_suffix(".py")
                if not candidate.is_file():
                    candidate = ROOT / Path(*module.split(".")) / "__init__.py"
                if not candidate.is_file():
                    unstable[module] = f"{module} is not resolvable to a file"
                    continue
                try:
                    assert_stable_migration_module_isolation(candidate)
                except MigrationContractError as error:
                    unstable[module] = str(error)

        self.assertEqual(
            unstable,
            {},
            f"a migration loads mutable runtime code: {unstable}",
        )

    def test_replaying_the_url_validator_migration_never_imports_requests(self) -> None:
        """The concrete proof: loading the migration file, in a fresh
        process, must not pull in ``requests`` or ``core.security`` --
        regardless of what the static AST check above concludes.

        Runs in a subprocess, without ``django.setup()``/``apps.populate()``,
        so unrelated app code (several apps legitimately import ``requests``
        from their own ``AppConfig.ready()``/admin registration) cannot mask
        an import that the migration itself triggers.
        """

        migration_path = ROOT / "courses" / "migrations" / "0001_initial.py"
        script = textwrap.dedent(
            f"""
            import importlib.util
            import sys

            from django.conf import settings

            # Force lazy settings configuration without populating the app
            # registry -- ``django.db.migrations``/``django.db.models`` don't
            # need it, and populating it would import unrelated apps that
            # legitimately use ``requests`` for their own reasons.
            _ = settings.INSTALLED_APPS

            spec = importlib.util.spec_from_file_location(
                "courses.migrations._isolated_0001_initial",
                {str(migration_path)!r},
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            assert "requests" not in sys.modules, (
                "loading the migration imported requests as a side effect"
            )
            assert "core.security" not in sys.modules, (
                "loading the migration imported core.security as a side effect"
            )
            print("OK")
            """
        )
        # Scrub every DTC_*/test-worker-specific variable before handing the
        # environment to the child process: this test must prove the migration
        # is import-clean regardless of what the parent test runner happens to
        # have set (e.g. DTC_SQLITE_PATH, which website.settings.test refuses
        # outright as an attempt to override the owned test worker database).
        env = {key: value for key, value in os.environ.items() if not key.startswith("DTC_")}
        env.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.test")
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"isolated migration import failed:\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("OK", result.stdout)

    def test_the_boundary_rejects_a_transitive_current_app_import(self) -> None:
        layout = settings.TEST_RUNTIME.worker("migration-import-boundary")
        unsafe_module = layout.artifacts / "unsafe_migration_validator.py"
        unsafe_module.parent.mkdir(parents=True, exist_ok=True)
        unsafe_module.write_text(
            "from core.redaction import is_sensitive_text\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "imports mutable runtime code"):
            assert_stable_migration_module_isolation(unsafe_module)


class IsolatedMigrationExecutorTests(unittest.TestCase):
    """The properties that matter: an empty database reaches the schema, and reverses."""

    FIRST_PARTY_APPS = (
        "accounts",
        "content",
        "core",
        "courses",
        "data",
        "email_app",
        "events",
        "jobs",
        "management_auth",
    )

    def setUp(self) -> None:
        super().setUp()
        digest = hashlib.sha256(self.id().encode("utf-8")).hexdigest()[:12]
        self.worker_id = f"migration-{digest}"
        layout = settings.TEST_RUNTIME.worker(self.worker_id)
        database_path = settings.TEST_RUNTIME.assert_database_path(
            layout.database,
            worker_id=self.worker_id,
        )
        self.connection = connections["default"]
        self.connection.close()
        self.original_name = self.connection.settings_dict["NAME"]
        self.original_test = self.connection.settings_dict["TEST"]
        self.original_worker = self.connection.settings_dict["DTC_WORKER_ID"]
        self.connection.settings_dict["NAME"] = database_path
        self.connection.settings_dict["TEST"] = {"NAME": database_path}
        self.connection.settings_dict["DTC_WORKER_ID"] = self.worker_id

    def tearDown(self) -> None:
        self.connection.close()
        self.connection.settings_dict["NAME"] = self.original_name
        self.connection.settings_dict["TEST"] = self.original_test
        self.connection.settings_dict["DTC_WORKER_ID"] = self.original_worker
        self.connection.connect()
        super().tearDown()

    def test_clean_database_migrates_from_zero_to_every_leaf(self) -> None:
        executor = MigrationExecutor(self.connection)
        leaves = executor.loader.graph.leaf_nodes()
        self.assertEqual(executor.loader.applied_migrations, {})
        executor.migrate(leaves)
        applied = MigrationExecutor(self.connection).loader.applied_migrations
        self.assertTrue(all(leaf in applied for leaf in leaves))

    def test_every_first_party_app_reverses_to_zero(self) -> None:
        executor = MigrationExecutor(self.connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        for app_label in reversed(self.FIRST_PARTY_APPS):
            with self.subTest(app=app_label):
                MigrationExecutor(self.connection).migrate([(app_label, None)])
                applied = MigrationExecutor(self.connection).loader.applied_migrations
                self.assertFalse(
                    [node for node in applied if node[0] == app_label],
                    f"{app_label} did not reverse to zero",
                )
