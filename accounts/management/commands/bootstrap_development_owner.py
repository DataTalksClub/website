from __future__ import annotations

import getpass
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.development_owner import (
    DevelopmentOwnerBootstrapDenied,
    bootstrap_development_owner,
    development_owner_exists,
)
from core.bootstrap import RuntimeEnvironment


class Command(BaseCommand):
    help = "Interactively bootstrap the single development owner without echoing secrets."

    def handle(self, *args, **options) -> None:
        del args, options
        if settings.RUNTIME_ENVIRONMENT is not RuntimeEnvironment.DEVELOPMENT:
            raise CommandError("bootstrap_development_owner: environment_denied")
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise CommandError("bootstrap_development_owner: noninteractive_denied")

        email = input("Owner email: ")
        confirmation = input("Confirm owner email: ")
        if email.strip().casefold() != confirmation.strip().casefold():
            raise CommandError("bootstrap_development_owner: confirmation_denied")

        reset_password = False
        if development_owner_exists():
            reset_password = input("Reset the existing owner password? [y/N]: ") == "y"
            if not reset_password:
                password = None
            else:
                password = self._password()
        else:
            if input("Create or reset this exact development identity? [y/N]: ") != "y":
                raise CommandError("bootstrap_development_owner: confirmation_denied")
            reset_password = True
            password = self._password()

        try:
            result = bootstrap_development_owner(
                email=email,
                password=password,
                reset_password=reset_password,
            )
        except DevelopmentOwnerBootstrapDenied as error:
            raise CommandError(f"bootstrap_development_owner: {error.category}") from None
        self.stdout.write(
            "bootstrap_development_owner: "
            f"{result.category} users={result.users} "
            f"human_principals={result.human_principals} "
            f"service_principals={result.service_principals} "
            f"revoked_sessions={result.revoked_staff_sessions} "
            f"revoked_human_credentials={result.revoked_human_credentials}"
        )

    @staticmethod
    def _password() -> str:
        password = getpass.getpass("New development password: ")
        confirmation = getpass.getpass("Confirm new development password: ")
        if password != confirmation:
            raise CommandError("bootstrap_development_owner: password_confirmation_denied")
        return password
