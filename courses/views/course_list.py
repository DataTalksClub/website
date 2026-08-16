from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Count
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from core.course_index_content import (
    catalog_eyebrow,
    catalog_stats,
    cohort_dates_display,
    course_filters,
    course_promise,
    enrolled_state_label,
    selected_course_filter,
)
from courses.models.course import Course
from courses.services.registration_counts import (
    public_course_registration_count,
)
from courses.views.course_homepage import add_course_homepage_info
from courses.views.course_list_user_state import (
    attach_registration_campaigns,
    mark_enrolled_courses,
    mark_registered_courses,
)


@dataclass(frozen=True)
class CourseListCourses:
    courses: list
    active_courses: list
    open_registration_courses: list
    finished_courses: list
    archive_courses_by_year: dict


def visible_course_list_queryset():
    courses = Course.objects.filter(visible=True)
    homework_count = Count("homework", distinct=True)
    project_count = Count("project", distinct=True)
    learner_count = Count("enrollment", distinct=True)
    courses = courses.annotate(
        homework_count=homework_count,
        project_count=project_count,
        learner_count=learner_count,
    )
    courses = courses.prefetch_related("homework_set", "project_set")
    return courses.order_by("-id")


def split_courses_by_status(courses, now):
    active_courses = []
    open_registration_courses = []
    finished_courses = []
    archive_courses_by_year = defaultdict(list)

    for course in courses:
        add_course_homepage_info(course, now)

        if course.finished:
            finished_courses.append(course)
            archive_courses_by_year[course.home_year].append(course)
        elif course.home_registration_open:
            open_registration_courses.append(course)
        else:
            active_courses.append(course)

    return CourseListCourses(
        courses=courses,
        active_courses=active_courses,
        open_registration_courses=open_registration_courses,
        finished_courses=finished_courses,
        archive_courses_by_year=archive_courses_by_year,
    )


def featured_course(active_courses):
    for course in active_courses:
        title = course.title.lower()
        if not title.startswith("fake"):
            return course

    if active_courses:
        return active_courses[0]

    return None


def archive_year_sort_key(year):
    return (year == "Archive", year)


def course_archive_groups(archive_courses_by_year):
    archive_year_keys = archive_courses_by_year.keys()
    archive_years = sorted(
        archive_year_keys,
        key=archive_year_sort_key,
        reverse=True,
    )
    if "Archive" in archive_years:
        archive_years.remove("Archive")
        archive_years.append("Archive")

    archive_groups = []
    for year in archive_years:
        archive_group = {
            "year": year,
            "courses": archive_courses_by_year[year],
        }
        archive_groups.append(archive_group)
    return archive_groups


def course_home_stats(courses, active_courses, finished_courses):
    homework_count = 0
    project_count = 0
    for course in courses:
        homework_count += course.homework_count
        project_count += course.project_count

    active_course_count = len(active_courses)
    archive_course_count = len(finished_courses)
    return {
        "active_courses": active_course_count,
        "archive_courses": archive_course_count,
        "homeworks": homework_count,
        "projects": project_count,
    }


def other_active_courses(active_courses, featured_course):
    other_courses = []
    for course in active_courses:
        if course != featured_course:
            other_courses.append(course)
    return other_courses


def prepare_course_list_courses(user):
    visible_courses = visible_course_list_queryset()
    courses = list(visible_courses)
    now = timezone.now()
    course_groups = split_courses_by_status(courses, now)

    mark_enrolled_courses(course_groups.courses, user)
    attach_registration_campaigns(course_groups.courses)
    mark_registered_courses(course_groups.courses, user)

    return course_groups


def registered_learner_count(course):
    """Read the accepted public registration count, or None when there is none.

    The count is the same governed figure the registration page publishes; it fails
    closed to None, and the card then simply omits the line.
    """

    campaign = getattr(course, "registration_campaign", None)
    if campaign is None:
        return None

    public_count = public_course_registration_count(campaign)
    if public_count is None:
        return None

    return public_count.count


def add_course_index_info(course, today) -> None:
    """Attach the facts the design 5a courses index shows for one course."""

    course.index_promise = course_promise(course.slug)
    course.index_dates = cohort_dates_display(
        course.start_date,
        course.end_date,
    )
    course.index_enrolled_state = enrolled_state_label(
        enrolled=bool(getattr(course, "is_enrolled", False)),
        start=course.start_date,
        end=course.end_date,
        today=today,
    )
    if course.home_duration_label == "TBA":
        course.index_length = ""
    else:
        course.index_length = course.home_duration_label
    course.index_when = course_when_display(
        course.index_dates,
        course.index_length,
    )


def course_when_display(dates, length):
    """Join the run's dates and its length, keeping whichever of the two exists."""

    parts = []
    if dates:
        parts.append(dates)
    if length:
        parts.append(length)
    return " · ".join(parts)


def visible_course_sections(selected_filter):
    return {
        "show_active_courses": selected_filter in {"all", "active"},
        "show_open_registration": selected_filter in {"all", "open"},
        "show_self_paced": selected_filter in {"all", "self-paced"},
    }


def course_list_context(request):
    course_groups = prepare_course_list_courses(request.user)
    today = timezone.localdate()
    for course in course_groups.courses:
        add_course_index_info(course, today)
    for course in course_groups.open_registration_courses:
        course.index_registered = registered_learner_count(course)

    selected_featured_course = featured_course(course_groups.active_courses)
    archive_groups = course_archive_groups(
        course_groups.archive_courses_by_year
    )
    secondary_active_courses = other_active_courses(
        course_groups.active_courses,
        selected_featured_course,
    )
    home_stats = course_home_stats(
        course_groups.courses,
        course_groups.active_courses,
        course_groups.finished_courses,
    )
    selected_filter = selected_course_filter(request.GET.get("filter"))

    context = {
        "active_courses": course_groups.active_courses,
        "open_registration_courses": course_groups.open_registration_courses,
        "archive_groups": archive_groups,
        "featured_course": selected_featured_course,
        "finished_courses": course_groups.finished_courses,
        "other_active_courses": secondary_active_courses,
        "home_stats": home_stats,
        "catalog_eyebrow": catalog_eyebrow(len(course_groups.courses)),
        "catalog_stats": catalog_stats(
            course_groups.courses,
            home_stats["homeworks"],
        ),
        "course_filters": course_filters(
            selected_filter,
            reverse("course_list"),
        ),
        "selected_course_filter": selected_filter,
    }
    context.update(visible_course_sections(selected_filter))
    return context


def course_list(request):
    context = course_list_context(request)
    response = render(
        request,
        "courses/course_list.html",
        context,
    )
    return response
