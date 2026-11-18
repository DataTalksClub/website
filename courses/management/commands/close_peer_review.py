"""Complete a project (COMPLETED): results and feedback become visible."""

from django.core.management.base import BaseCommand

from courses.management.commands.set_project_state import move_project


class Command(BaseCommand):
    help = "Close peer review for a project (move it to Completed)."

    def add_arguments(self, parser):
        parser.add_argument("course_slug")
        parser.add_argument("project_slug")
        parser.add_argument("--cohort", default=None, help="Optional cohort identifier or slug.")

    def handle(self, *args, **options):
        project, previous = move_project(
            options["course_slug"],
            options["project_slug"],
            "completed",
            cohort_slug=options["cohort"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"{project.slug}: peer review closed ({previous} -> {project.state})"
            )
        )
