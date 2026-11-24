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
from courses import catalog_facets
from courses.models.cohort import Cohort, DeliveryMode
from courses.models.wrapped import WrappedStatistics
from courses.services.public_course_catalog import visible_course_list_queryset
from courses.services.registration_campaigns import family_registration
from courses.services.registration_counts import (
    public_course_registration_count,
)
from courses.services.testimonials import course_catalog_testimonials
from courses.views.course_homepage import add_course_homepage_info
from courses.views.course_list_user_state import (
    attach_registration_campaigns,
    mark_enrolled_courses,
    mark_registered_courses,
)
from courses.views.site_project_gallery import optional_all_projects_url


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


@dataclass(frozen=True)
class CatalogCard:
    """One row of the unified ``/courses`` catalogue: a family card plus its real state.

    The owner's ask is that every family shows one of three honest states -- you can
    join a cohort that is running now, register for an announced cohort with a real
    date, or register for whatever cohort comes next when no date has been set --
    rather than being sorted into a "running now" or "self-paced" section.  ``kind``
    carries that state (``"active"``, ``"open_dated"``, ``"open_undated"``, or
    ``"self_paced"`` for a family with no live schedule at all; ``"archived"`` is the
    rare fallback for a family with neither a live cohort nor an open campaign).
    ``next_campaign`` is only set for ``"open_undated"``, where the promoted cohort
    (``card.cohort``) is not the edition being registered for.
    """

    card: CourseFamilyCard
    kind: str
    next_campaign: object = None

    @property
    def family(self):
        return self.card.family

    @property
    def status(self) -> str:
        return catalog_facets.STATUS_BY_KIND.get(self.kind, "")

    @property
    def has_facets(self) -> bool:
        family = self.family
        return bool(
            family.catalog_topic
            or family.catalog_level
            or family.catalog_duration_weeks
            or family.catalog_tools
            or family.catalog_project
        )

    @property
    def topic_label(self) -> str:
        return self.family.get_catalog_topic_display() if self.family.catalog_topic else ""

    @property
    def level_label(self) -> str:
        return self.family.get_catalog_level_display() if self.family.catalog_level else ""

    @property
    def duration_label(self) -> str:
        return catalog_facets.duration_label(self.family.catalog_duration_weeks)

    @property
    def topic_level_line(self) -> str:
        return " · ".join(part for part in (self.topic_label, self.level_label) if part)

    @property
    def length_line(self) -> str:
        if not self.duration_label:
            return ""
        return " · ".join(part for part in (self.duration_label, self.hours_label) if part)

    @property
    def hours_label(self) -> str:
        hours = (self.family.catalog_hours_per_week or "").strip()
        return f"{hours} hours a week" if hours else ""

    @property
    def filter_values(self) -> dict[str, tuple[str, ...]]:
        return catalog_facets.card_filter_values(self.family, self.kind)

    @property
    def filter_data(self) -> dict[str, str]:
        """Space-joined values for the card's ``data-filter-*`` attributes."""

        return {key: " ".join(values) for key, values in self.filter_values.items()}

    @property
    def cohort(self) -> Cohort:
        return self.card.cohort

    @property
    def title(self) -> str:
        return self.card.title

    @property
    def outcome(self) -> str:
        return self.card.outcome

    @property
    def edition_label(self) -> str:
        return self.card.edition_label

    @property
    def github_repo_url(self) -> str:
        return self.card.github_repo_url


def catalog_card_kind(card: CourseFamilyCard) -> tuple[str, object]:
    """Return the honest registration state a catalogue card should show.

    Reuses the same source of truth the family landing page reads
    (``family_registration``) instead of deriving a second opinion from dates: a
    cohort delivered self-paced with no live schedule says so before anything else,
    because ``split_courses_by_status`` otherwise bundles it in with genuinely
    running cohorts.  A live cohort that is running now, or one whose registration
    is open for a real future date, keeps the state ``course_family_cards`` already
    computed.  Anything else -- a family whose newest edition has finished -- asks
    the campaign catalogue whether a next-cohort campaign is open, and offers that
    instead of a manufactured date.
    """

    cohort = card.cohort
    if (
        getattr(cohort, "delivery_mode", "") == DeliveryMode.SELF_PACED.value
        and not cohort.finished
    ):
        return "self_paced", None
    if card.status == "active":
        return "active", None
    if card.status == "open_registration":
        return "open_dated", None
    registration = family_registration(card.family, editions=card.cohorts)
    if registration.campaign is not None:
        return "open_undated", registration.campaign
    return "archived", None


def catalog_family_order(family_slug: str) -> tuple[int, str]:
    """Sort the catalogue in the site's reviewed family order, unlisted ones after.

    Mirrors ``core.home_content``'s own ``_catalog_order``: the database decides which
    families exist, this only decides the order a reader sees them in, and a family
    the database holds that this table does not still renders, placed last.
    """

    for index, (listed_slug, _title) in enumerate(COURSE_FAMILIES):
        if listed_slug == family_slug:
            return (index, family_slug)
    return (len(COURSE_FAMILIES), family_slug)


# Site feedback 3.1: sort by start date, the nearest cohort first.  A cohort you
# can register for with a real date leads (soonest first), then the cohorts running
# right now (you can still join), then families whose next cohort has no date yet,
# then the self-paced ones.  Ties fall back to the site's reviewed family order.
_CATALOG_KIND_PRIORITY = {
    "open_dated": 0,
    "active": 1,
    "open_undated": 2,
    "self_paced": 3,
    "archived": 3,
}


def _catalog_sort_key(entry):
    start = getattr(entry.cohort, "start_date", None)
    start_ordinal = start.toordinal() if start and entry.kind == "open_dated" else 0
    return (
        _CATALOG_KIND_PRIORITY.get(entry.kind, 4),
        start_ordinal,
        catalog_family_order(entry.family.slug),
    )


def course_catalog_cards(family_cards: list[CourseFamilyCard]) -> list[CatalogCard]:
    """Build the unified catalogue: one row per family, honest state first, then date.

    Every family renders in the same list -- nothing is sorted into a "running now" or
    "self-paced" section -- but the reading order puts the nearest cohort first:
    register for the soonest dated cohort, then join what's running now, then the
    families whose next date is not set, then the self-paced ones.
    """

    catalog_cards = []
    for card in family_cards:
        kind, next_campaign = catalog_card_kind(card)
        catalog_cards.append(CatalogCard(card=card, kind=kind, next_campaign=next_campaign))
    catalog_cards.sort(key=_catalog_sort_key)
    return catalog_cards


def filter_href(selection, base_path: str) -> str:
    """The catalogue URL for one filter selection, anchored at the grid."""

    parts = [
        f"{key}={','.join(selection[key])}"
        for key in catalog_facets.FILTER_KEYS
        if selection.get(key)
    ]
    query = f"?{'&'.join(parts)}" if parts else ""
    return f"{base_path}{query}#courses"


def toggled_selection(selection, key, value):
    current = list(selection.get(key, ()))
    if value in current:
        current.remove(value)
    else:
        current.append(value)
    updated = dict(selection)
    if current:
        updated[key] = tuple(current)
    else:
        updated.pop(key, None)
    return updated


@dataclass(frozen=True)
class FilterOption:
    value: str
    label: str
    active: bool
    href: str = ""


@dataclass(frozen=True)
class FilterGroup:
    key: str
    label: str
    options: tuple[FilterOption, ...]


def catalog_filter_groups(catalog_cards, selection, base_path) -> tuple[FilterGroup, ...]:
    """The filter rows above the grid, offering only values some card really has.

    A pill that could only ever empty the grid is not offered: with seven families,
    every option shown narrows to at least one real course.
    """

    present: dict[str, set[str]] = defaultdict(set)
    tool_labels: dict[str, str] = {}
    for entry in catalog_cards:
        for key, values in entry.filter_values.items():
            present[key].update(values)
        for tool in entry.family.catalog_tools or []:
            tool_labels.setdefault(catalog_facets.tool_key(tool), tool)

    tool_options = tuple(sorted(tool_labels.items(), key=lambda item: item[1].lower()))
    rows = (
        ("topic", "Topic", catalog_facets.CatalogTopic.choices),
        ("level", "Level", catalog_facets.CatalogLevel.choices),
        ("length", "Length", catalog_facets.LENGTHS),
        ("status", "Format & status", catalog_facets.STATUSES),
        ("tool", "Tools", tool_options),
    )
    groups = []
    for key, label, choices in rows:
        chosen = selection.get(key, ())
        options = tuple(
            FilterOption(
                value=value,
                label=option_label,
                active=value in chosen,
                href=filter_href(toggled_selection(selection, key, value), base_path),
            )
            for value, option_label in choices
            if value in present[key]
        )
        if options:
            groups.append(FilterGroup(key=key, label=label, options=options))
    return tuple(groups)


def catalog_count_label(catalog_rows, selection) -> str:
    """Say "7 courses", or "2 of 7 courses" while a filter is on, as the page script does."""

    total = len(catalog_rows)
    if not selection:
        return f"{total} courses"
    shown = sum(1 for row in catalog_rows if row["visible"])
    return f"{shown} of {total} courses"


def offered_selection(catalog_cards, selection):
    """Drop any requested value no card has, so a stale link cannot empty the grid
    behind a filter the page does not even show."""

    offered: dict[str, set[str]] = defaultdict(set)
    for entry in catalog_cards:
        for key, values in entry.filter_values.items():
            offered[key].update(values)
    cleaned = {}
    for key, values in selection.items():
        kept = tuple(value for value in values if value in offered[key])
        if kept:
            cleaned[key] = kept
    return cleaned


def course_fit_options(catalog_cards, selection, base_path):
    """The "Which course fits me?" buttons, each naming the courses it filters to."""

    chosen = selection.get("for", ())
    options = []
    for value, label in catalog_facets.CatalogFit.choices:
        hint = catalog_facets.FIT_HINTS.get(value, "")
        titles = [entry.title for entry in catalog_cards if value in entry.filter_values["for"]]
        if titles:
            options.append(
                {
                    "value": value,
                    "label": label,
                    "hint": hint,
                    "titles": titles,
                    "active": value in chosen,
                    "href": filter_href(toggled_selection(selection, "for", value), base_path),
                }
            )
    return options


def active_filter_chips(filter_groups, fit_options):
    """The selected filters as removable chips; each chip's link drops just itself."""

    chips = [
        {"key": "for", "value": option["value"], "label": option["label"], "href": option["href"]}
        for option in fit_options
        if option["active"]
    ]
    for group in filter_groups:
        for option in group.options:
            if option.active:
                chips.append(
                    {
                        "key": group.key,
                        "value": option.value,
                        "label": option.label,
                        "href": option.href,
                    }
                )
    return chips


# ``visible_course_list_queryset`` now lives in
# ``courses.services.public_course_catalog`` so the homepage can share this one selector
# without importing this view module, which itself imports ``core.home_content``.  It is
# still imported above, so callers that have always read it from here keep working.


def split_courses_by_status(courses, now, *, include_assignments: bool = True):
    active_courses = []
    open_registration_courses = []
    finished_courses = []
    archive_courses_by_year = defaultdict(list)

    for course in courses:
        add_course_homepage_info(course, now, include_assignments=include_assignments)

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


def prepare_course_list_courses(
    user,
    *,
    prefetch_assignments: bool = True,
    include_user_state: bool = True,
):
    visible_courses = visible_course_list_queryset(prefetch_assignments=prefetch_assignments)
    courses = list(visible_courses)
    now = timezone.now()
    course_groups = split_courses_by_status(
        courses,
        now,
        include_assignments=prefetch_assignments,
    )

    if include_user_state:
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

    # The single catalogue list the page renders: one card per family, in the site's
    # reviewed order, each carrying the honest state (`CatalogCard.kind`) the owner asked
    # for instead of a "running now" / "self-paced" section split.
    catalog_cards = course_catalog_cards(family_cards)
    filter_selection = offered_selection(
        catalog_cards,
        catalog_facets.parse_filters(request.GET),
    )
    catalog_rows = [
        {
            "entry": entry,
            "visible": catalog_facets.card_matches(entry.filter_values, filter_selection),
        }
        for entry in catalog_cards
    ]
    filter_groups = catalog_filter_groups(catalog_cards, filter_selection, request.path)
    fit_options = course_fit_options(catalog_cards, filter_selection, request.path)

    context = {
        # Keep the cohort lists available to existing context consumers while
        # the page itself renders `catalog_cards` below.
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
        "catalog_cards": catalog_cards,
        "catalog_rows": catalog_rows,
        "catalog_visible_count": sum(1 for row in catalog_rows if row["visible"]),
        "catalog_count_label": catalog_count_label(catalog_rows, filter_selection),
        "catalog_filter_groups": filter_groups,
        "course_fit_options": fit_options,
        "active_filter_chips": active_filter_chips(filter_groups, fit_options),
        "clear_filters_href": filter_href({}, request.path),
        "catalog_testimonials": course_catalog_testimonials(),
        "home_stats": home_stats,
        "course_family_count": course_family_count,
        "total_open_registration_count": total_open_registration_count,
        "hero_register_url": hero_register_url,
        "all_projects_url": optional_all_projects_url(),
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
