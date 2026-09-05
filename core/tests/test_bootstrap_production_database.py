"""Input refusals of the production database bootstrap command.

The command's later steps talk to Secrets Manager and to PostgreSQL, so the
cases here are deliberately the ones that must fail *before* either: an option
this command cannot use is a configuration mistake to report, never something
to carry into a live production role or into a stored ``DATABASE_URL``.
"""

from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

BASE_OPTIONS = {
    "master_secret_id": "dtc/production/database/master",
    "database_url_secret_id": "dtc/production/database/url",
    "django_secret_key_secret_id": "dtc/production/django-secret-key",
    "database_host": "database.production.invalid",
}


@override_settings(ENVIRONMENT="production")
class BootstrapProductionDatabasePortTests(SimpleTestCase):
    def bootstrap(self, **overrides: object) -> None:
        call_command("bootstrap_production_database", **BASE_OPTIONS, **overrides)

    def test_a_non_numeric_port_is_refused_before_any_aws_or_database_call(self) -> None:
        with (
            patch("core.management.commands.bootstrap_production_database.boto3") as boto3,
            patch("core.management.commands.bootstrap_production_database.psycopg") as psycopg,
        ):
            with self.assertRaises(CommandError) as raised:
                self.bootstrap(database_port="not-a-port")

        self.assertIn("--database-port", str(raised.exception))
        boto3.client.assert_not_called()
        psycopg.connect.assert_not_called()

    def test_a_port_outside_the_tcp_range_is_refused(self) -> None:
        for port in (0, 65_536):
            with self.subTest(port=port), self.assertRaises(CommandError):
                self.bootstrap(database_port=port)

    def test_a_port_that_is_not_a_number_at_all_is_refused(self) -> None:
        for port in (None, True, 5432.5):
            with self.subTest(port=port), self.assertRaises(CommandError):
                self.bootstrap(database_port=port)


class BootstrapProductionDatabaseEnvironmentTests(SimpleTestCase):
    def test_the_command_is_restricted_to_production(self) -> None:
        with override_settings(ENVIRONMENT="development"):
            with self.assertRaises(CommandError) as raised:
                call_command("bootstrap_production_database", **BASE_OPTIONS)

        self.assertIn("production environment", str(raised.exception))
