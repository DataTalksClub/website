from __future__ import annotations

import argparse

from django.test import SimpleTestCase

from deploy.cli import _add_runtime_arguments
from deploy.development_target import (
    DEFAULT_DEVELOPMENT_HOSTNAME,
    DEVELOPMENT_HOSTNAME_VARIABLE,
    PERMITTED_DEVELOPMENT_HOSTNAMES,
    DevelopmentTargetError,
    development_hostname,
    development_origin,
)
from deploy.task_definitions import FIXED_NONSECRET_ENVIRONMENT
from test_support.safety import DEVELOPMENT_HOSTS


class DevelopmentTargetSelectionTests(SimpleTestCase):
    def test_unset_configuration_keeps_the_current_deployment_hostname(self) -> None:
        self.assertEqual(development_hostname({}), DEFAULT_DEVELOPMENT_HOSTNAME)
        self.assertEqual(development_origin({}), f"https://{DEFAULT_DEVELOPMENT_HOSTNAME}")

    def test_every_reviewed_hostname_is_selectable_by_configuration_alone(self) -> None:
        for hostname in sorted(PERMITTED_DEVELOPMENT_HOSTNAMES):
            with self.subTest(hostname=hostname):
                environ = {DEVELOPMENT_HOSTNAME_VARIABLE: hostname}
                self.assertEqual(development_hostname(environ), hostname)
                self.assertEqual(development_origin(environ), f"https://{hostname}")

    def test_an_unreviewed_hostname_fails_closed(self) -> None:
        for value in (
            "datatalks.club",
            "example.invalid",
            "https://dev.datatalks.club",
            "dev.datatalks.club:8443",
            "DEV.DATATALKS.CLUB",
        ):
            with self.subTest(value=value), self.assertRaises(DevelopmentTargetError):
                development_hostname({DEVELOPMENT_HOSTNAME_VARIABLE: value})

    def test_the_production_apex_is_never_a_development_target(self) -> None:
        self.assertNotIn("datatalks.club", PERMITTED_DEVELOPMENT_HOSTNAMES)


class DevelopmentTargetCoherenceTests(SimpleTestCase):
    def test_remote_test_allowlist_matches_the_reviewed_hostnames(self) -> None:
        self.assertEqual(DEVELOPMENT_HOSTS, PERMITTED_DEVELOPMENT_HOSTNAMES)

    def test_task_definition_environment_follows_the_selected_hostname(self) -> None:
        self.assertEqual(
            FIXED_NONSECRET_ENVIRONMENT["DJANGO_ALLOWED_HOSTS"],
            DEFAULT_DEVELOPMENT_HOSTNAME,
        )
        self.assertEqual(
            FIXED_NONSECRET_ENVIRONMENT["DJANGO_CSRF_TRUSTED_ORIGINS"],
            f"https://{DEFAULT_DEVELOPMENT_HOSTNAME}",
        )
        self.assertEqual(
            FIXED_NONSECRET_ENVIRONMENT["CANONICAL_ORIGIN"],
            "https://datatalks.club",
        )

    def test_release_smoke_default_follows_the_selected_hostname(self) -> None:
        parser = argparse.ArgumentParser()
        _add_runtime_arguments(parser)
        self.assertEqual(
            parser.get_default("base_url"),
            f"https://{DEFAULT_DEVELOPMENT_HOSTNAME}",
        )
