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
    if cohort_identifier is not None:
        # The two-segment shape dispatches for shared-curriculum families: a
        # shared module slug serves the cohort-free shared page, and a cohort
        # identifier of a moved family one-hop redirects to the canonical
        # ``cohorts/<identifier>`` namespace.  Families still entirely on the
        # legacy contract keep rendering the cohort page here.
        from .shared_course import dispatch_two_segment_path

        dispatched = dispatch_two_segment_path(request, course_slug, str(cohort_identifier))
        if dispatched is not None:
            return dispatched

    return _render_cohort_page(request, course_slug, cohort_identifier)


def cohort_page_view(
    request: HttpRequest,
    course_slug: str,
    cohort_identifier: str,
) -> HttpResponse:
    """The canonical ``/courses/<family>/cohorts/<identifier>`` landing page.

    The kwarg adapter keeps the existing cohort-page data pipeline untouched
    while the canonical public path carries the semantic identifier name.
    This entry never re-dispatches: the path is already canonical.
    """

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
