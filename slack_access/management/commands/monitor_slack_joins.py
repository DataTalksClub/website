"""Read-only provider polling; output contains counts, never profile data."""

from django.core.management.base import BaseCommand, CommandError

from slack_access.monitoring import poll_joins
from slack_access.slack_api import SlackReadError


class Command(BaseCommand):
    help = "Record Slack joins for the website approval report; never modify Slack."

    def handle(self, *args, **options):
        try:
            counts = poll_joins()
        except SlackReadError as error:
            raise CommandError(f"Slack monitoring failed: {error}") from None
        self.stdout.write(" ".join(f"{key}={value}" for key, value in sorted(counts.items())))
