from django.http import Http404, HttpRequest, HttpResponse

from django.shortcuts import render, redirect

from courses.views.course_page_context import (
    CoursePageData,
    course_page_context,
    course_page_data,
    course_family_page_data,
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
    cohort_year: int | None = None,
) -> HttpResponse:
    data = course_page_data(course_slug, request.user, cohort_year)
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
        # links are generated from the family/year route below.
        return course_view(request, course_slug)
    cohorts = family.cohorts.filter(visible=True).order_by("-year", "-id")
    return render(
        request,
        "courses/course_family.html",
        {"course_family": family, "cohorts": cohorts},
    )
