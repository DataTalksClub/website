from django.contrib import admin, messages
from unfold.admin import ModelAdmin

from courses.assignment_statistics import calculate_project_statistics
from courses.models.project import (
    Project,
    ProjectCriteriaAssignment,
    ProjectState,
    ReviewCriteria,
)
from courses.project_assignment import (
    ProjectActionStatus,
    assign_peer_reviews_for_project,
)
from courses.scoring_jobs import enqueue_project_notify, enqueue_project_score


def assign_peer_reviews_for_project_admin(
    modeladmin, request, queryset
):
    for project in queryset:
        status, message = assign_peer_reviews_for_project(project)
        if status == ProjectActionStatus.OK:
            modeladmin.message_user(
                request, message, level=messages.SUCCESS
            )
        else:
            modeladmin.message_user(
                request, message, level=messages.WARNING
            )


assign_peer_reviews_for_project_admin.short_description = (
    "Assign peer reviews"
)


def score_projects_admin(modeladmin, request, queryset):
    from django.db import transaction

    count = 0
    for project in queryset:
        with transaction.atomic():
            enqueue_project_score(project.id)
            enqueue_project_notify(project.id)
        count += 1
    modeladmin.message_user(
        request,
        f"Scoring queued for {count} project(s). Track progress in Admin > Job intents.",
        level=messages.SUCCESS,
    )


score_projects_admin.short_description = "Score projects"


def calculate_statistics_selected_projects(
    modeladmin, request, queryset
):
    for project in queryset:
        if project.state != ProjectState.COMPLETED.value:
            modeladmin.message_user(
                request,
                f"Cannot calculate statistics for {project} "
                "because it has not been completed",
                level=messages.WARNING,
            )
            continue

        calculate_project_statistics(project, force=True)

        message = f"Statistics calculated for {project}"
        modeladmin.message_user(
            request, message, level=messages.SUCCESS
        )


calculate_statistics_selected_projects.short_description = (
    "Calculate statistics"
)


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    actions = [
        assign_peer_reviews_for_project_admin,
        score_projects_admin,
        calculate_statistics_selected_projects,
    ]

    list_display = ["title", "course", "state"]
    list_filter = ["course__slug"]


admin.site.register(ReviewCriteria, ModelAdmin)
admin.site.register(ProjectCriteriaAssignment, ModelAdmin)
