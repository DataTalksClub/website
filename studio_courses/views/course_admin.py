from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from courses.models.cohort import (
    Cohort,
    Enrollment,
    LeaderboardComplaint,
    RegistrationCampaign,
)
from courses.models.homework import (
    Homework,
    HomeworkState,
)
from courses.models.project import (
    Project,
    ProjectState,
)
from studio_courses.deadline_extension import (
    EXTENSION_OPTIONS,
    project_extension_plan,
)
from .campaign_metrics import registration_campaign_metrics
from .helpers import staff_required


@staff_required
def course_list(request):
    """List all courses with admin actions"""
    courses = Cohort.objects.all().order_by("finished", "-id")

    context = {
        "courses": courses,
    }

    response = render(request, "studio_courses/course_list.html", context)
    return response


def course_homeworks_for_admin(course):
    homework_queryset = (
        Homework.objects.filter(course=course)
        .annotate(submissions_count=Count("submission"))
        .order_by("due_date")
    )
    homeworks = list(homework_queryset)
    for homework in homeworks:
        homework.can_score = homework.state in [
            HomeworkState.OPEN.value,
            HomeworkState.CLOSED.value,
        ]
        homework.can_notify_scores = (
            homework.state == HomeworkState.SCORED.value
        )
        homework.can_extend_deadline = (
            homework.state == HomeworkState.OPEN.value
        )
    return homeworks


def course_projects_for_admin(course):
    project_queryset = (
        Project.objects.filter(course=course)
        .annotate(submissions_count=Count("projectsubmission"))
        .order_by("id")
    )
    projects = list(project_queryset)
    for project in projects:
        project.needs_review_assignment = (
            project.state == ProjectState.COLLECTING_SUBMISSIONS.value
        )
        project.needs_scoring = (
            project.state == ProjectState.PEER_REVIEWING.value
        )
        extension_fields, extension_label = project_extension_plan(
            project
        )
        project.can_extend_deadline = extension_fields is not None
        project.extend_deadline_label = extension_label
    return projects


def course_support_metrics(course):
    enrollments = Enrollment.objects.filter(course=course)
    disabled_lip = enrollments.filter(
        disable_learning_in_public=True
    ).count()
    zero_score = enrollments.filter(total_score=0).count()
    hidden_leaderboard = enrollments.filter(
        display_on_leaderboard=False
    ).count()
    open_complaints = LeaderboardComplaint.objects.filter(
        enrollment__course=course,
        resolved=False,
    ).count()
    return {
        "disabled_lip": disabled_lip,
        "zero_score": zero_score,
        "hidden_leaderboard": hidden_leaderboard,
        "open_complaints": open_complaints,
    }


def course_registration_metrics(course):
    campaigns = (
        RegistrationCampaign.objects.filter(current_course=course)
        .select_related("current_course")
        .order_by("title", "slug")
    )
    registration_metrics = []
    primary_campaign = None
    for campaign in campaigns:
        if primary_campaign is None:
            primary_campaign = campaign
        metric = registration_campaign_metrics(campaign)
        registration_metrics.append(metric)

    return {
        "registration_metrics": registration_metrics,
        "primary_campaign": primary_campaign,
    }


def course_admin_context(course):
    from course_management.relay_campaigns import get_campaign_status
    from courses.models import Announcement

    context = course_registration_metrics(course)
    homeworks = course_homeworks_for_admin(course)
    projects = course_projects_for_admin(course)
    total_enrollments = course.enrollment_set.count()
    support_metrics = course_support_metrics(course)
    announcements = Announcement.objects.filter(
        cohort=course,
        channel="email",
        status__in=("submitted", "sent"),
    ).order_by("-id")[:10]
    announcement_relay_statuses = []
    for announcement in announcements:
        relay_status = get_campaign_status(f"announcement-{announcement.pk}")
        if relay_status is not None:
            announcement_relay_statuses.append(
                {
                    "announcement": announcement,
                    **relay_status,
                }
            )
    context.update(
        {
            "course": course,
            "homeworks": homeworks,
            "projects": projects,
            "total_enrollments": total_enrollments,
            "support_metrics": support_metrics,
            "extension_options": EXTENSION_OPTIONS,
            "announcement_relay_statuses": announcement_relay_statuses,
        }
    )
    return context


@staff_required
def course_admin(request, course_slug):
    """Admin panel for a specific course"""
    course = get_object_or_404(Cohort, slug=course_slug)
    context = course_admin_context(course)
    response = render(
        request,
        "studio_courses/course_admin.html",
        context,
    )
    return response
