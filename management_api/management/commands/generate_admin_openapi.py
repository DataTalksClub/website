from django.core.management.base import BaseCommand, CommandError

from management_api.openapi import SCHEMA_PATH, render_document


class Command(BaseCommand):
    help = "Generate or verify the isolated management OpenAPI 3.1 document."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--check", action="store_true")

    def handle(self, *args, **options) -> None:
        del args
        rendered = render_document()
        if options["check"]:
            if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text(encoding="utf-8") != rendered:
                raise CommandError("management OpenAPI document is stale")
            self.stdout.write("Management OpenAPI document is current.")
            return
        SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_text(rendered, encoding="utf-8")
        self.stdout.write(f"Wrote {SCHEMA_PATH}")
