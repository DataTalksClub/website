from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from core.course_index_content import (
    cohort_dates_display,
    enrolled_state_label,
)
from core.home_content import COURSE_FAMILIES
from courses.models.cohort import Cohort
from courses.models.wrapped import WrappedStatistics
from courses.services.public_course_catalog import visible_course_list_queryset
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


@dataclass(frozen=True)
class CourseFamilyCard:
    """One catalogue row for a reusable course family.

    The family owns the copy shown on the card.  The selected cohort supplies
    the edition-specific facts that the catalogue already exposes, such as
    dates, registration state, assignments, and enrolment state.
    """

    family: object
    cohort: Cohort
    cohorts: tuple[Cohort, ...]
    status: str

    @property
    def title(self) -> str:
        return self.family.title

    @property
    def outcome(self) -> str:
        return getattr(self.family, "outcome", "") or ""

    @property
    def edition_label(self) -> str:
        if self.status == "active":
            return "Current edition"
        if self.status == "open_registration":
            return "Next edition"
        return "Latest edition"

    @property
    def github_repo_url(self) -> str:
        return (
            getattr(self.family, "github_repo_url", "")
            or getattr(self.cohort, "github_repo_url", "")
            or ""
        )


# ``visible_course_list_queryset`` now lives in
# ``courses.services.public_course_catalog`` so the homepage can share this one selector
# without importing this view module, which itself imports ``core.home_content``.  It is
# still imported above, so callers that have always read it from here keep working.


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
    active_by_family_slug = {
        course.course.slug: course
        for course in active_courses
        if getattr(course, "course", None) is not None
    }
    for family_slug, _title in COURSE_FAMILIES:
        featured = active_by_family_slug.get(family_slug)
        if featured is not None:
            return featured

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


def _cohort_recency_key(cohort):
    return (
        cohort.year,
        cohort.start_date or date.min,
        cohort.end_date or date.min,
        cohort.id,
    )


def course_family_cards(course_groups: CourseListCourses) -> list[CourseFamilyCard]:
    """Collapse visible cohort editions into one family card each.

    A live edition wins over an upcoming edition, and an upcoming edition wins
    over an archived one.  Within a status, the newest edition is the public
    route represented by the card.
    """

    cohorts_by_family = defaultdict(list)
    for cohort in course_groups.courses:
        cohorts_by_family[cohort.course_id].append(cohort)

    active_ids = {cohort.id for cohort in course_groups.active_courses}
    open_ids = {cohort.id for cohort in course_groups.open_registration_courses}
    finished_ids = {cohort.id for cohort in course_groups.finished_courses}

    cards = []
    for cohorts in cohorts_by_family.values():
        active = [cohort for cohort in cohorts if cohort.id in active_ids]
        open_registration = [cohort for cohort in cohorts if cohort.id in open_ids]
        finished = [cohort for cohort in cohorts if cohort.id in finished_ids]

        if active:
            status = "active"
            candidates = active
        elif open_registration:
            status = "open_registration"
            candidates = open_registration
        else:
            status = "finished"
            candidates = finished

        representative = max(candidates, key=_cohort_recency_key)
        cards.append(
            CourseFamilyCard(
                family=representative.course,
                cohort=representative,
                cohorts=tuple(sorted(cohorts, key=_cohort_recency_key, reverse=True)),
                status=status,
            )
        )

    return cards


def course_family_archive_groups(cards):
    archive_courses_by_year = defaultdict(list)
    for card in cards:
        archive_courses_by_year[card.cohort.home_year].append(card)
    return course_archive_groups(archive_courses_by_year)


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
    """Attach the facts the design system courses index shows for one course."""

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


# The year the Wrapped entry point shipped with, and the year it keeps pointing
# at while no wrapped has been published yet.
WRAPPED_ENTRY_FALLBACK_YEAR = 2025


def wrapped_entry_year():
    """Return the year the courses index links its Wrapped entry point at.

    The courses index is the site's only route into Wrapped, so the year follows
    the published data rather than a constant in the template: the newest year an
    editor has made visible.  With nothing published the link keeps the year the
    entry point shipped with, which is where the page pointed before.  Nothing is
    read while SHOW_WRAPPED is off, and the template renders no entry point
    without a year.
    """

    if not settings.SHOW_WRAPPED:
        return None

    published_years = WrappedStatistics.objects.filter(is_visible=True)
    published_year = published_years.order_by("-year").values_list("year", flat=True).first()
    if published_year is None:
        return WRAPPED_ENTRY_FALLBACK_YEAR
    return published_year


def open_registration_registered_total(open_registration_family_cards):
    """Sum the real published registration counts across the open cards.

    Each count is the same governed public figure a card already shows on its
    own (``registered_learner_count``); this is only their total, for the hero
    stat tile.  A card with no publishable count contributes nothing, and the
    tile is omitted entirely (``None``) when no card has one to add.
    """

    counts = [
        card.cohort.index_registered
        for card in open_registration_family_cards
        if getattr(card.cohort, "index_registered", None)
    ]
    if not counts:
        return None
    return sum(counts)


# Decorative site illustrations for the hero collage, paired with the collage
# captions (real family titles from the database).  The artwork is the shared
# illustration set the homepage already carries; each entry names the
# light/dark file pair rendered by the template.
HERO_COLLAGE_ILLUSTRATIONS = ("reading", "pipeline", "shipping", "learner")


def hero_collage_cards(active_family_cards, open_registration_family_cards):
    """Pair up to four real family titles with the hero's decorative artwork."""

    titles_by_slug = {}
    for card in [*active_family_cards, *open_registration_family_cards]:
        titles_by_slug.setdefault(card.family.slug, card.title)

    ordered_titles = []
    for family_slug, _title in COURSE_FAMILIES:
        title = titles_by_slug.pop(family_slug, None)
        if title:
            ordered_titles.append(title)
    ordered_titles.extend(titles_by_slug.values())

    return [
        {"title": title, "illustration": HERO_COLLAGE_ILLUSTRATIONS[index % 4]}
        for index, title in enumerate(ordered_titles[:4])
    ]


def course_list_context(request):
    course_groups = prepare_course_list_courses(request.user)
    today = timezone.localdate()
    for course in course_groups.courses:
        add_course_index_info(course, today)
    for course in course_groups.open_registration_courses:
        course.index_registered = registered_learner_count(course)

    family_cards = course_family_cards(course_groups)
    active_family_cards = [card for card in family_cards if card.status == "active"]
    open_registration_family_cards = [
        card for card in family_cards if card.status == "open_registration"
    ]
    finished_family_cards = [card for card in family_cards if card.status == "finished"]

    selected_featured_course = featured_course([card.cohort for card in active_family_cards])
    selected_featured_card = next(
        (card for card in active_family_cards if card.cohort == selected_featured_course),
        None,
    )
    secondary_active_courses = other_active_courses(
        [card.cohort for card in active_family_cards],
        selected_featured_course,
    )
    secondary_active_family_cards = [
        card for card in active_family_cards if card is not selected_featured_card
    ]
    displayed_active_family_cards = secondary_active_family_cards
    if selected_featured_card is not None:
        displayed_active_family_cards = [
            selected_featured_card,
            *secondary_active_family_cards,
        ]
    archive_groups = course_family_archive_groups(finished_family_cards)
    home_stats = course_home_stats(
        course_groups.courses,
        course_groups.active_courses,
        course_groups.finished_courses,
    )
    course_family_count = len(family_cards)
    total_open_registration_count = open_registration_registered_total(
        open_registration_family_cards
    )
    hero_register_url = None
    for card in open_registration_family_cards:
        campaign = getattr(card.cohort, "registration_campaign", None)
        if campaign is not None:
            hero_register_url = reverse("registration_campaign", args=[campaign.slug])
            break

    context = {
        # Keep the cohort lists available to existing context consumers while
        # the page itself renders the family-card lists below.
        "active_courses": [card.cohort for card in displayed_active_family_cards],
        "open_registration_courses": [card.cohort for card in open_registration_family_cards],
        "archive_groups": archive_groups,
        "featured_course": selected_featured_course,
        "finished_courses": [card.cohort for card in finished_family_cards],
        "other_active_courses": secondary_active_courses,
        "course_family_cards": family_cards,
        "active_course_cards": displayed_active_family_cards,
        "open_registration_course_cards": open_registration_family_cards,
        "finished_course_cards": finished_family_cards,
        "featured_course_card": selected_featured_card,
        "home_stats": home_stats,
        "course_family_count": course_family_count,
        "total_open_registration_count": total_open_registration_count,
        "hero_collage": hero_collage_cards(
            displayed_active_family_cards,
            open_registration_family_cards,
        ),
        "hero_register_url": hero_register_url,
        "show_active_courses": True,
        "show_open_registration": True,
        "show_finished": True,
        "wrapped_year": wrapped_entry_year(),
    }
    return context


def course_list(request):
    context = course_list_context(request)
    response = render(
        request,
        "courses/course_list.html",
        context,
    )
    return response
