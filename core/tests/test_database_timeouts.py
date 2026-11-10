"""Deployed PostgreSQL connections have bounded connection and statement waits."""

from pathlib import Path
from typing import Any

from django.test import SimpleTestCase

from core.bootstrap import RuntimeEnvironment, database_configuration


class DatabaseTimeoutDefaultsTests(SimpleTestCase):
    database_url = "postgresql://user:password@db.example.invalid/site"

    def database(
        self,
        environment: RuntimeEnvironment,
        database_url: str | None = None,
    ) -> dict[str, Any]:
        return database_configuration(
            environment=environment,
            database_url=database_url or self.database_url,
        )

    def test_deployed_postgresql_settings_have_bounded_timeouts(self) -> None:
        for environment in (RuntimeEnvironment.DEVELOPMENT, RuntimeEnvironment.PRODUCTION):
            with self.subTest(environment=environment):
                options = self.database(environment)["OPTIONS"]

                self.assertEqual(options["connect_timeout"], 5)
                self.assertEqual(
                    options["options"],
                    "-c statement_timeout=30000 "
                    "-c lock_timeout=5000 "
                    "-c idle_in_transaction_session_timeout=60000",
                )

    def test_explicit_postgresql_timeouts_are_preserved(self) -> None:
        database = self.database(
            RuntimeEnvironment.PRODUCTION,
            "postgresql://user:password@db.example.invalid/site"
            "?connect_timeout=9&options=-c%20statement_timeout%3D60000",
        )

        self.assertEqual(database["OPTIONS"]["connect_timeout"], 9)
        self.assertEqual(database["OPTIONS"]["options"], "-c statement_timeout=60000")

    def test_local_sqlite_settings_do_not_receive_postgresql_options(self) -> None:
        database = database_configuration(
            environment=RuntimeEnvironment.LOCAL,
            database_url=None,
            sqlite_fallback=Path(".tmp/local.sqlite3"),
        )

        self.assertEqual(
            database,
            {"ENGINE": "django.db.backends.sqlite3", "NAME": Path(".tmp/local.sqlite3")},
        )
