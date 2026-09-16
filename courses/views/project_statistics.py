from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from courses.assignment_statistics import calculate_project_statistics
from courses.models.project import Project, ProjectState
from courses.models.stat_display import project_stat_sections
from courses.views.url_utils import canonical_cohort_url_kwargs, get_cohort_or_404


def _position(value, minimum, maximum):
    if value is None or minimum is None or maximum is None:
        return None
    if maximum == minimum:
        return 50.0
    return max(0.0, min(100.0, (value - minimum) * 100 / (maximum - minimum)))


def project_statistics_sections(stats):
    """Prepare score ranges for the dashboard-style statistics cards."""
    result = []
    for section in project_stat_sections():
        values = {
            stat_type: stats.get_value(section.field_name, stat_type)
            for stat_type in ("min", "q1", "median", "avg", "q3", "max")
        }
        minimum = values["min"]
        maximum = values["max"]
        has_data = all(value is not None for value in values.values())
        result.append(
            {
                "label": section.label,
                "is_time": section.field_name == "time_spent",
                "values": values,
                "has_data": has_data,
                "q1_position": _position(values["q1"], minimum, maximum),
                "median_position": _position(values["median"], minimum, maximum),
                "average_position": _position(values["avg"], minimum, maximum),
                "q3_position": _position(values["q3"], minimum, maximum),
                "iqr_width": (
                    max(
                        0.0,
                        _position(values["q3"], minimum, maximum)
                        - _position(values["q1"], minimum, maximum),
                    )
                    if has_data
                    else None
                ),
            }
        )
    return result


def incomplete_project_statistics_response(request, course, project):
    messages.error(
        request,
        "This project is not completed yet, so there are no available statistics.",
        extra_tags="project",
    )
    response = redirect(
        "cohort_project",
        **canonical_cohort_url_kwargs(course),
        project_slug=project.slug,
    )
    return response


def project_statistics(request, course_slug, project_slug, cohort_identifier=None):
    course = get_cohort_or_404(course_slug, cohort_identifier)
    project = get_object_or_404(Project, course=course, slug=project_slug)

    if project.state != ProjectState.COMPLETED.value:
        return incomplete_project_statistics_response(
            request,
            course,
            project,
        )

    stats = calculate_project_statistics(project, force=False)

    context = {
        "course": course,
        "course_family": course.course,
        "project": project,
        "stats": stats,
        "stat_sections": project_statistics_sections(stats),
    }

    response = render(request, "projects/stats.html", context)
    return response
