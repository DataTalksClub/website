from collections.abc import Callable

from django.http import HttpResponse
from django.urls import URLPattern, path

from .views import (
    campaigns,
    course_admin,
    datamailer,
    enrollment,
    homework,
    observability,
    projects,
)

RouteDefinition = tuple[str, Callable[..., HttpResponse], str]

ROUTE_DEFINITIONS: tuple[RouteDefinition, ...] = (
    ("", course_admin.course_list, "course_list"),
    ("campaigns/new/", campaigns.campaign_create, "campaign_create"),
    (
        "campaigns/<slug:campaign_slug>/edit/",
        campaigns.campaign_edit,
        "campaign_edit",
    ),
    (
        "registrations/<slug:campaign_slug>/",
        campaigns.campaign_registrations,
        "campaign_registrations",
    ),
    ("datamailer/", datamailer.datamailer_operations, "datamailer_operations"),
    ("datamailer/events/", datamailer.datamailer_events, "datamailer_events"),
    (
        "cloudwatch/",
        observability.cloudwatch_dashboard,
        "cloudwatch_dashboard",
    ),
    ("<slug:course_slug>/", course_admin.course_admin, "course"),
    (
        "<slug:course_slug>/homework/<slug:homework_slug>/score",
        homework.homework_score,
        "homework_score",
    ),
    (
        "<slug:course_slug>/homework/<slug:homework_slug>/rescore",
        homework.homework_rescore,
        "homework_rescore",
    ),
    (
        "<slug:course_slug>/homework/<slug:homework_slug>/extend-deadline",
        homework.homework_extend_deadline,
        "homework_extend_deadline",
    ),
    (
        "<slug:course_slug>/homework/<slug:homework_slug>/notify-scores",
        homework.homework_notify_scores,
        "homework_notify_scores",
    ),
    (
        "<slug:course_slug>/homework/<slug:homework_slug>/save-answers",
        homework.homework_save_answers,
        "homework_save_answers",
    ),
    (
        "<slug:course_slug>/homework/<slug:homework_slug>/set-correct-answers",
        homework.homework_set_correct_answers,
        "homework_set_correct_answers",
    ),
    (
        "<slug:course_slug>/homework/<slug:homework_slug>/clear-correct-answers",
        homework.homework_clear_correct_answers,
        "homework_clear_correct_answers",
    ),
    (
        "<slug:course_slug>/homework/<slug:homework_slug>/submissions",
        homework.homework_submissions,
        "homework_submissions",
    ),
    (
        "<slug:course_slug>/homework/<slug:homework_slug>/submissions/<int:submission_id>/edit",
        homework.homework_submission_edit,
        "homework_submission_edit",
    ),
    (
        "<slug:course_slug>/project/<slug:project_slug>/assign-reviews",
        projects.project_assign_reviews,
        "project_assign_reviews",
    ),
    (
        "<slug:course_slug>/project/<slug:project_slug>/extend-deadline",
        projects.project_extend_deadline,
        "project_extend_deadline",
    ),
    (
        "<slug:course_slug>/project/<slug:project_slug>/score",
        projects.project_score,
        "project_score",
    ),
    (
        "<slug:course_slug>/project/<slug:project_slug>/submissions",
        projects.project_submissions,
        "project_submissions",
    ),
    (
        "<slug:course_slug>/project/<slug:project_slug>/submissions/<int:submission_id>/edit",
        projects.project_submission_edit,
        "project_submission_edit",
    ),
    (
        "<slug:course_slug>/enrollments/",
        enrollment.enrollments_list,
        "enrollments",
    ),
    (
        "<slug:course_slug>/leaderboard-complaints/",
        enrollment.leaderboard_complaints,
        "leaderboard_complaints",
    ),
    (
        "<slug:course_slug>/leaderboard-complaints/<int:complaint_id>/resolve",
        enrollment.leaderboard_complaint_resolve,
        "leaderboard_complaint_resolve",
    ),
    (
        "<slug:course_slug>/enrollment/<int:enrollment_id>/edit",
        enrollment.enrollment_edit,
        "enrollment_edit",
    ),
)


def _patterns(name_prefix: str) -> list[URLPattern]:
    return [
        path(route, view, name=f"{name_prefix}{name}") for route, view, name in ROUTE_DEFINITIONS
    ]


# The copied operation inventory keeps its implementation in this package, while every
# route name exposed by the unified platform uses the Studio Courses product language.
urlpatterns = _patterns("studio_courses_")
