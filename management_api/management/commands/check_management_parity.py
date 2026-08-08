from django.core.management.base import BaseCommand, CommandError

from management_api.parity import parity_errors


class Command(BaseCommand):
    help = "Fail when the management registry, routes, services, or metadata drift."

    def handle(self, *args, **options) -> None:
        del args, options
        errors = parity_errors()
        if errors:
            raise CommandError("; ".join(errors))
        self.stdout.write("Management capability parity is current.")
