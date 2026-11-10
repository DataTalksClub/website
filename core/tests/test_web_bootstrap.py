from __future__ import annotations

from unittest.mock import call, patch

from django.core.management import call_command as real_call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from core.management.commands.web_bootstrap import Command, _migration_options


class WebBootstrapTests(SimpleTestCase):
    def test_migration_timeout_overrides_url_timeout_and_preserves_other_options(self) -> None:
        self.assertEqual(
            _migration_options(
                "-c statement_timeout=30000 -c lock_timeout=5000 "
                "-c idle_in_transaction_session_timeout=60000"
            ),
            "-c statement_timeout=30000 -c lock_timeout=5000 "
            "-c idle_in_transaction_session_timeout=60000 -c statement_timeout=120000",
        )

    @patch("core.management.commands.web_bootstrap.call_command")
    @patch("core.management.commands.web_bootstrap.connections")
    def test_preflight_then_migration_use_one_connection_configuration(
        self,
        connections,
        call_command,
    ) -> None:
        database = connections.__getitem__.return_value
        database.vendor = "postgresql"
        database.settings_dict = {
            "OPTIONS": {"options": "-c statement_timeout=30000 -c lock_timeout=5000"}
        }
        command = Command()

        command.handle(noinput=True)

        self.assertEqual(
            [item.args[0] for item in call_command.call_args_list],
            ["check", "db_preflight", "migrate"],
        )
        self.assertEqual(
            database.settings_dict["OPTIONS"]["options"],
            "-c statement_timeout=30000 -c lock_timeout=5000 -c statement_timeout=120000",
        )
        database.close.assert_called_once_with()
        self.assertEqual(
            call_command.call_args_list[-1],
            call(
                "migrate",
                interactive=False,
                skip_checks=True,
                stdout=command.stdout,
                stderr=command.stderr,
            ),
        )

    @patch("core.management.commands.web_bootstrap.call_command")
    @patch("core.management.commands.web_bootstrap.connections")
    def test_preflight_failure_stops_before_migration(self, connections, call_command) -> None:
        database = connections.__getitem__.return_value
        database.vendor = "postgresql"
        database.settings_dict = {"OPTIONS": {}}
        call_command.side_effect = [None, CommandError("preflight failed")]

        with self.assertRaisesMessage(CommandError, "preflight failed"):
            Command().handle(noinput=True)

        self.assertEqual(
            [item.args[0] for item in call_command.call_args_list],
            ["check", "db_preflight"],
        )

    @patch("django.core.management.commands.migrate.Command.handle", return_value="")
    @patch("core.management.commands.web_bootstrap.connections")
    def test_migrate_accepts_the_options_bootstrap_passes(self, connections, migrate) -> None:
        database = connections.__getitem__.return_value
        database.vendor = "postgresql"
        database.settings_dict = {"OPTIONS": {}}

        def dispatch(name, *args, **kwargs):
            # Only migrate runs for real, so Django validates its option names.
            if name == "migrate":
                return real_call_command(name, *args, **kwargs)
            return None

        with patch("core.management.commands.web_bootstrap.call_command", side_effect=dispatch):
            Command().handle(noinput=True)

        migrate.assert_called_once()
        self.assertFalse(migrate.call_args.kwargs["interactive"])
