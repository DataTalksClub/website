"""Pure context builders for the package mail purposes.

Relocated from the retired Datamailer payloads (D1.2ca). Everything here
is envelope-free: functions take model objects and return plain template
context; the send side lives in :mod:`course_management.package_mail`.
"""

from __future__ import annotations

from django.utils.dateformat import format as format_date

from course_management.public_urls import (
    cohort_route_kwargs,
    public_route_url,
    public_url,
)


def registration_email(registration) -> str:
    email = registration.email_normalized or registration.email or ""
    return email.strip().lower()


def registration_confirmation_urls(campaign, course) -> dict[str, str]:
    registration_kwargs = {"campaign_slug": campaign.slug}
    registration_url = public_route_url(
        "registration_campaign",
        registration_kwargs,
    )
    course_url = ""
    if course is not None:
        course_kwargs = cohort_route_kwargs(course)
        course_url = public_route_url("cohort", course_kwargs)
    profile_url = public_route_url("account_settings")
    return {
        "registration_url": registration_url,
        "course_url": course_url,
        "profile_url": profile_url,
    }


def registration_confirmation_course_context(course):
    context = {
        "course_title": "",
        "course_slug": "",
        "course_start_date": "",
    }
    if course is not None:
        context["course_title"] = course.title
        context["course_slug"] = course.slug
        if course.start_date:
            context["course_start_date"] = format_date(
                course.start_date,
                "F j, Y",
            )
    return context


def certificate_availability_urls(enrollment):
    course = enrollment.course
    certificate_path = enrollment.certificate_url.strip()
    certificate_url = public_url(certificate_path)
    course_kwargs = cohort_route_kwargs(course)
    course_url = public_route_url("cohort", course_kwargs)
    profile_url = public_route_url("account_settings")
    return {
        "course_url": course_url,
        "certificate_url": certificate_url,
        "profile_url": profile_url,
    }
