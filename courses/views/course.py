from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from courses.views.course_page_context import (
    CoursePageData,
    course_family_page_context,
    course_family_page_data,
    course_page_context,
    course_page_data,
    should_redirect_to_registration_campaign,
)


def course_registration_redirect_response(data: CoursePageData):
    if should_redirect_to_registration_campaign(
        registration_campaign=data.registration_campaign,
        homeworks=data.homeworks,
        projects=data.projects,
        user=data.user,
    ):
        response = redirect(
            "registration_campaign",
            campaign_slug=data.registration_campaign.slug,
        )
        return response
    return None


def course_view(
    request: HttpRequest,
    course_slug: str,
    cohort_identifier: str | int | None = None,
) -> HttpResponse:
    """The one entry point for both course-family shapes.

    The two-segment shape (``<family>/<cohort_identifier>``, name
    ``cohort``) is ambiguous on its face -- the second segment could name a
    cohort or a shared-curriculum module -- so it dispatches: a shared
    module slug renders the cohort-free shared page, and a cohort identifier
    renders the cohort page directly, since this bare path is already the
    canonical one (issue #320). The one-segment legacy edition-slug shim
    (name ``course``) calls this with no identifier and always renders the
    cohort page.
    """

    if cohort_identifier is not None:
        from .shared_course import dispatch_two_segment_path

        dispatched = dispatch_two_segment_path(request, course_slug, str(cohort_identifier))
        if dispatched is not None:
            return dispatched

    return _render_cohort_page(request, course_slug, cohort_identifier)


def _render_cohort_page(
    request: HttpRequest,
    course_slug: str,
    cohort_identifier: str | int | None,
) -> HttpResponse:
    data = course_page_data(course_slug, request.user, cohort_identifier)
    redirect_response = course_registration_redirect_response(data)
    if redirect_response is not None:
        return redirect_response

    context = course_page_context(data)
    response = render(
        request,
        "courses/course.html",
        context,
    )
    return response


def course_family_view(request: HttpRequest, course_slug: str) -> HttpResponse:
    try:
        family = course_family_page_data(course_slug)
    except Http404:
        # A small fixture/management shim for old edition-slug requests. Public
        # links are generated from the family/identifier route below.
        return course_view(request, course_slug)
    context = course_family_page_context(family, request.user)
    return render(
        request,
        "courses/course_family.html",
        context,
    )
