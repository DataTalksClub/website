import re

from django.urls import path

from .views import (
    course,
    course_calendar,
    course_enrollment,
    course_leaderboard,
    course_list,
    dashboard,
    homework,
    homework_statistics,
    homework_submissions,
    module,
    project,
    project_eval,
    project_eval_actions,
    project_eval_submit,
    project_results,
    project_statistics,
    project_submissions,
    registration,
    shared_course,
    site_project_gallery,
    unit,
    wrapped,
)

urlpatterns = [
    path("", course_list.course_list, name="course_list"),
    # A cohort's URL is one flat, cohorts-segment-free shape:
    # ``<family>/<cohort_identifier>/...`` (issue #320). The old
    # ``cohorts/``-prefixed shape is retired outright, with no redirect.
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/calendar.ics",
        course_calendar.course_calendar_view,
        name="cohort_calendar",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/homework/<slug:homework_slug>",
        homework.homework_view,
        name="cohort_homework",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/leaderboard",
        course_leaderboard.leaderboard_view,
        name="cohort_leaderboard",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/dashboard",
        dashboard.dashboard_view,
        name="cohort_dashboard",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/projects",
        site_project_gallery.cohort_projects_redirect,
        name="cohort_projects",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/enrollment",
        course_enrollment.enrollment_view,
        name="cohort_enrollment",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/modules/<slug:module_slug>",
        module.module_view,
        name="cohort_module",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/modules/<slug:module_slug>/<slug:unit_slug>",
        unit.unit_view,
        name="cohort_unit",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/modules/<slug:module_slug>/<slug:unit_slug>/read",
        module.update_unit_read_state,
        name="cohort_unit_read_state",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/homework/<slug:homework_slug>/stats",
        homework_statistics.homework_statistics,
        name="cohort_homework_statistics",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/homework/<slug:homework_slug>/submissions",
        homework_submissions.homework_submissions,
        name="cohort_homework_submissions",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/leaderboard/<int:enrollment_id>/",
        course_leaderboard.leaderboard_score_breakdown_view,
        name="cohort_leaderboard_score_breakdown",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/leaderboard/<int:enrollment_id>/report",
        course_leaderboard.leaderboard_complaint_view,
        name="cohort_leaderboard_complaint",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/enrollment/toggle",
        course_enrollment.update_enrollment_toggle,
        name="cohort_update_enrollment_toggle",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>",
        project.project_view,
        name="cohort_project",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/list",
        site_project_gallery.project_gallery_view,
        name="cohort_project_list",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/eval",
        project_eval.projects_eval_view,
        name="cohort_projects_eval",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/results",
        project_results.project_results,
        name="cohort_project_results",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/stats",
        project_statistics.project_statistics,
        name="cohort_project_statistics",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/submissions",
        project_submissions.project_submissions,
        name="cohort_project_submissions",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/eval/<int:review_id>",
        project_eval_submit.projects_eval_submit,
        name="cohort_projects_eval_submit",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/eval/add/<int:submission_id>",
        project_eval_actions.projects_eval_add,
        name="cohort_projects_eval_add",
    ),
    path(
        "<slug:course_slug>/<slug:cohort_identifier>/project/<slug:project_slug>/eval/delete/<int:review_id>",
        project_eval_actions.projects_eval_delete,
        name="cohort_projects_eval_delete",
    ),
    path(
        "<slug:course_slug>/<slug:module_slug>",
        shared_course.shared_module_view,
        name="shared_module",
    ),
    path(
        "<slug:course_slug>/<slug:module_slug>/<slug:lesson_slug>",
        shared_course.shared_lesson_view,
        name="shared_lesson",
    ),
    path(
        "register/<slug:campaign_slug>/",
        registration.registration_campaign_view,
        name="registration_campaign",
    ),
    path("wrapped/<int:year>/", wrapped.wrapped_view, name="wrapped"),
    path("wrapped/<int:year>/<int:student_id>/", wrapped.user_wrapped_view, name="user_wrapped"),
    # The bare two-segment shape is ambiguous on its face -- its second
    # segment could name either a cohort or a shared-curriculum module -- so
    # this one route owns both: ``course.course_view`` tries a cohort first
    # (via ``shared_course.dispatch_two_segment_path``) and renders the
    # shared module page when it isn't one. A ``SharedModule`` can never be
    # created with a slug that collides with a ``Cohort`` identifier in the
    # same family (and vice versa), so the lookup order never actually has
    # to break a tie -- see the model-level guards on both.
    path(
        "<slug:course_slug>/<slug:cohort_identifier>",
        course.course_view,
        name="cohort",
    ),
    path(
        "<slug:course_slug>",
        course.course_family_view,
        name="course_family",
    ),
    # Legacy edition-slug patterns remain only so existing copied fixtures and
    # management callbacks can be exercised while all generated public links
    # use the canonical ``<family>/<cohort_identifier>`` shape above.
    path(
        "<slug:course_slug>/calendar.ics",
        course_calendar.course_calendar_view,
        name="course_calendar",
    ),
    path(
        "<slug:course_slug>/",
        course.course_view,
        name="course",
    ),
    # Family-wide gallery: every project submitted across every cohort of the
    # family, not one cohort's submissions. ``<slug:course_slug>/projects``
    # above is already the per-cohort shorthand (it resolves the cohort whose
    # slug equals the family slug), so this route carries its own distinct
    # literal segment rather than colliding with it.
    path(
        "<slug:course_slug>/projects/all",
        site_project_gallery.family_projects_redirect,
        name="family_projects",
    ),
    path(
        "<slug:course_slug>/leaderboard",
        course_leaderboard.leaderboard_view,
        name="leaderboard",
    ),
    path(
        "<slug:course_slug>/leaderboard/<int:enrollment_id>/",
        course_leaderboard.leaderboard_score_breakdown_view,
        name="leaderboard_score_breakdown",
    ),
    path(
        "<slug:course_slug>/leaderboard/<int:enrollment_id>/report",
        course_leaderboard.leaderboard_complaint_view,
        name="leaderboard_complaint",
    ),
    path(
        "<slug:course_slug>/enrollment/toggle",
        course_enrollment.update_enrollment_toggle,
        name="update_enrollment_toggle",
    ),
    path(
        "<slug:course_slug>/enrollment",
        course_enrollment.enrollment_view,
        name="enrollment",
    ),
    path(
        "<slug:course_slug>/dashboard",
        dashboard.dashboard_view,
        name="dashboard",
    ),
    # project
    path(
        "<slug:course_slug>/project/<slug:project_slug>",
        project.project_view,
        name="project",
    ),
    path(
        "<slug:course_slug>/project/<slug:project_slug>/eval",
        project_eval.projects_eval_view,
        name="projects_eval",
    ),
    path(
        "<slug:course_slug>/project/<slug:project_slug>/results",
        project_results.project_results,
        name="project_results",
    ),
    path(
        "<slug:course_slug>/project/<slug:project_slug>/stats",
        project_statistics.project_statistics,
        name="project_statistics",
    ),
    path(
        "<slug:course_slug>/project/<slug:project_slug>/submissions",
        project_submissions.project_submissions,
        name="project_submissions",
    ),
    path(
        "<slug:course_slug>/project/<slug:project_slug>/eval/<int:review_id>",
        project_eval_submit.projects_eval_submit,
        name="projects_eval_submit",
    ),
    path(
        "<slug:course_slug>/project/<slug:project_slug>/eval/add/<int:submission_id>",
        project_eval_actions.projects_eval_add,
        name="projects_eval_add",
    ),
    path(
        "<slug:course_slug>/project/<slug:project_slug>/eval/delete/<int:review_id>",
        project_eval_actions.projects_eval_delete,
        name="projects_eval_delete",
    ),
    # homework
    path(
        "<slug:course_slug>/homework/<slug:homework_slug>",
        homework.homework_view,
        name="homework",
    ),
    path(
        "<slug:course_slug>/homework/<slug:homework_slug>/stats",
        homework_statistics.homework_statistics,
        name="homework_statistics",
    ),
    path(
        "<slug:course_slug>/homework/<slug:homework_slug>/submissions",
        homework_submissions.homework_submissions,
        name="homework_submissions",
    ),
]

# Route resolution order:
# 0. the retired ``cohorts/`` prefix, matched only to 301-redirect it away;
# 1. a cohort route with a literal operation segment (``modules/``,
#    ``homework/``, ``enrollment``, ...), which must win over any generic
#    slug shape -- ``/courses/x/homework/hw`` is a homework route, never a
#    lesson;
# 2. the generic two-segment ``cohort`` route: the one entry point for a
#    bare two-segment course path, dispatching to the shared-curriculum
#    module view when the second segment isn't a cohort of this family;
# 3. generic shared module/lesson shapes, registered so ``reverse`` emits
#    the canonical cohort-free paths (real inbound traffic never reaches
#    these directly -- group 2 already dispatches to them);
# 4. the one-segment family route, plus every literal-segment legacy
#    edition-slug shim below it.


def _has_literal_segment(pattern: str) -> bool:
    """Whether the route carries a literal (non-converter) path part."""

    stripped = re.sub(r"<[^>]+>", "", pattern)
    return any(part for part in stripped.split("/") if part)


def _route_group(pattern: str) -> int:
    if "/cohorts/" in pattern:
        return 0
    literal = _has_literal_segment(pattern)
    if pattern == "<slug:course_slug>/<slug:cohort_identifier>":
        return 2
    if "<slug:cohort_" in pattern:
        return 1 if literal else 3
    if pattern.startswith("<slug:course_slug>/"):
        return 1 if literal else 3
    return 4


urlpatterns.sort(key=lambda pattern: _route_group(str(pattern.pattern)))
