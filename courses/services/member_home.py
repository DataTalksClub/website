"""Context builder for the authenticated branch of ``/`` (issue: signed-in home).

Every query here is grounded in ``_docs/design/specs/signed-in-home.md``: the
"my courses" union (§4), the first-unread-unit resume rule (§4, reusing the
annotation pattern from ``courses/views/module.py:36-45``), the state-filtered
deadline union (§4, following ``courses/deadline_reminder_queries.py`` rather
than the unfiltered ``add_course_homepage_info`` reminder path), and the four
states with their exact copy (§5).

The view (``core/views.home``) owns the authentication branch; this module
owns every query because every model it touches is courses-owned
(``_docs/architecture/app-boundaries.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from accounts.home_dismissals import (
    CHECKLIST_DISMISSED,
    CHECKLIST_SKIP_COURSE,
    CHECKLIST_SKIP_PROFILE,
    CHECKLIST_SKIP_SLACK,
    CHECKLIST_SLACK_DONE,
    HOME_DISMISSAL_KEYS,
)
from content.public_data import event_groups
from core.home_content import event_time_display
from courses.course_page_content import course_modules, submission_progress
from courses.models.cohort import Cohort, CourseRegistration, CurriculumFormat, Enrollment
from courses.models.curriculum import Unit, UnitReadState
from courses.models.homework import Homework, HomeworkState, Submission
from courses.models.project import Project, ProjectState, ProjectSubmission
from courses.services.registration_campaigns import next_edition_campaign_for_cohort
from courses.views.course_homeworks import get_homeworks_for_course
from courses.views.course_list import course_family_cards, prepare_course_list_courses
from courses.views.course_projects import get_projects_for_course
from courses.views.url_utils import cohort_url, cohort_url_kwargs

# The onboarding checklist's server-persisted keys are owned by ``accounts``
# (``accounts/home_dismissals.py``), since ``home_dismissals`` is an accounts
# field; re-exported here so callers that only know this module keep working.
__all__ = [
    "HOME_DISMISSAL_KEYS",
    "CHECKLIST_SKIP_COURSE",
    "CHECKLIST_SLACK_DONE",
    "CHECKLIST_SKIP_SLACK",
    "CHECKLIST_SKIP_PROFILE",
    "CHECKLIST_DISMISSED",
    "build_member_home_context",
    "profile_is_complete",
]

# Defaults named in §13-Q8, kept as one-constant knobs.
DUE_SOON_WINDOW_DAYS = 14
DUE_SOON_CAP = 6
DISCOVER_CAP_STATE_1 = 4
DISCOVER_CAP_OTHER_STATES = 3
EVENTS_CAP = 3


def profile_is_complete(user) -> bool:
    """Derived, never a flag (§7.1.2): the three onboarding-owned fields are set."""

    return bool(user.certificate_name and user.country and user.registration_role)


@dataclass(frozen=True, slots=True)
class DeadlineItem:
    due: object
    title: str
    url: str
    cohort_title: str


@dataclass
class CohortCard:
    cohort: Cohort
    course_url: str
    kind: str  # "active" | "upcoming" | "finished"
    pill_label: str
    pill_class: str
    progress: object | None = None
    next_deadline: DeadlineItem | None = None
    cta_label: str = ""
    cta_url: str = ""
    total_score: int | None = None
    certificate_url: str | None = None
    next_edition_campaign: object | None = None


@dataclass
class DiscoverCard:
    title: str
    cohort_label: str
    start_display: str
    url: str


def classify_cohort(cohort: Cohort, today) -> str:
    """The derived state a cohort is in, per §4: no state column exists."""

    if cohort.finished:
        return "finished"
    if cohort.registration_url and cohort.start_date and today < cohort.start_date:
        return "upcoming"
    return "active"


def my_cohorts(user) -> list[Cohort]:
    """The union of enrolled and registered cohorts (§4): neither alone is enough."""

    email_normalized = (user.email or "").strip().lower()
    enrolled_ids = Enrollment.objects.filter(student=user).values("course_id")
    registered_ids = CourseRegistration.objects.filter(
        Q(user=user) | Q(email_normalized=email_normalized)
    ).values("course_id")
    return list(
        Cohort.objects.filter(Q(id__in=enrolled_ids) | Q(id__in=registered_ids))
        .select_related("course")
        .distinct()
    )


def resume_target(user, cohort: Cohort) -> tuple[str, str]:
    """The Continue/Start course/Open course label and target (§4)."""

    course_url = cohort_url(cohort)
    if cohort.curriculum_format != CurriculumFormat.MODULES:
        return "Open course", course_url

    next_unit = (
        Unit.objects.filter(module__cohort=cohort)
        .select_related("module")
        .annotate(is_read=Exists(UnitReadState.objects.filter(user=user, unit_id=OuterRef("pk"))))
        .order_by("module__position", "position")
        .filter(is_read=False)
        .first()
    )
    if next_unit is None:
        # Either every unit is read, or the cohort has no units at all.
        return "Open course", course_url

    any_read = UnitReadState.objects.filter(user=user, unit__module__cohort=cohort).exists()
    label = "Continue" if any_read else "Start course"
    unit_url = reverse(
        "unit",
        kwargs={
            "course_slug": cohort.course.slug,
            "cohort_identifier": cohort.identifier,
            "module_slug": next_unit.module.slug,
            "unit_slug": next_unit.slug,
        },
    )
    return label, unit_url


def _open_homework_candidates(user, cohort: Cohort, now):
    submitted_ids = Submission.objects.filter(student=user, homework__course=cohort).values(
        "homework_id"
    )
    return (
        Homework.objects.filter(
            course=cohort,
            state=HomeworkState.OPEN.value,
            due_date__gte=now,
        )
        .exclude(id__in=submitted_ids)
        .order_by("due_date")
    )


def _collecting_project_candidates(user, cohort: Cohort, now):
    submitted_ids = ProjectSubmission.objects.filter(
        student=user,
        volunteer_review_only=False,
        project__course=cohort,
    ).values("project_id")
    return (
        Project.objects.filter(
            course=cohort,
            state=ProjectState.COLLECTING_SUBMISSIONS.value,
            submission_due_date__gte=now,
        )
        .exclude(id__in=submitted_ids)
        .order_by("submission_due_date")
    )


def _peer_review_project_candidates(cohort: Cohort, now):
    return Project.objects.filter(
        course=cohort,
        state=ProjectState.PEER_REVIEWING.value,
        peer_review_due_date__gte=now,
    ).order_by("peer_review_due_date")


def deadline_items_for_cohort(user, cohort: Cohort, now) -> list[DeadlineItem]:
    """Every actionable deadline for one cohort (§4), state-filtered like the reminders."""

    items: list[DeadlineItem] = []
    kwargs = cohort_url_kwargs(cohort)
    for homework in _open_homework_candidates(user, cohort, now):
        items.append(
            DeadlineItem(
                due=homework.due_date,
                title=homework.title,
                url=reverse("homework", kwargs={**kwargs, "homework_slug": homework.slug}),
                cohort_title=cohort.title,
            )
        )
    for project in _collecting_project_candidates(user, cohort, now):
        items.append(
            DeadlineItem(
                due=project.submission_due_date,
                title=project.title,
                url=reverse("project", kwargs={**kwargs, "project_slug": project.slug}),
                cohort_title=cohort.title,
            )
        )
    for project in _peer_review_project_candidates(cohort, now):
        items.append(
            DeadlineItem(
                due=project.peer_review_due_date,
                title=project.title,
                url=reverse("projects_eval", kwargs={**kwargs, "project_slug": project.slug}),
                cohort_title=cohort.title,
            )
        )
    items.sort(key=lambda item: item.due)
    return items


def progress_for_cohort(user, cohort: Cohort):
    """The learner's own submitted-module count (§4), reusing the cohort page's own service."""

    homeworks = get_homeworks_for_course(cohort, user)
    projects = get_projects_for_course(cohort, user)
    modules = course_modules(homeworks, projects)
    return submission_progress(modules)


def _active_cohort_card(user, cohort: Cohort, has_enrollment: bool, now) -> CohortCard:
    cta_label, cta_url = resume_target(user, cohort)
    deadlines = deadline_items_for_cohort(user, cohort, now)
    progress = progress_for_cohort(user, cohort) if has_enrollment else None
    return CohortCard(
        cohort=cohort,
        course_url=cohort_url(cohort),
        kind="active",
        pill_label="enrolled" if has_enrollment else "registered",
        pill_class="" if has_enrollment else "status-pill-open",
        progress=progress,
        next_deadline=deadlines[0] if deadlines else None,
        cta_label=cta_label,
        cta_url=cta_url,
    )


def _upcoming_cohort_card(has_enrollment: bool, cohort: Cohort) -> CohortCard:
    return CohortCard(
        cohort=cohort,
        course_url=cohort_url(cohort),
        kind="upcoming",
        pill_label="enrolled" if has_enrollment else "registered",
        pill_class="" if has_enrollment else "status-pill-open",
    )


def _finished_cohort_card(user, cohort: Cohort, has_enrollment: bool) -> CohortCard:
    total_score = None
    certificate_url = None
    if has_enrollment and cohort.first_homework_scored:
        enrollment = Enrollment.objects.filter(student=user, course=cohort).first()
        if enrollment is not None:
            total_score = enrollment.total_score
            certificate_url = enrollment.certificate_url
    return CohortCard(
        cohort=cohort,
        course_url=cohort_url(cohort),
        kind="finished",
        pill_label="enrolled" if has_enrollment else "registered",
        pill_class="" if has_enrollment else "status-pill-open",
        total_score=total_score,
        certificate_url=certificate_url,
        next_edition_campaign=next_edition_campaign_for_cohort(cohort),
    )


def discover_cards(user, exclude_family_ids: set, limit: int) -> list[DiscoverCard]:
    """Catalogue cards for families the user has no cohort in (§5): active first, then open."""

    course_groups = prepare_course_list_courses(user)
    cards = course_family_cards(course_groups)
    ordered = [card for card in cards if card.status in ("active", "open_registration")]
    ordered.sort(key=lambda card: 0 if card.status == "active" else 1)
    results: list[DiscoverCard] = []
    for card in ordered:
        if card.family.id in exclude_family_ids:
            continue
        if len(results) >= limit:
            break
        start_display = ""
        if card.cohort.start_date and card.cohort.start_date > timezone.localdate():
            start = card.cohort.start_date
            start_display = f"{start:%B} {start.day}, {start:%Y}"
        results.append(
            DiscoverCard(
                title=card.title,
                cohort_label=f"{card.cohort.year} cohort",
                start_display=start_display,
                url=cohort_url(card.cohort),
            )
        )
    return results


def upcoming_events(limit: int = EVENTS_CAP):
    groups = event_groups()
    return tuple(
        {**event, "home_time": event_time_display(event["starts_at"])}
        for event in groups.upcoming[:limit]
    )


@dataclass
class ChecklistItem:
    key: str
    title: str
    description: str
    action_label: str
    action_url: str
    complete: bool
    skip_key: str | None = None


def checklist_items(user, dismissals: dict) -> list[ChecklistItem]:
    course_complete = bool(my_cohorts(user))
    slack_complete = bool(
        dismissals.get(CHECKLIST_SLACK_DONE) or dismissals.get(CHECKLIST_SKIP_SLACK)
    )
    profile_complete = profile_is_complete(user)

    items = [
        ChecklistItem(
            key="course",
            title="Pick your first course",
            description=(
                "Free, cohort-based, with homework, projects, and a certificate at the end."
            ),
            action_label="Browse courses",
            action_url=reverse("course_list"),
            complete=course_complete or bool(dismissals.get(CHECKLIST_SKIP_COURSE)),
            skip_key=CHECKLIST_SKIP_COURSE,
        ),
        ChecklistItem(
            key="slack",
            title="Join the DataTalks.Club Slack",
            description="Where course questions get asked and answered.",
            action_label="Join Slack",
            action_url=reverse("slack"),
            complete=slack_complete,
            skip_key=CHECKLIST_SKIP_SLACK,
        ),
        ChecklistItem(
            key="profile",
            title="Tell us about you",
            description=(
                "Your name, country, and role prefill every course registration. Enter them once."
            ),
            action_label="Add your details",
            action_url=reverse("account_welcome"),
            complete=profile_complete or bool(dismissals.get(CHECKLIST_SKIP_PROFILE)),
            skip_key=CHECKLIST_SKIP_PROFILE,
        ),
    ]
    return items


def build_member_home_context(request) -> dict:
    user = request.user
    now = timezone.now()
    today = timezone.localdate(now)
    dismissals = user.home_dismissals or {}

    cohorts = my_cohorts(user)
    enrolled_ids = set(
        Enrollment.objects.filter(student=user, course__in=cohorts).values_list(
            "course_id", flat=True
        )
    )

    active_cards: list[CohortCard] = []
    upcoming_cards: list[CohortCard] = []
    finished_cards: list[CohortCard] = []
    for cohort in cohorts:
        has_enrollment = cohort.id in enrolled_ids
        state = classify_cohort(cohort, today)
        if state == "active":
            active_cards.append(_active_cohort_card(user, cohort, has_enrollment, now))
        elif state == "upcoming":
            upcoming_cards.append(_upcoming_cohort_card(has_enrollment, cohort))
        else:
            finished_cards.append(_finished_cohort_card(user, cohort, has_enrollment))

    # Newest first, matching the cohort page's own recency ordering.
    finished_cards.sort(key=lambda card: card.cohort.year, reverse=True)

    due_soon: list[DeadlineItem] = []
    if len(active_cards) >= 2:
        window_end = now + timedelta(days=DUE_SOON_WINDOW_DAYS)
        for card in active_cards:
            for item in deadline_items_for_cohort(user, card.cohort, now):
                if item.due <= window_end:
                    due_soon.append(item)
        due_soon.sort(key=lambda item: item.due)
        due_soon = due_soon[:DUE_SOON_CAP]

    if cohorts:
        state = "state_2" if active_cards or upcoming_cards else "state_3"
    else:
        state = "state_1"

    family_ids = {cohort.course_id for cohort in cohorts}
    discover_limit = DISCOVER_CAP_STATE_1 if state == "state_1" else DISCOVER_CAP_OTHER_STATES
    discover = discover_cards(user, family_ids, discover_limit)

    checklist = checklist_items(user, dismissals)
    checklist_complete = all(item.complete for item in checklist)
    checklist_dismissed = bool(dismissals.get(CHECKLIST_DISMISSED))

    # The hero speaks for the cohorts the member actually has.  "Pick up where
    # you left off" is only true when something is running: with no active
    # cohort the hero names the next one that opens instead, which is §5's
    # state-4 lede and is honest whether there is one upcoming cohort or five.
    next_upcoming_cohort = None
    if not active_cards and upcoming_cards:
        next_upcoming_cohort = min(
            upcoming_cards, key=lambda card: card.cohort.start_date
        )

    your_courses_variant = "row_list" if len(active_cards) >= 4 else "cards"

    return {
        "member_home_state": state,
        "active_cards": active_cards,
        "upcoming_cards": upcoming_cards,
        "finished_cards": finished_cards,
        "your_courses_cards": active_cards + upcoming_cards,
        "your_courses_variant": your_courses_variant,
        "due_soon": due_soon,
        "show_due_soon": len(active_cards) >= 2,
        "discover_cards": discover,
        "discover_heading": (
            "Courses you can join today" if state == "state_1" else "More free courses"
        ),
        "upcoming_events": upcoming_events(),
        "checklist_items": checklist,
        "checklist_complete": checklist_complete,
        "checklist_dismissed": checklist_dismissed,
        "checklist_done_count": sum(1 for item in checklist if item.complete),
        "single_active_cohort": active_cards[0] if len(active_cards) == 1 else None,
        "next_upcoming_cohort": next_upcoming_cohort,
        "multi_active_cohorts": len(active_cards) > 1,
    }
