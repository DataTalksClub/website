"""Views for the shared current curriculum pages.

``/courses/<family>/<module>`` and ``/courses/<family>/<module>/<lesson>`` are
canonical, cohort-free paths: one current curriculum, one page per module and
lesson, shared by every delivery.  The delivery panel (cohort, homework links,
archive notice) is selected by :mod:`courses.services.course_context` and never
changes the page body or the canonical path.
"""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
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
        "delivery_choices": (
            delivery_choices(course, request) if context.needs_chooser else []
        ),
        "delivery_multiple_enrollments": context.has_multiple_enrollments,
        "delivery_placements": placements,
        "archive_cohort": archive_cohort,
        "self_paced": (
            cohort is not None and cohort.delivery_mode == DeliveryMode.SELF_PACED
        ),
        "context_q": context_query(context.cohort),
    }


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
        .select_related("curriculum")
        .first()
    )
    if module is None:
        return _legacy_two_segment_fallback(request, course, module_slug)

    context = _apply_context(request, course)
    lessons = list(
        SharedLesson.objects.filter(module=module, published=True).order_by("position", "id")
    )
    panel = _delivery_panel(request, course, context)
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
                _append_context(lesson_path(next_lesson), panel["context_q"])
                if next_lesson
                else ""
            ),
            "module_url": _append_context(module_path, panel["context_q"]),
            "terminal_homework": placement.terminal_homework if placement else None,
            "canonical_url": f"https://datatalks.club{canonical_path}",
            **panel,
        },
    )


def dispatch_two_segment_path(
    request: HttpRequest, course_slug: str, segment: str
):
    """Route a legacy two-segment course path to its current destination.

    Returns ``None`` when the path belongs to the legacy contract and
    ``course_view`` should carry on unchanged.
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
            return redirect(
                "cohort",
                course_slug=course.slug,
                cohort_identifier=cohort.identifier,
            )
    return None


def _legacy_two_segment_fallback(
    request: HttpRequest, course: Course, segment: str
):
    """Resolve a two-segment path that names no shared module.

    For a family with a shared current curriculum the old
    ``/courses/<family>/<identifier>`` cohort shape is a retired alias: one
    hop redirects to the canonical ``cohorts/<identifier>`` namespace, and
    anything unknown is a real 404.  A family still living entirely on the
    legacy contract keeps its current behaviour -- the two-segment cohort
    page renders directly -- until its own reviewed cutover (W6/W7).
    """

    cohort = Cohort.objects.filter(course=course, identifier=segment).first()
    if cohort is not None:
        if SharedCurriculum.objects.filter(course=course).exists():
            return redirect(
                "cohort",
                course_slug=course.slug,
                cohort_identifier=cohort.identifier,
            )
        from .course import course_view

        return course_view(request, course.slug, cohort_identifier=segment)
    raise Http404(f"No shared module or cohort named {segment!r}.")
