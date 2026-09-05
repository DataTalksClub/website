"""Where a ``scripts/prod`` entry point writes, and what it refuses to do.

The local route is pinned value-for-value against what the thirteen copied
``_configure`` functions did, because that is the behaviour every Make target,
rehearsal command and existing test depends on.  The deployed route is pinned
against ``deploy.deployment_targets``, so the environment an import runs with and the
environment the deployed task runs with cannot drift apart.

Nothing here connects to a database.  The one test that boots production settings does
so in a subprocess with synthetic values and asserts that Django resolved a PostgreSQL
configuration *without* opening a connection.
"""

from __future__ import annotations

import argparse
import json
import pkgutil
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

import scripts.prod
from deploy.deployment_targets import DEPLOYMENT_TARGETS
from scripts.prod.target import (
    LOCAL_SETTINGS_MODULE,
    REQUIRED_DEPLOYED_ENVIRONMENT,
    TargetRefused,
    add_target_arguments,
    resolve_target,
)

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent
PRODUCTION_TARGET = "website-production"
RETIRED_TARGET = "website-sandbox"

# Synthetic stand-ins with the exact shape the deployed contract requires. The
# release identity is the one `make deployment-check` already uses; the URL and the
# key are invented here and address nothing.
SYNTHETIC_DEPLOYED_ENVIRONMENT = {
    "DATABASE_URL": "postgresql://check:check@127.0.0.1:5432/check",
    "DJANGO_SECRET_KEY": "synthetic-target-resolution-key-that-is-long-enough-to-pass",
    "VERSION": "20260809-143205-aaaaaaa",
    "SOURCE_SHA": "a" * 40,
    "IMAGE_DIGEST": "sha256:" + "b" * 64,
}


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_target_arguments(parser)
    return parser.parse_args(argv)


class LocalTargetTests(SimpleTestCase):
    """`--database` must still do exactly what the copied `_configure` did."""

    def test_a_database_path_selects_local_sqlite_the_way_it_always_did(self) -> None:
        target = resolve_target(_parse("--database", ".tmp/scratch.sqlite3"), {})

        self.assertFalse(target.deployed)
        self.assertEqual(
            dict(target.forced),
            {
                "DTC_ENVIRONMENT": "local",
                "DTC_SQLITE_PATH": str((Path.cwd() / ".tmp/scratch.sqlite3").resolve()),
            },
        )
        self.assertEqual(dict(target.defaulted), {"DJANGO_SETTINGS_MODULE": LOCAL_SETTINGS_MODULE})
        self.assertEqual(target.cleared, ())

    def test_an_already_chosen_settings_module_still_wins(self) -> None:
        """Every in-process test calls `main()` under `website.settings.test`."""

        environ = {"DJANGO_SETTINGS_MODULE": "website.settings.test"}
        resolve_target(_parse("--database", ".tmp/scratch.sqlite3"), environ).apply(environ)

        self.assertEqual(environ["DJANGO_SETTINGS_MODULE"], "website.settings.test")
        self.assertEqual(environ["DTC_ENVIRONMENT"], "local")

    def test_a_shell_that_already_selects_production_cannot_promote_a_local_run(self) -> None:
        for environ in (
            {"DTC_ENVIRONMENT": "production"},
            {"DJANGO_SETTINGS_MODULE": "website.settings.production"},
            {"DJANGO_SETTINGS_MODULE": "website.settings.development"},
        ):
            with self.subTest(environ=sorted(environ)):
                with self.assertRaises(TargetRefused) as refusal:
                    resolve_target(_parse("--database", ".tmp/scratch.sqlite3"), environ)
                self.assertIn("--deployment-target", str(refusal.exception))

    def test_the_opt_in_is_meaningless_without_a_deployment_target(self) -> None:
        """So it cannot sit in a rehearsal command waiting to become live."""

        with self.assertRaises(TargetRefused) as refusal:
            resolve_target(
                _parse(
                    "--database",
                    ".tmp/scratch.sqlite3",
                    "--allow-production-write",
                    PRODUCTION_TARGET,
                ),
                {},
            )
        self.assertIn("--allow-production-write", str(refusal.exception))

    def test_a_run_with_no_target_at_all_is_refused_by_argparse(self) -> None:
        with self.assertRaises(SystemExit):
            _parse("--allow-production-write", PRODUCTION_TARGET)


class DeployedTargetRefusalTests(SimpleTestCase):
    """Everything someone has to get right before a row can reach production."""

    def test_selecting_production_without_the_opt_in_is_refused(self) -> None:
        with self.assertRaises(TargetRefused) as refusal:
            resolve_target(
                _parse("--deployment-target", PRODUCTION_TARGET),
                SYNTHETIC_DEPLOYED_ENVIRONMENT,
            )
        message = str(refusal.exception)
        self.assertIn(f"--allow-production-write {PRODUCTION_TARGET}", message)
        self.assertNotIn("postgresql", message)

    def test_an_opt_in_naming_another_target_is_refused(self) -> None:
        with self.assertRaises(TargetRefused) as refusal:
            resolve_target(
                _parse(
                    "--deployment-target",
                    PRODUCTION_TARGET,
                    "--allow-production-write",
                    RETIRED_TARGET,
                ),
                SYNTHETIC_DEPLOYED_ENVIRONMENT,
            )
        self.assertIn("must repeat", str(refusal.exception))

    def test_an_unreviewed_target_name_is_refused(self) -> None:
        with self.assertRaises(TargetRefused) as refusal:
            resolve_target(
                _parse(
                    "--deployment-target",
                    "prod",
                    "--allow-production-write",
                    "prod",
                ),
                SYNTHETIC_DEPLOYED_ENVIRONMENT,
            )
        self.assertIn("reviewed deployment target", str(refusal.exception))

    def test_a_retired_target_cannot_be_written_to(self) -> None:
        self.assertTrue(DEPLOYMENT_TARGETS[RETIRED_TARGET].retired)
        with self.assertRaises(TargetRefused) as refusal:
            resolve_target(
                _parse(
                    "--deployment-target",
                    RETIRED_TARGET,
                    "--allow-production-write",
                    RETIRED_TARGET,
                ),
                SYNTHETIC_DEPLOYED_ENVIRONMENT,
            )
        self.assertIn("retired", str(refusal.exception))

    def test_every_required_deployed_name_is_refused_by_name_when_absent(self) -> None:
        """A refusal names the condition and never the value behind it."""

        for name in REQUIRED_DEPLOYED_ENVIRONMENT:
            with self.subTest(name=name):
                environ = dict(SYNTHETIC_DEPLOYED_ENVIRONMENT)
                secret = environ.pop(name)
                with self.assertRaises(TargetRefused) as refusal:
                    resolve_target(
                        _parse(
                            "--deployment-target",
                            PRODUCTION_TARGET,
                            "--allow-production-write",
                            PRODUCTION_TARGET,
                        ),
                        environ,
                    )
                message = str(refusal.exception)
                self.assertIn(name, message)
                self.assertNotIn(secret, message)

    def test_a_complete_selection_resolves_the_deployed_task_contract(self) -> None:
        """The import runs with the environment the deployed task runs with."""

        target = resolve_target(
            _parse(
                "--deployment-target",
                PRODUCTION_TARGET,
                "--allow-production-write",
                PRODUCTION_TARGET,
            ),
            SYNTHETIC_DEPLOYED_ENVIRONMENT,
        )

        self.assertTrue(target.deployed)
        self.assertEqual(
            dict(target.forced),
            dict(DEPLOYMENT_TARGETS[PRODUCTION_TARGET].fixed_nonsecret_environment),
        )
        self.assertEqual(target.forced["DJANGO_SETTINGS_MODULE"], "website.settings.production")
        # The deployed Datamailer client is inert, so an import cannot email anyone.
        self.assertEqual(target.forced["DATAMAILER_URL"], "")
        self.assertEqual(target.cleared, ("DTC_SQLITE_PATH",))
        for name in SYNTHETIC_DEPLOYED_ENVIRONMENT:
            self.assertNotIn(name, target.forced)


class ProductionSettingsResolutionTests(SimpleTestCase):
    """The production route reaches PostgreSQL settings without dialling anything."""

    PROGRAM = """
import json, os, sys, argparse
sys.path.insert(0, os.getcwd())
from scripts.prod.target import add_target_arguments, configure_target

parser = argparse.ArgumentParser()
add_target_arguments(parser)
configure_target(parser, parser.parse_args(sys.argv[1:]))

from django.conf import settings
from django.db import connection

print(json.dumps({
    "engine": settings.DATABASES["default"]["ENGINE"],
    "environment": settings.ENVIRONMENT,
    "settings_module": os.environ["DJANGO_SETTINGS_MODULE"],
    "sqlite_path_present": "DTC_SQLITE_PATH" in os.environ,
    "connection_opened": connection.connection is not None,
}))
"""

    def _run(self, *argv: str, extra_environment: dict[str, str] | None = None):
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(settings.BASE_DIR / ".tmp"),
            # A stale rehearsal path must not survive into a deployed run.
            "DTC_SQLITE_PATH": str(settings.BASE_DIR / ".tmp" / "stale-rehearsal.sqlite3"),
            **SYNTHETIC_DEPLOYED_ENVIRONMENT,
            **(extra_environment or {}),
        }
        return subprocess.run(
            [sys.executable, "-c", self.PROGRAM, *argv],
            cwd=settings.BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_production_settings_resolve_to_postgresql_without_connecting(self) -> None:
        completed = self._run(
            "--deployment-target",
            PRODUCTION_TARGET,
            "--allow-production-write",
            PRODUCTION_TARGET,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        resolved = json.loads(completed.stdout)
        self.assertEqual(resolved["engine"], "django.db.backends.postgresql")
        self.assertEqual(resolved["environment"], "production")
        self.assertEqual(resolved["settings_module"], "website.settings.production")
        self.assertFalse(resolved["sqlite_path_present"])
        self.assertFalse(resolved["connection_opened"])

    def test_the_same_command_without_the_opt_in_exits_two_and_writes_nothing(self) -> None:
        completed = self._run("--deployment-target", PRODUCTION_TARGET)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn(f"--allow-production-write {PRODUCTION_TARGET}", completed.stderr)
        self.assertNotIn(SYNTHETIC_DEPLOYED_ENVIRONMENT["DATABASE_URL"], completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


class SharedSelectionTests(SimpleTestCase):
    """Thirteen copies of one four-line function is how this drifted."""

    def test_no_entry_point_configures_its_own_database(self) -> None:
        for module in pkgutil.iter_modules([str(PROD_ROOT)]):
            if module.ispkg:
                continue
            with self.subTest(module=module.name):
                source = (PROD_ROOT / f"{module.name}.py").read_text(encoding="utf-8")
                self.assertNotIn("DTC_SQLITE_PATH", source)
                self.assertNotIn('os.environ["DTC_ENVIRONMENT"]', source)
                self.assertIn("from scripts.prod.target import", source)
