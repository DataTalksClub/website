from dataclasses import dataclass

from django.shortcuts import get_object_or_404
from django.utils import timezone

from courses.course_page_content import (
    course_modules,
    course_specs,
    family_capstone_project,
    family_edition_rows,
    family_facts,
    family_project_cards,
    family_registration_specs,
    family_story_rows,
    family_syllabus_rows,
    submission_progress,
)
from courses.models.cohort import (
    Cohort,
    Course,
    CourseRegistration,
    CurriculumFormat,
    DeliveryMode,
    Enrollment,
    RegistrationCampaign,
)
from courses.models.project import ProjectState
from courses.models.shared_curriculum import SharedLesson, SharedModule
from courses.models.testimonial import TestimonialPlacement
from courses.services.curriculum_flow import build_curriculum_flow
from courses.services.registration_campaigns import (
    FamilyRegistration,
    active_campaign_for_cohort,
    family_registration,
    next_edition_campaign_for_cohort,
)
from courses.services.registration_counts import public_course_registration_count
from courses.views.course_homepage import (
    add_course_homepage_info,
    course_duration_label,
)
from courses.views.course_homeworks import get_homeworks_for_course
from courses.views.course_projects import get_projects_for_course
from courses.views.url_utils import get_cohort_or_404


@dataclass(frozen=True)
class CoursePageData:
    course: Cohort
    user: object
    homeworks: list
    projects: list
    registration_campaign: object
    next_edition_campaign: object = None


@dataclass(frozen=True)
class CourseFamilyEdition:
    """Public data for one visible edition on a course-family page."""

    cohort: Cohort
    projects: list


def active_registration_campaign_for_course(
    course: Cohort,
) -> RegistrationCampaign | None:
    """The campaign promoting this exact edition. One definition, owned by the service."""

    return active_campaign_for_cohort(course)


def should_redirect_to_registration_campaign(
    *,
    registration_campaign: RegistrationCampaign | None,
    homeworks,
    projects,
    user,
) -> bool:
    return (
        registration_campaign is not None
        and not homeworks
        and not projects
        and not user.is_staff
    )


def has_completed_projects(projects) -> bool:
    for project in projects:
        if project.state == ProjectState.COMPLETED.value:
            return True
    return False


def _authenticated_course_progress(
    user,
    course: Cohort,
    registration_campaign: RegistrationCampaign | None,
) -> dict:
    context = _course_enrollment_progress(user, course)
    context["has_registration"] = _has_course_registration(
        user,
        registration_campaign,
    )
    return context


def _course_enrollment_progress(user, course: Cohort) -> dict:
    try:
        enrollment = Enrollment.objects.get(
            student=user,
            course=course,
        )
    except Enrollment.DoesNotExist:
        return {
            "has_enrollment": False,
            "total_score": None,
            "certificate_url": None,
        }

    return {
        "has_enrollment": True,
        "total_score": enrollment.total_score,
        "certificate_url": enrollment.certificate_url,
    }

def _has_course_registration(
    user,
    registration_campaign: RegistrationCampaign | None,
) -> bool:
    if not registration_campaign:
        return False

    email = user.email or ""
    stripped_email = email.strip()
    email_normalized = stripped_email.lower()
    return CourseRegistration.objects.filter(
        campaign=registration_campaign,
        email_normalized=email_normalized,
    ).exists()


def course_user_context(
    user,
    course: Cohort,
    registration_campaign: RegistrationCampaign | None,
) -> dict:
    if not user.is_authenticated:
        return {
            "has_enrollment": False,
            "total_score": None,
            "certificate_url": None,
            "has_registration": False,
        }

    return _authenticated_course_progress(
        user,
        course,
        registration_campaign,
    )


def registered_learner_count(
    registration_campaign: RegistrationCampaign | None,
) -> int | None:
    """Return the published registration total, or ``None`` when there is no published count.

    This is the same accessor the registration page renders, so the two surfaces can never
    quote different numbers for one cohort.
    """

    if registration_campaign is None:
        return None
    public_count = public_course_registration_count(registration_campaign)
    if public_count is None:
        return None
    return public_count.count


def course_page_context(data: CoursePageData) -> dict:
    has_completed_course_projects = has_completed_projects(data.projects)
    modules = course_modules(data.homeworks, data.projects)
    is_module_curriculum = data.course.curriculum_format in (
        CurriculumFormat.MODULES,
        CurriculumFormat.SHARED,
    )
    curriculum_flow = build_curriculum_flow(
        data.course,
        data.homeworks,
        data.projects,
    )
    signup_count = registered_learner_count(data.registration_campaign)
    context = {
        "course": data.course,
        "course_family": data.course.course,
        "course_slug": data.course.course.slug,
        "cohort_identifier": data.course.identifier,
        "homeworks": data.homeworks,
        "projects": data.projects,
        "course_modules": modules,
        "curriculum_flow": curriculum_flow,
        "is_module_curriculum": is_module_curriculum,
        "course_specs": course_specs(
            data.course,
            homework_count=len(data.homeworks),
            project_count=len(data.projects),
            signup_count=signup_count,
        ),
        "signup_count": signup_count,
        "has_completed_projects": has_completed_course_projects,
        "is_authenticated": data.user.is_authenticated,
        "registration_campaign": data.registration_campaign,
        "next_edition_campaign": data.next_edition_campaign,
    }
    course_editions = visible_course_editions(data.course)
    context["course_editions"] = course_editions
    context["other_course_editions"] = [
        edition
        for edition in course_editions
        if edition.pk != data.course.pk
    ]
    user_context = course_user_context(
        data.user,
        data.course,
        data.registration_campaign,
    )
    context.update(user_context)
    # The submitted counts are this learner's own, so the bar is only drawn for the
    # signed-in learner whose enrolment produced them.
    context["submission_progress"] = (
        submission_progress(modules)
        if data.user.is_authenticated and user_context["has_enrollment"]
        else None
    )
    return context


def course_page_data(
    course_slug: str,
    user,
    cohort_identifier: str | int | None = None,
) -> CoursePageData:
    course = get_cohort_or_404(course_slug, cohort_identifier)
    now = timezone.now()
    add_course_homepage_info(course, now)
    homeworks = get_homeworks_for_course(course, user)
    projects = get_projects_for_course(course, user)
    registration_campaign = active_registration_campaign_for_course(
        course
    )
    # Only a closed edition offers the next one, so this is never a second Register
    # button beside the first: the template reaches it only when no campaign promotes
    # this edition.
    next_edition_campaign = (
        None
        if registration_campaign is not None
        else next_edition_campaign_for_cohort(course)
    )
    return CoursePageData(
        course=course,
        user=user,
        homeworks=homeworks,
        projects=projects,
        registration_campaign=registration_campaign,
        next_edition_campaign=next_edition_campaign,
    )


def course_family_page_data(course_slug: str):
    return get_object_or_404(
        Course.objects.prefetch_related("cohorts"),
        slug=course_slug,
        visible=True,
    )


def visible_course_editions(course: Cohort) -> list[Cohort]:
    """Return the visible editions in the family, newest first."""

    return list(
        Cohort.objects.filter(
            course=course.course,
            visible=True,
        ).order_by("-year", "-id")
    )


def family_lede(family: Course) -> str:
    """Return the family's one-line lede, or nothing when it would repeat the heading.

    ``Course.outcome`` is the written promise and is preferred whenever it exists.
    ``Course.description`` is a fallback that is only sometimes prose: a family whose
    repository has no ``SITE.md`` pointer still carries the placeholder description CMP
    seeded, which is the title again -- and a family that does have one carries raw
    README markdown, heading marker and all.  Neither belongs under an ``h1`` that
    already says the same thing, so the comparison is on normalised text rather than on
    a list of named courses: every course whose description was never written is covered,
    not the three that happen to be unwritten today.
    """

    if family.outcome.strip():
        return family.outcome.strip()
    description = family.description.strip()
    if not description or "\n" in description or description.startswith("#"):
        # Multi-line or heading-prefixed text is raw README markdown, not a lede; the
        # template renders plain text, so it would leak the markers verbatim.
        return ""
    if description.casefold() == family.title.strip().casefold():
        return ""
    return description


def course_family_page_context(family: Course, user) -> dict:
    """Build the family landing context without changing cohort view logic."""

    today = timezone.localdate(timezone.now())
    editions = [
        CourseFamilyEdition(
            cohort=cohort,
            projects=get_projects_for_course(cohort, user),
        )
        for cohort in visible_course_editions_for_family(family)
    ]
    registration: FamilyRegistration = family_registration(family)
    registration_cohort = registration.cohort
    front_cohort = registration_cohort or (editions[0].cohort if editions else None)
    registered = registered_learner_count(registration.campaign)
    materials_url = family.github_repo_url or next(
        (
            edition.cohort.github_repo_url
            for edition in editions
            if edition.cohort.github_repo_url
        ),
        "",
    )
    self_paced_cohort = next(
        (
            edition.cohort
            for edition in editions
            if edition.cohort.delivery_mode == DeliveryMode.SELF_PACED.value
        ),
        None,
    )
    front_edition = next(
        (edition for edition in editions if edition.cohort.pk == front_cohort.pk),
        None,
    )
    front_projects = front_edition.projects if front_edition else []
    # The syllabus band reads the family's shared curriculum when an import
    # created one and the front cohort's homework list otherwise; a family with
    # neither draws no syllabus at all.
    shared_modules = list(
        SharedModule.objects.filter(
            curriculum__course=family,
            published=True,
            retired_at__isnull=True,
        ).order_by("position", "id")
    )
    if shared_modules:
        syllabus_units: list = shared_modules
        lesson_count = SharedLesson.objects.filter(
            module__in=shared_modules,
            published=True,
            retired_at__isnull=True,
        ).count()
        syllabus_fact = f"{len(shared_modules)} modules · {lesson_count} lessons"
    else:
        syllabus_units = (
            get_homeworks_for_course(front_cohort, user) if front_cohort else []
        )
        syllabus_fact = f"{len(syllabus_units)} homeworks" if syllabus_units else ""
    project_cards = family_project_cards(editions)
    return {
        "course_family": family,
        "cohorts": [edition.cohort for edition in editions],
        "cohort_editions": editions,
        "family_edition_rows": family_edition_rows(
            editions,
            registration_cohort,
            today,
        ),
        "family_registration": registration,
        "registration_cohort": registration_cohort,
        "registration_specs": family_registration_specs(
            registration_cohort,
            registered,
        ),
        "family_facts": family_facts(
            editions,
            course_duration_label(front_cohort) if front_cohort else "TBA",
            registered if registration_cohort else None,
        ),
        "materials_url": materials_url,
        "self_paced_cohort": self_paced_cohort,
        "family_lede": family_lede(family),
        "front_cohort": front_cohort,
        "family_syllabus_rows": family_syllabus_rows(syllabus_units),
        "syllabus_fact": syllabus_fact,
        "syllabus_capstone": family_capstone_project(front_projects),
        "family_stories": family_story_rows(
            family.testimonials.filter(
                placement=TestimonialPlacement.COURSE,
                published=True,
            )[:3]
        ),
        "family_project_cards": project_cards,
        "built_cohort": project_cards[0].cohort if project_cards else None,
    }


def visible_course_editions_for_family(family: Course) -> list[Cohort]:
    """Return only public editions for the family landing page."""

    return list(
        family.cohorts.filter(visible=True).order_by("-year", "-id")
    )
