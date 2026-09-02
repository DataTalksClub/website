from __future__ import annotations

import argparse

from django.test import SimpleTestCase

from deploy.cli import _add_runtime_arguments
from deploy.deployment_targets import DEPLOYMENT_TARGETS, SELECTED_TARGET
from deploy.development_target import (
    DEFAULT_DEVELOPMENT_HOSTNAME,
    DEVELOPMENT_HOSTNAME_VARIABLE,
    PERMITTED_DEVELOPMENT_HOSTNAMES,
    DevelopmentTargetError,
    development_hostname,
    development_origin,
)
from deploy.task_definitions import FIXED_NONSECRET_ENVIRONMENT
from test_support.safety import REMOTE_HOSTS


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
    def test_remote_test_allowlist_covers_exactly_the_reviewed_hostnames(self) -> None:
        deployable = {
            target.hostname for target in DEPLOYMENT_TARGETS.values() if not target.retired
        }
        self.assertEqual(REMOTE_HOSTS, PERMITTED_DEVELOPMENT_HOSTNAMES | deployable)
        self.assertIn(SELECTED_TARGET.hostname, REMOTE_HOSTS)

    def test_task_definition_environment_follows_the_selected_deployment_target(self) -> None:
        self.assertEqual(
            FIXED_NONSECRET_ENVIRONMENT["DJANGO_ALLOWED_HOSTS"],
            SELECTED_TARGET.hostname,
        )
        self.assertEqual(
            FIXED_NONSECRET_ENVIRONMENT["DJANGO_CSRF_TRUSTED_ORIGINS"],
            SELECTED_TARGET.origin,
        )
        self.assertEqual(
            FIXED_NONSECRET_ENVIRONMENT["DJANGO_SETTINGS_MODULE"],
            SELECTED_TARGET.settings_module,
        )
        # A non-indexable staging or development host still consolidates to the
        # production apex, so this is the apex and not the served origin.
        self.assertEqual(
            FIXED_NONSECRET_ENVIRONMENT["CANONICAL_ORIGIN"],
            "https://datatalks.club",
        )

    def test_release_smoke_default_follows_the_selected_deployment_target(self) -> None:
        parser = argparse.ArgumentParser()
        _add_runtime_arguments(parser)
        self.assertEqual(parser.get_default("base_url"), SELECTED_TARGET.origin)

    def test_a_development_hostname_is_never_the_deployed_target_by_accident(self) -> None:
        # The development hostnames are Django-settings configuration.  Deploying
        # to one of them requires a reviewed deployment target that names it.
        if SELECTED_TARGET.hostname in PERMITTED_DEVELOPMENT_HOSTNAMES:
            self.assertEqual(SELECTED_TARGET.settings_module, "website.settings.development")
        else:
            self.assertNotEqual(SELECTED_TARGET.settings_module, "website.settings.development")
