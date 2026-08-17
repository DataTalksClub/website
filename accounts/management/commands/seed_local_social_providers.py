from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from accounts.services.local_provider_seed import (
    LocalProviderSeedError,
    assert_local_database,
    assert_providers_installed,
    seed_local_social_providers,
)


class Command(BaseCommand):
    """Seed placeholder social sign-in apps so the entrance pages draw their buttons.

    The credentials this writes are obviously fake placeholders, never secrets:
    they are checked into the repository, they authenticate against nothing, and
    following a seeded button fails at the real provider.  The command refuses to
    run against anything but a local or test SQLite database, so a placeholder
    cannot reach a deployed one.
    """

    help = (
        "Seed the local development database with placeholder Google, GitHub and "
        "Slack sign-in apps so /accounts/signup/ and /accounts/login/ render their "
        "provider buttons. The credentials are inert placeholders, not secrets."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate the environment and the installed providers without writing rows.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            if options["check"]:
                assert_local_database()
                assert_providers_installed()
                summary: dict[str, object] = {"checked": True, "written": False}
            else:
                summary = {**seed_local_social_providers().summary(), "written": True}
        except LocalProviderSeedError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(summary, sort_keys=True))
