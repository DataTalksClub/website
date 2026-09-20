from __future__ import annotations

import ast
import hashlib
import unittest
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader


class MigrationContractError(ValueError):
    pass


APPLICATION_ROOTS = frozenset(
    {
        "accounts",
        "content",
        "core",
        "courses",
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


class MigrationWindowTestCase(unittest.TestCase):
    """Exercise a data migration on a private database it migrates itself.

    A migration that moves values between a user column and an extension row
    cannot be exercised through the live models for long: the contract phase
    drops the column, and from then on no database in the suite has both ends
    of the copy. Rather than lose the coverage exactly when the values become
    unrecoverable, a test of this kind builds its own database, migrates it to
    the node before the one under test, seeds rows through that node's
    historical models, and then migrates forward -- so the same test keeps
    meaning on every branch of the move.

    ``migrate`` returns the historical app registry for the node it stopped at;
    read and write rows through that, never through the live models.
    """

    def setUp(self) -> None:
        super().setUp()
        digest = hashlib.sha256(self.id().encode("utf-8")).hexdigest()[:12]
        self.worker_id = f"migration-window-{digest}"
        layout = settings.TEST_RUNTIME.worker(self.worker_id)
        database_path = settings.TEST_RUNTIME.assert_database_path(
            layout.database,
            worker_id=self.worker_id,
        )
        self.connection = connections["default"]
        self.connection.close()
        self._restore = (
            self.connection.settings_dict["NAME"],
            self.connection.settings_dict["TEST"],
            self.connection.settings_dict["DTC_WORKER_ID"],
        )
        self.connection.settings_dict["NAME"] = database_path
        self.connection.settings_dict["TEST"] = {"NAME": database_path}
        self.connection.settings_dict["DTC_WORKER_ID"] = self.worker_id

    def tearDown(self) -> None:
        self.connection.close()
        name, test, worker_id = self._restore
        self.connection.settings_dict["NAME"] = name
        self.connection.settings_dict["TEST"] = test
        self.connection.settings_dict["DTC_WORKER_ID"] = worker_id
        self.connection.connect()
        super().tearDown()

    def migrate(self, *targets: tuple[str, str]):
        """Migrate to exactly ``targets`` and return that state's app registry.

        Naming both ends of the move pins each app where the test needs it:
        migrating the extension app forward while holding ``accounts`` before
        its contract migration is what keeps the user columns in the schema.
        """

        MigrationExecutor(self.connection).migrate(list(targets))
        return MigrationLoader(self.connection).project_state(list(targets)).apps
