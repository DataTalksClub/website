"""Move a site project between states by hand using community_base.coursework.project_state."""

from community_base.coursework.project_state import (
    ProjectStateError,
    parse_state,
    set_project_state,
)
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from courses.models.project import Project, ProjectState, project_yaml_criteria


def move_project(
    course_slug: str,
    project_slug: str,
    state: str,
    *,
    cohort_slug: str | None = None,
):
    qs = Project.objects.select_related("course", "course__course").filter(
        Q(course__slug=course_slug) | Q(course__course__slug=course_slug),
        slug=project_slug,
    )
    if cohort_slug:
        qs = qs.filter(Q(course__identifier=cohort_slug) | Q(course__slug=cohort_slug))
    matches = list(qs[:2])
    if not matches:
        raise CommandError(f'Project "{project_slug}" not found in course "{course_slug}".')
    if len(matches) > 1:
        raise CommandError(
            f'Multiple cohorts have project "{project_slug}" in "{course_slug}"; pass --cohort.'
        )
    project = matches[0]
    try:
        target_state = parse_state(state)
        if (
            target_state.value == ProjectState.PEER_REVIEWING.value
            and not project_yaml_criteria(project)
            and not project.criteria_for_project()
        ):
            raise CommandError(f'Project "{project.slug}" has no review criteria set assigned.')
        previous = set_project_state(project, target_state)
    except ProjectStateError as error:
        raise CommandError(str(error)) from error
    return project, previous


class Command(BaseCommand):
    help = "Set a project state (collecting_submissions, peer_reviewing, completed, closed)."

    def add_arguments(self, parser):
        parser.add_argument("course_slug")
        parser.add_argument("project_slug")
        parser.add_argument("state")
        parser.add_argument("--cohort", default=None, help="Optional cohort identifier or slug.")

    def handle(self, *args, **options):
        project, previous = move_project(
            options["course_slug"],
            options["project_slug"],
            options["state"],
            cohort_slug=options["cohort"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"{project.slug}: {previous} -> {project.state} ({project.get_state_display()})"
            )
        )
