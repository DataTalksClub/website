from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from courses.views.course_calendar_events import (
    course_calendar_event_lines,
    course_calendar_lines,
)
from courses.views.url_utils import get_cohort_or_404


def course_calendar_view(
    request: HttpRequest,
    course_slug: str,
    cohort_year: str | int | None = None,
) -> HttpResponse:
    course = get_cohort_or_404(course_slug, cohort_year, visible=True)
    dtstamp = timezone.now()
    event_lines = course_calendar_event_lines(request, course, dtstamp)
    calendar_lines = course_calendar_lines(course, event_lines)
    response_body = "\r\n".join(calendar_lines) + "\r\n"

    response = HttpResponse(
        response_body,
        content_type="text/calendar; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'inline; filename="{course.slug}-deadlines.ics"'
    )
    return response
