"""Views for the shared current curriculum pages.

``/courses/<family>/<module>`` and ``/courses/<family>/<module>/<lesson>`` are
canonical, cohort-free paths: one current curriculum, one page per module and
lesson, shared by every delivery.  The delivery panel (cohort, homework links,
archive notice) is selected by :mod:`courses.services.course_context` and never
changes the page body or the canonical path.
"""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from courses.models import (
    Cohort,
    CohortSharedModule,
    Course,
    DeliveryMode,
    SharedCurriculum,
    SharedLesson,
    SharedModule,
)
from courses.services.course_context import (
    context_query,
    delivery_choices,
    remember_cohort,
    resolve_delivery_context,
)
from courses.models.project import Project, ProjectState
from courses.services.attempt_flow import attempt_rows, learner_attempts
from courses.templatetags.attempt_rail import ATTEMPT_STATUS_PILLS
from courses.services.course_rail import course_rail, lessons_of, rail_placement
from courses.services.module_syllabus import module_syllabus
from courses.services.registration_campaigns import family_registration
from courses.views.homework_states import learner_homework_state


def _course_or_404(course_slug: str) -> Course:
    return get_object_or_404(
        Course.objects.filter(visible=True),
        slug=course_slug,
    )


def _apply_context(request: HttpRequest, course: Course, explicit_identifier=None):
    """Resolve the delivery context and mark privacy when it varies."""

    context = resolve_delivery_context(request, course, explicit_identifier)
    if context.source == "explicit" and context.cohort is not None:
        remember_cohort(request, course, context.cohort)
    if context.varies_response:
        request.private_response_required = True
    return context


def _delivery_panel(request: HttpRequest, course: Course, context) -> dict[str, object]:
    placements: list[CohortSharedModule] = []
    archive_cohort = None
    cohort = context.cohort
    if cohort is not None:
        if cohort.curriculum_source == "github_archive":
            archive_cohort = cohort
        elif cohort.curriculum_format == "shared":
            placements = list(
                CohortSharedModule.objects.filter(cohort=cohort)
                .select_related(
                    "shared_module",
                    "terminal_homework",
                )
                .order_by("position", "id")
            )
    return {
        "delivery_cohort": cohort,
        "delivery_source": context.source,
        "delivery_enrollment": context.enrollment,
        "delivery_choices": (delivery_choices(course, request) if context.needs_chooser else []),
        "delivery_multiple_enrollments": context.has_multiple_enrollments,
        "delivery_placements": placements,
        "archive_cohort": archive_cohort,
        "self_paced": (cohort is not None and cohort.delivery_mode == DeliveryMode.SELF_PACED),
        "context_q": context_query(context.cohort),
    }


def _registration_action(course: Course) -> dict[str, str] | None:
    """Where a visitor who is not signed in starts: the family's open registration."""

    registration = family_registration(course)
    if not registration:
        return None
    return {
        "label": "Register to start lesson 1",
        "note": "",
        "aria": "Register for the course to start lesson 1",
        "url": reverse("registration_campaign", args=[registration.campaign.slug]),
    }


def _module_action(
    *,
    user,
    course: Course,
    syllabus_lessons,
    next_module,
    next_module_url: str,
    homework,
    homework_url: str,
):
    """The module page's one primary action, from the reader's own progress.

    Start lesson 1, continue with the first unread lesson, then go on to the
    next module.  A visitor who is not signed in is sent into registration
    while the course has one open.
    """

    if not syllabus_lessons:
        return None
    first = syllabus_lessons[0]

    def lesson_action(label, row):
        return {
            "label": label,
            "url": row.url,
            "note": row.lesson.title,
            "aria": f"{label}: {row.lesson.title}",
        }

    if not getattr(user, "is_authenticated", False):
        return _registration_action(course) or lesson_action("Start lesson 1", first)
    unread = next((row for row in syllabus_lessons if not row.is_read), None)
    if unread is first:
        return lesson_action("Start lesson 1", first)
    if unread is not None:
        return lesson_action(f"Continue with lesson {unread.number}", unread)
    if next_module is not None:
        label = f"Continue to module {next_module.number}"
        return {
            "label": label,
            "url": next_module_url,
            "note": next_module.module.title,
            "aria": f"{label}: {next_module.module.title}",
        }
    if homework is not None and homework_url:
        return {
            "label": "Open the homework",
            "url": homework_url,
            "note": homework.title,
            "aria": f"Open {homework.title}",
        }
    return None


def _append_context(path: str, context_q: str) -> str:
    return f"{path}{context_q}" if context_q else path


def shared_module_view(
    request: HttpRequest,
    course_slug: str,
    module_slug: str,
) -> HttpResponse:
    """One shared module of the course's one current curriculum.

    A two-segment URL that names no shared module is the old cohort-page
    shape: when the second segment names a cohort of this family, one hop
    redirects to the canonical ``/cohorts/<identifier>`` path.  Anything else
    is a real 404.
    """

    course = _course_or_404(course_slug)
    module = (
        SharedModule.objects.filter(
            curriculum__course=course,
            slug=module_slug,
            published=True,
        )
        .select_related("curriculum", "curriculum__course")
        .first()
    )
    if module is None:
        return _legacy_two_segment_fallback(request, course, module_slug)

    context = _apply_context(request, course)
    panel = _delivery_panel(request, course, context)
    context_q = panel["context_q"]
    lessons = lessons_of(module)
    rail = course_rail(request.user, module, context_q)
    syllabus = module_syllabus(
        module,
        lessons,
        lesson_url=lambda lesson: _append_context(
            reverse("shared_lesson", args=[course.slug, module.slug, lesson.slug]), context_q
        ),
        read_ids=rail.read_lesson_ids,
    )
    syllabus_lessons = [row for part in syllabus.parts for row in part.lessons]
    current = next((item for item in rail.modules if item.is_current), None)
    next_module = (
        rail.modules[current.number] if current and current.number < len(rail.modules) else None
    )
    placement = rail_placement(course, module, panel["delivery_cohort"])
    rail_cohort = placement.cohort if placement else None
    homework = placement.terminal_homework if placement else None
    homework_url = (
        reverse("cohort_homework", args=[course.slug, rail_cohort.identifier, homework.slug])
        if homework
        else ""
    )
    homework_state = (
        learner_homework_state(request.user, homework)
        if homework is not None and request.user.is_authenticated
        else None
    )
    read_count = sum(1 for row in syllabus_lessons if row.is_read)
    project_rows = []
    if rail_cohort is not None:
        projects = list(
            Project.objects.filter(course=rail_cohort, shared_module=module)
            .exclude(state=ProjectState.CLOSED.value)
            .select_related("course__course", "criteria_set")
            .order_by("submission_due_date", "pk")
        )
        if projects:
            project_rows = attempt_rows(learner_attempts(request.user, rail_cohort, projects))
            for row in project_rows:
                row["pill"] = ATTEMPT_STATUS_PILLS.get(row["status_key"], "")
    canonical_path = reverse(
        "shared_module", kwargs={"course_slug": course.slug, "module_slug": module.slug}
    )
    return render(
        request,
        "courses/shared_module.html",
        {
            "course_family": course,
            "module": module,
            "lessons": lessons,
            "syllabus": syllabus,
            "rail": rail,
            "rail_cohort": rail_cohort,
            "rail_homework": homework,
            "rail_homework_state": homework_state,
            "homework_url": homework_url,
            "homework_question_count": homework.question_set.count() if homework else 0,
            "project_rows": project_rows,
            "read_count": read_count,
            "read_percent": round(100 * read_count / len(lessons)) if lessons else 0,
            "primary_action": _module_action(
                user=request.user,
                course=course,
                syllabus_lessons=syllabus_lessons,
                next_module=next_module,
                next_module_url=next_module.url if next_module else "",
                homework=homework,
                homework_url=homework_url,
            ),
            "canonical_url": f"https://datatalks.club{canonical_path}",
            **panel,
        },
    )


def shared_lesson_view(
    request: HttpRequest,
    course_slug: str,
    module_slug: str,
    lesson_slug: str,
) -> HttpResponse:
    course = _course_or_404(course_slug)
    lesson = get_object_or_404(
        SharedLesson.objects.filter(
            module__curriculum__course=course,
            module__slug=module_slug,
            published=True,
        ).select_related("module", "module__curriculum", "module__curriculum__course"),
        slug=lesson_slug,
    )
    module = lesson.module
    context = _apply_context(request, course)
    siblings = list(
        SharedLesson.objects.filter(module=module, published=True).order_by("position", "id")
    )
    index = next((i for i, item in enumerate(siblings) if item.pk == lesson.pk), 0)
    previous_lesson = siblings[index - 1] if index > 0 else None
    next_lesson = siblings[index + 1] if index + 1 < len(siblings) else None

    def lesson_path(target: SharedLesson) -> str:
        path = reverse(
            "shared_lesson",
            kwargs={
                "course_slug": course.slug,
                "module_slug": module.slug,
                "lesson_slug": target.slug,
            },
        )
        return path

    panel = _delivery_panel(request, course, context)
    placement_by_module: dict[int, CohortSharedModule] = {
        placement.shared_module_id: placement for placement in panel["delivery_placements"]
    }
    placement = placement_by_module.get(module.pk)
    # The rail shows the reader's own cohort's homework, or the newest
    # cohort's when no delivery is resolved, exactly as the module page does.
    shown = rail_placement(course, module, panel["delivery_cohort"])
    rail_homework = shown.terminal_homework if shown else None
    module_path = reverse(
        "shared_module",
        kwargs={"course_slug": course.slug, "module_slug": module.slug},
    )
    canonical_path = reverse(
        "shared_lesson",
        kwargs={
            "course_slug": course.slug,
            "module_slug": module.slug,
            "lesson_slug": lesson.slug,
        },
    )
    return render(
        request,
        "courses/shared_lesson.html",
        {
            "course_family": course,
            "module": module,
            "lesson": lesson,
            "lessons": siblings,
            "previous_lesson": previous_lesson,
            "next_lesson": next_lesson,
            "previous_lesson_url": (
                _append_context(lesson_path(previous_lesson), panel["context_q"])
                if previous_lesson
                else ""
            ),
            "next_lesson_url": (
                _append_context(lesson_path(next_lesson), panel["context_q"]) if next_lesson else ""
            ),
            "module_url": _append_context(module_path, panel["context_q"]),
            "terminal_homework": placement.terminal_homework if placement else None,
            "rail_cohort": shown.cohort if shown else None,
            "rail_homework": rail_homework,
            "rail_homework_state": (
                learner_homework_state(request.user, rail_homework)
                if rail_homework is not None and request.user.is_authenticated
                else None
            ),
            "canonical_url": f"https://datatalks.club{canonical_path}",
            **panel,
        },
    )


def dispatch_two_segment_path(request: HttpRequest, course_slug: str, segment: str):
    """Route the bare two-segment course path to its current destination.

    The second segment of ``<family>/<segment>`` could name a shared module
    or a cohort identifier; a ``SharedModule`` can never be created with a
    slug that collides with a ``Cohort`` identifier of the same family (and
    vice versa -- see each model's ``clean``/``save``), so this lookup order
    never actually has to break a tie. Returns ``None`` when neither matches
    and ``course_view`` should render the cohort page anyway (the legacy
    edition-slug shim calls in with no real cohort/module segment to check).
    """

    course = Course.objects.filter(slug=course_slug).first()
    if course is None:
        return None
    if SharedCurriculum.objects.filter(course=course).exists():
        module = SharedModule.objects.filter(
            curriculum__course=course, slug=segment, published=True
        ).first()
        if module is not None:
            return shared_module_view(request, course_slug, segment)
        cohort = Cohort.objects.filter(course=course, identifier=segment).first()
        if cohort is not None:
            from .course import _render_cohort_page

            return _render_cohort_page(request, course.slug, cohort.identifier)
    return None


def _legacy_two_segment_fallback(request: HttpRequest, course: Course, segment: str):
    """Resolve a two-segment path that names no shared module.

    The bare ``/courses/<family>/<identifier>`` shape is already canonical
    for a cohort (issue #320), so a matching cohort renders directly here
    rather than redirecting anywhere. Anything that names neither a shared
    module nor a cohort is a real 404.
    """

    cohort = Cohort.objects.filter(course=course, identifier=segment).first()
    if cohort is not None:
        from .course import _render_cohort_page

        return _render_cohort_page(request, course.slug, cohort.identifier)
    raise Http404(f"No shared module or cohort named {segment!r}.")
