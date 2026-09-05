from django.core.management import call_command
from django.core.management.base import BaseCommand

from events.identity import import_identity_manifest


class Command(BaseCommand):
    help = "Apply database migrations and load required code-owned data"

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        call_command("migrate", interactive=False)
        report = import_identity_manifest()
        self.stdout.write(
            self.style.SUCCESS(
                "Prepared deployment: "
                f"{report.event_total} event identities, {report.alias_total} aliases"
            )
        )
