from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Apply database migrations"

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        call_command("migrate", interactive=False)
        self.stdout.write(self.style.SUCCESS("Prepared deployment: migrations applied"))
