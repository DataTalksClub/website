import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from content.docs_reader import docs_page
from content.faq_data import faq_course_for_family_slug, render_faq_answer
from courses.course_page_content import (
    CourseSpec,
    course_modules,
    course_specs,
    family_edition_rows,
    family_outcome_stats,
    family_project_cards,
    family_registration_specs,
    family_story_rows,
    family_syllabus_rows,
    merge_syllabus_rows_with_projects,
    split_current_edition,
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
from courses.models.shared_curriculum import CohortSharedModule, SharedLesson, SharedModule
from courses.models.testimonial import TestimonialPlacement
from courses.registration_wall import registration_wall_context
from courses.services.curriculum_flow import build_curriculum_flow
from courses.services.registration_campaigns import (
    FamilyRegistration,
    active_campaign_for_cohort,
    family_registration,
    next_edition_campaign_for_cohort,
)
from courses.services.registration_counts import (
    public_course_registration_count,
    public_family_registration_count,
)
from courses.views.course_homepage import add_course_homepage_info
from courses.views.course_homeworks import get_homeworks_for_course
from courses.views.course_projects import get_projects_for_course
from courses.views.project_gallery_groups import family_project_submissions
from courses.views.site_project_gallery import _repository_identity
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
    # Same reasoning as the family landing's materials link: a cohort whose
    # curriculum was imported (real SharedModule rows) keeps the visitor on
    # the platform instead of sending them to the source repository.
    materials_url = data.course.github_repo_url
    materials_on_platform = False
    if data.course.curriculum_format == CurriculumFormat.SHARED:
        first_module = (
            SharedModule.objects.filter(
                curriculum__course=data.course.course,
                published=True,
                retired_at__isnull=True,
            )
            .order_by("position", "id")
            .first()
        )
        if first_module is not None:
            materials_url = reverse(
                "shared_module",
                args=[data.course.course.slug, first_module.slug],
            )
            materials_on_platform = True
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
        "materials_url": materials_url,
        "materials_on_platform": materials_on_platform,
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
        edition for edition in course_editions if edition.pk != data.course.pk
    ]
    user_context = course_user_context(
        data.user,
        data.course,
        data.registration_campaign,
    )
    context.update(user_context)
    context.update(registration_wall_context(data.course, data.user))
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
    registration_campaign = active_registration_campaign_for_course(course)
    # Only a closed edition offers the next one, so this is never a second Register
    # button beside the first: the template reaches it only when no campaign promotes
    # this edition.
    next_edition_campaign = (
        None if registration_campaign is not None else next_edition_campaign_for_cohort(course)
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


def family_docs_path(family: Course) -> str:
    """Return the family's docs section as a relative site path, or nothing.

    ``Course.docs_url`` is stored as the absolute same-site URL the curriculum
    importer read from the course repository; the family page links same-site
    destinations with a relative path, never an absolute host or a new tab. A
    stored URL can drift from the synced docs tree (a page renamed or removed
    upstream), so this only returns the path when the docs projection still
    publishes that exact page -- a family with no ``docs_url`` (``ml-zoomcamp``
    today) or a stale one renders no docs link rather than a dead one.
    """

    docs_url = family.docs_url.strip()
    if not docs_url:
        return ""
    parsed = urlsplit(docs_url)
    if parsed.netloc and parsed.netloc != "datatalks.club":
        return ""
    path = parsed.path
    if not path.startswith("/docs/") or docs_page(path) is None:
        return ""
    return path


#: How many real FAQ questions the family landing page previews inline
#: before pointing to the full FAQ page for the rest.
FAMILY_FAQ_PREVIEW_LIMIT = 5

#: How many real project submissions the family landing page shows inline
#: before pointing to the full family project gallery for the rest.
FAMILY_GALLERY_PREVIEW_LIMIT = 6


def family_faq_preview(family: Course, *, limit: int = FAMILY_FAQ_PREVIEW_LIMIT) -> tuple:
    """Real FAQ questions to preview inline, and the link to the rest.

    Matches the family to its published FAQ document by slug
    (``content.faq_data.faq_course_for_family_slug``) and renders its first
    *limit* questions the same way the FAQ page itself does.  A family with no
    matching document falls back to its own legacy external FAQ link (or to
    nothing at all when it carries neither) rather than showing an empty or
    fabricated preview.
    """

    course = faq_course_for_family_slug(family.slug)
    if course is None:
        return (), family.faq_document_url
    questions = [
        (question, section) for section in course["sections"] for question in section["questions"]
    ][:limit]
    if not questions:
        return (), family.faq_document_url
    rows = tuple(
        {
            "id": question["id"],
            "question": faq_question_without_section_label(
                question["question"], section_title(section)
            ),
            "rendered_answer": render_faq_answer(question),
        }
        for question, section in questions
    )
    return rows, course["public_path"]


def section_title(section: dict) -> str:
    return str(section.get("title") or section.get("name") or "")


# FAQ sources file a question under a category by prefixing it: "Course: When
# does the course start?", "Course - Can I still join?".  Inside the FAQ
# document the prefix sorts the questions; previewed on a course's own landing
# page under one heading it only repeats the section the question came from.
_FAQ_CATEGORY_PREFIX = re.compile(r"^\s*([A-Z][\w&/ ]{0,30}?)\s*(?::|\s-\s)\s*(?=\S)")


def faq_question_without_section_label(question: str, section: str) -> str:
    """Drop a question's category prefix when its section already names it.

    The prefix is removed only when its words appear in the question's own
    section title ("Course" inside "General Course-Related Questions"); a
    prefix that adds information ("Leaderboard: ...") is kept.
    """

    match = _FAQ_CATEGORY_PREFIX.match(question)
    if not match:
        return question
    label = match.group(1).strip().casefold()
    if not label or label not in section.casefold():
        return question
    remainder = question[match.end() :]
    return remainder[:1].upper() + remainder[1:]


@dataclass(frozen=True)
class QuickFaqItem:
    """One of the catalogue's objection-answering FAQ entries, reused verbatim."""

    keywords: tuple[str, ...]
    question: str
    answer: str


#: The four objection-answering questions the ``/courses`` catalogue already
#: answers well (``courses/templates/courses/course_list.html``'s
#: ``.faq-grid``): is it free, how much time it takes, whether a late start is
#: fine, and the certificate rule.  A course family's own real FAQ document
#: preview (``family_faq_preview``, above) shows the document's first five
#: questions in source order, which for every real course today are general
#: orientation questions, not these -- so a visitor who never expands "See the
#: full course FAQ" never sees them.  Reusing this exact, already-published
#: copy here closes that gap without inventing new marketing text.
QUICK_FAQ_ITEMS: tuple[QuickFaqItem, ...] = (
    QuickFaqItem(
        keywords=("free",),
        question="Is it really free?",
        answer=(
            "Yes. Every video, homework and solution stays public — there is "
            "nothing to pay and nothing to upsell."
        ),
    ),
    QuickFaqItem(
        keywords=("join", "late start", "already started"),
        question="Can I join after a cohort starts?",
        answer=(
            "Yes, while it is still running. Catch up on the recordings and "
            "submit whatever homework is still open."
        ),
    ),
    QuickFaqItem(
        keywords=("how much time", "hour", "time do i need"),
        question="How much time do I need?",
        answer=(
            "Enough for the weekly videos plus hands-on practice. Most people "
            "do it alongside a full-time job — each course's own syllabus lays "
            "out its pace."
        ),
    ),
    QuickFaqItem(
        keywords=("certificate",),
        question="Do I get a certificate?",
        answer="To get a certificate you need to pass a project.",
    ),
)


def family_quick_faq_items(
    family_faq_questions: tuple,
    *,
    family_has_a_real_cohort: bool,
) -> tuple[QuickFaqItem, ...]:
    """The catalogue's objection-answering items this family doesn't already show.

    Reused only for a family that actually runs a course (``family_has_a_real_cohort``)
    -- a family with no cohort at all has nothing these generic, always-true zoomcamp
    facts would be answering.  An item is dropped when the family's own inline FAQ
    preview already asks something close enough (its question text contains one of the
    item's keywords), so the same question is never shown twice on one page.
    """

    if not family_has_a_real_cohort:
        return ()
    covered = " ".join(question["question"].casefold() for question in family_faq_questions)
    return tuple(
        item for item in QUICK_FAQ_ITEMS if not any(keyword in covered for keyword in item.keywords)
    )


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
    family_registered = public_family_registration_count(
        registration.campaign,
        family,
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
        syllabus_urls = [
            reverse("shared_module", args=[family.slug, module.slug]) for module in shared_modules
        ]
        lesson_count = SharedLesson.objects.filter(
            module__in=shared_modules,
            published=True,
            retired_at__isnull=True,
        ).count()
        # A lesson count that only repeats the module count ("4 modules · 4
        # lessons") says nothing; it is named only when modules hold more.
        syllabus_fact = f"{len(shared_modules)} modules" + (
            f" · {lesson_count} lessons" if lesson_count > len(shared_modules) else ""
        )
        # A shared module carries no due date of its own -- it is shared
        # across every cohort of the family -- so the front cohort's own
        # placement (its terminal homework's real due date) stands in for
        # it when one exists, the same binding the cohort page's own
        # curriculum flow already reads (courses/services/curriculum_flow.py).
        placements_by_module_id = (
            {
                placement.shared_module_id: placement
                for placement in CohortSharedModule.objects.filter(
                    cohort=front_cohort, shared_module__in=shared_modules
                ).select_related("terminal_homework")
            }
            if front_cohort
            else {}
        )
        unit_due_dates = [
            placement.terminal_homework.due_date
            if (placement := placements_by_module_id.get(module.pk))
            and placement.terminal_homework_id
            else None
            for module in shared_modules
        ]
    else:
        syllabus_units = get_homeworks_for_course(front_cohort, user) if front_cohort else []
        syllabus_fact = f"{len(syllabus_units)} homeworks" if syllabus_units else ""
        syllabus_urls = [
            reverse(
                "cohort_homework",
                args=[family.slug, front_cohort.identifier, homework.slug],
            )
            for homework in syllabus_units
        ]
        unit_due_dates = [getattr(unit, "due_date", None) for unit in syllabus_units]
    # A family whose curriculum has been imported (``shared_modules`` real rows)
    # keeps every visitor on the platform: the materials route opens the first
    # module page instead of sending anyone to the source repository. A family
    # that hasn't been imported yet has no module pages to send them to, so the
    # repository stays the only honest destination.
    materials_on_platform = bool(shared_modules)
    if materials_on_platform:
        materials_url = reverse(
            "shared_module",
            args=[family.slug, shared_modules[0].slug],
        )
    else:
        materials_url = family.github_repo_url or next(
            (
                edition.cohort.github_repo_url
                for edition in editions
                if edition.cohort.github_repo_url
            ),
            "",
        )
    project_cards = family_project_cards(editions)
    syllabus_rows = family_syllabus_rows(syllabus_units, urls=syllabus_urls)
    if front_projects:
        project_urls = [
            reverse(
                "cohort_project",
                kwargs={
                    "course_slug": family.slug,
                    "cohort_identifier": front_cohort.identifier,
                    "project_slug": project.slug,
                },
            )
            for project in front_projects
        ]
        syllabus_rows = merge_syllabus_rows_with_projects(
            syllabus_rows, unit_due_dates, front_projects, project_urls
        )
    cohorts = [edition.cohort for edition in editions]
    # The outcome strip's "project submissions" stat and the inline gallery
    # below both read the same family-wide submissions queryset
    # (courses/views/project_gallery_groups.py already excludes hidden
    # cohorts and volunteer-review-only rows), so it is built once here
    # rather than twice. ``submission_count``/``has_learner_projects`` stay
    # unfiltered by grading state -- they also feed the outcome-stats strip
    # and the course-journey stage-3 proof foot, which count every
    # submission the family has ever collected, not just the passed ones.
    family_submissions = family_project_submissions(family)
    submission_count = family_submissions.count()
    has_learner_projects = submission_count > 0
    gallery_submissions = []
    if has_learner_projects:
        # This inline preview shows no vote/score/pass badge any more (owner
        # feedback), so every row rendered here must actually have passed --
        # filtered at this section's own level, the same way the family/site
        # project galleries filter themselves in
        # courses/views/site_project_gallery.py, rather than in
        # family_project_submissions() itself, which the stats above still
        # need unfiltered.
        gallery_submissions = list(
            family_submissions.filter(passed=True)[:FAMILY_GALLERY_PREVIEW_LIMIT]
        )
        for submission in gallery_submissions:
            # ``Project.course`` is the submission's cohort (confusingly
            # named; see courses/models/project.py) -- alias it as
            # ``.cohort`` the same way the full family/site galleries do.
            submission.cohort = submission.project.course
            submission.repository_label, submission.repository_url = _repository_identity(
                submission.github_link
            )
            # The card names the repository, not the host it lives on.
            # A repository path has no spaces to wrap at; a zero-width break
            # after each separator lets a long one wrap at "/" or "_" rather
            # than mid-word.
            submission.repository_label = (
                submission.repository_label.removeprefix("github.com/")
                .replace("/", "/\u200b")
                .replace("_", "_\u200b")
            )
    # The same family-wide total the outcome strip publishes, written as the
    # journey's stage-3 proof reads it ("1,874 projects"); one count, one
    # queryset, so the two places on the page can never disagree.
    submissions_fact = (
        f"{submission_count:,} project{'' if submission_count == 1 else 's'}"
        if submission_count
        else ""
    )
    enrolled_count = Enrollment.objects.filter(course__course=family, course__visible=True).count()
    certificate_count = (
        Enrollment.objects.filter(
            course__course=family,
            course__visible=True,
            certificate_url__isnull=False,
        )
        .exclude(certificate_url="")
        .count()
    )
    since_year = min((cohort.year for cohort in cohorts), default=None)
    outcome_stats = family_outcome_stats(
        enrolled_count,
        certificate_count,
        submission_count,
        since_year,
        registration_count=(family_registered.count if family_registered else None),
        cohort_count=len(cohorts),
    )
    project_brief = next((card for card in project_cards if card.project.instructions_url), None)
    transformation = family.progression if len(family.progression) == 3 else []
    certificate_cohort = (
        front_cohort
        if front_cohort
        and front_cohort.delivery_mode == DeliveryMode.LIVE
        and not front_cohort.finished
        and (not front_cohort.end_date or front_cohort.end_date >= today)
        and front_projects
        else None
    )
    description = family.description.strip()
    # Preserve curated learning notes without exposing a raw README as hero copy.
    overview = (
        description
        if description
        and not description.startswith("#")
        and description.casefold()
        not in {family.title.strip().casefold(), family_lede(family).casefold()}
        else ""
    )
    # The source text is hard-wrapped at its own column; a single newline is
    # a wrap, not a break, so only a blank line starts a new paragraph.
    overview = re.sub(r"(?<!\n)\n(?!\n)", " ", overview)
    current_edition_row, previous_edition_rows = split_current_edition(
        family_edition_rows(
            editions,
            registration_cohort,
            today,
        )
    )
    family_faq_questions, family_faq_url = family_faq_preview(family)
    first_module_row = next(
        (row for row in syllabus_rows if not row.is_project),
        None,
    )
    # No open campaign, but a cohort to join: registering is enrolling in it,
    # which is what tracks a learner's progress (the owner's flow: register
    # first, then start module 1).  Only where the curriculum is on the
    # platform: a GitHub-only family has nothing here to track.  Self-paced
    # and approval-walled cohorts keep their own routes.
    enroll_cohort = (
        front_cohort
        if registration.campaign is None
        and materials_on_platform
        and front_cohort is not None
        and self_paced_cohort is None
        and not front_cohort.requires_registration_approval
        else None
    )
    quick_faq_items = family_quick_faq_items(
        family_faq_questions,
        family_has_a_real_cohort=front_cohort is not None,
    )
    is_authenticated = bool(getattr(user, "is_authenticated", False))
    enrolled_cohort = None
    if is_authenticated:
        enrollment = (
            Enrollment.objects.filter(
                student=user,
                course__course=family,
                course__visible=True,
            )
            .select_related("course")
            .order_by("-course__year", "-course__id")
            .first()
        )
        if enrollment is not None:
            enrolled_cohort = enrollment.course
    return {
        "course_family": family,
        "is_authenticated": is_authenticated,
        "enrolled_cohort": enrolled_cohort,
        **registration_wall_context(registration_cohort, user),
        "cohorts": cohorts,
        "cohort_editions": editions,
        "current_edition_row": current_edition_row,
        "previous_edition_rows": previous_edition_rows,
        "family_registration": registration,
        "registration_cohort": registration_cohort,
        "registration_specs": family_registration_specs(
            registration_cohort,
            registered,
        ),
        "materials_url": materials_url,
        "materials_on_platform": materials_on_platform,
        "family_faq_questions": family_faq_questions,
        "family_faq_url": family_faq_url,
        "family_quick_faq_items": quick_faq_items,
        "self_paced_cohort": self_paced_cohort,
        "enroll_cohort": enroll_cohort,
        "family_lede": family_lede(family),
        "family_docs_path": family_docs_path(family),
        "family_starting_point": family.starting_point.strip(),
        "family_prerequisites": family.prerequisites.strip(),
        "family_weekly_commitment": family.weekly_commitment.strip(),
        "family_transformation": transformation,
        "family_overview": overview,
        "front_cohort": front_cohort,
        "family_syllabus_rows": syllabus_rows,
        "family_outcome_stats": outcome_stats,
        "has_learner_projects": has_learner_projects,
        "family_gallery_submissions": gallery_submissions,
        "family_submissions_fact": submissions_fact,
        "family_project_brief": project_brief,
        "family_project_summary": family.project_summary.strip(),
        "family_tools": tuple(
            tool.strip() for tool in (family.catalog_tools or ()) if str(tool).strip()
        ),
        "certificate_cohort": certificate_cohort,
        "syllabus_fact": syllabus_fact,
        "family_facts": family_hero_facts(
            registration_cohort=registration_cohort,
            format_label=family_format_label(
                family,
                cohorts=[edition.cohort for edition in editions],
                registration_cohort=registration_cohort,
                today=today,
            ),
            syllabus_fact=syllabus_fact,
            weekly_commitment=family.weekly_commitment.strip(),
        ),
        "family_first_module": first_module_row if materials_on_platform else None,
        "syllabus_has_brief": any(row.brief_url for row in syllabus_rows),
        # The landing ends on its own closing action; the site-wide Academy
        # and tour stripes under it would make three closing calls in a row.
        "hide_academy_cta": True,
        "hide_tour_cta": True,
        "family_stories": family_story_rows(
            family.testimonials.filter(
                placement=TestimonialPlacement.COURSE,
                published=True,
            )[:3]
        ),
        "family_project_cards": project_cards,
        "built_cohort": project_cards[0].cohort if project_cards else None,
    }


def family_format_label(
    family: Course,
    *,
    cohorts,
    registration_cohort,
    today,
) -> str:
    """How a family runs, in words: "Live cohort or self-paced", "Self-paced".

    One reading for every surface that states it -- the family hero's Format
    fact and the ``/courses`` catalogue card's facts line -- so the two cannot
    disagree.  A family is *live* while a cohort is open for registration or
    under way, and *self-paced* when an edition is delivered self-paced or the
    materials are reachable (imported module pages or a source repository).
    A family the records cannot place in either says nothing.
    """

    editions = sorted(cohorts, key=lambda cohort: (cohort.year, cohort.id), reverse=True)
    current_row, _previous = split_current_edition(
        family_edition_rows(
            [CourseFamilyEdition(cohort=cohort, projects=[]) for cohort in editions],
            registration_cohort,
            today,
        )
    )
    live = registration_cohort is not None or (
        current_row is not None and current_row.state_words == "in progress"
    )
    self_paced = (
        any(cohort.delivery_mode == DeliveryMode.SELF_PACED.value for cohort in editions)
        or bool(family.github_repo_url)
        or any(cohort.github_repo_url for cohort in editions)
        or SharedModule.objects.filter(
            curriculum__course=family,
            published=True,
            retired_at__isnull=True,
        ).exists()
    )
    formats = [
        words for words, present in (("live cohort", live), ("self-paced", self_paced)) if present
    ]
    return " or ".join(formats).capitalize()


def family_hero_facts(
    *,
    registration_cohort,
    format_label: str,
    syllabus_fact: str,
    weekly_commitment: str,
) -> tuple[CourseSpec, ...]:
    """The facts at the foot of the family hero, only from data the family has.

    The same label-over-value strip an Academy program closes its title block
    with: when the next cohort starts, how the course runs, how big the
    syllabus is and the weekly time it asks.  A fact the records cannot back
    is left out rather than guessed.
    """

    facts: list[CourseSpec] = []
    if format_label or syllabus_fact:
        facts.append(CourseSpec("Cost", "Free"))
    if registration_cohort is not None and registration_cohort.start_date:
        facts.append(
            CourseSpec(
                "Next cohort",
                registration_cohort.start_date.strftime("%b %-d, %Y"),
            )
        )
    if format_label:
        facts.append(CourseSpec("Format", format_label))
    if syllabus_fact:
        facts.append(CourseSpec("Syllabus", syllabus_fact))
    if weekly_commitment:
        facts.append(CourseSpec("Time", weekly_commitment))
    return tuple(facts)


def visible_course_editions_for_family(family: Course) -> list[Cohort]:
    """Return only public editions for the family landing page."""

    return list(family.cohorts.filter(visible=True).order_by("-year", "-id"))
