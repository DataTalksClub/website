from django.core.management.base import BaseCommand

from accounts.studio_roles import synchronize_studio_roles


class Command(BaseCommand):
    help = "Synchronize the code-owned Studio role manifest"

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        groups = synchronize_studio_roles()
        self.stdout.write(self.style.SUCCESS(f"Synchronized {len(groups)} Studio roles"))
