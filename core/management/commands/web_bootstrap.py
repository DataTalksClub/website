"""Run deployment checks and migrations in one process with a bounded timeout."""

from __future__ import annotations

from django.core.management import BaseCommand, call_command
from django.core.management.base import CommandError
from django.db import connections

MIGRATION_STATEMENT_TIMEOUT = "120000"


def _migration_options(options: object) -> str:
    """Append the migration timeout while preserving URL-supplied options."""

    if options is None:
        options = ""
    if not isinstance(options, str):
        raise CommandError("PostgreSQL migration options must be a string.")
    return f"{options} -c statement_timeout={MIGRATION_STATEMENT_TIMEOUT}".strip()


class Command(BaseCommand):
    help = "Validate the deploy database and apply migrations before web startup"
    requires_system_checks = []

    def add_arguments(self, parser) -> None:
        parser.add_argument("--noinput", action="store_true")

    def handle(self, *args, **options) -> None:
        del args

        # Run Django checks once. The called management commands skip their
        # own system checks so startup does not repeat this Django bootstrap.
        call_command("check", stdout=self.stdout, stderr=self.stderr)
        call_command("db_preflight", skip_checks=True, stdout=self.stdout, stderr=self.stderr)

        database = connections["default"]
        if database.vendor != "postgresql":
            raise CommandError("Deploy migrations require PostgreSQL.")

        database_options = database.settings_dict.setdefault("OPTIONS", {})
        if not isinstance(database_options, dict):
            raise CommandError("PostgreSQL database options must be a mapping.")
        database_options["options"] = _migration_options(database_options.get("options"))
        # db_preflight opened the connection with the ordinary serving timeout.
        # Reconnect so PostgreSQL receives the migration-only option string.
        database.close()

        call_command(
            "migrate",
            noinput=options["noinput"],
            skip_checks=True,
            stdout=self.stdout,
            stderr=self.stderr,
        )
