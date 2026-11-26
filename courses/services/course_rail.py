"""The course rail: the whole current curriculum as one navigation column.

The module page and every page that carries the module rail (lessons, the
homework and project pages) draw the same rail: every module of the course,
the current one expanded into its lessons, its homework and its project
attempts.  This module reads what the rail needs in a fixed number of queries
-- the modules with their lesson counts, the signed-in reader's read lessons,
and the homework's steps -- so a seven-module course costs the same as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Count, Q
from django.urls import reverse

from courses.models import (
    CohortSharedModule,
    SharedLesson,
    SharedLessonReadState,
    SharedModule,
)


@dataclass
class RailModule:
    module: SharedModule
    number: int
    url: str
    lesson_count: int
    read_count: int = 0
    is_current: bool = False


@dataclass
class CourseRail:
    modules: list[RailModule] = field(default_factory=list)
    lesson_total: int = 0
    read_lesson_ids: frozenset[int] = frozenset()
    signed_in: bool = False


def course_rail(user, module: SharedModule, context_q: str = "") -> CourseRail:
    """Every published module of ``module``'s curriculum, with the reader's progress."""

    if module is None:
        return CourseRail()
    course_slug = module.curriculum.course.slug
    signed_in = bool(getattr(user, "is_authenticated", False))
    published_lessons = Q(lessons__published=True, lessons__retired_at__isnull=True)
    modules = list(
        SharedModule.objects.filter(
            curriculum_id=module.curriculum_id,
            published=True,
            retired_at__isnull=True,
        )
        .annotate(lesson_count=Count("lessons", filter=published_lessons))
        .order_by("position", "id")
    )
    read_ids: frozenset[int] = frozenset()
    read_by_module: dict[int, int] = {}
    if signed_in:
        rows = SharedLessonReadState.objects.filter(
            user=user,
            shared_lesson__module__curriculum_id=module.curriculum_id,
            shared_lesson__published=True,
            shared_lesson__retired_at__isnull=True,
        ).values_list("shared_lesson_id", "shared_lesson__module_id")
        read_ids = frozenset(lesson_id for lesson_id, _ in rows)
        for _, module_id in rows:
            read_by_module[module_id] = read_by_module.get(module_id, 0) + 1
    items = [
        RailModule(
            module=item,
            number=index,
            url=reverse("shared_module", args=[course_slug, item.slug]) + context_q,
            lesson_count=item.lesson_count,
            read_count=read_by_module.get(item.pk, 0),
            is_current=item.pk == module.pk,
        )
        for index, item in enumerate(modules, start=1)
    ]
    return CourseRail(
        modules=items,
        lesson_total=sum(item.lesson_count for item in items),
        read_lesson_ids=read_ids,
        signed_in=signed_in,
    )


def read_lesson_ids(user, lessons) -> frozenset[int]:
    """The ids among ``lessons`` the signed-in reader has marked read."""

    if not getattr(user, "is_authenticated", False) or not lessons:
        return frozenset()
    return frozenset(
        SharedLessonReadState.objects.filter(
            user=user, shared_lesson__in=[lesson.pk for lesson in lessons]
        ).values_list("shared_lesson_id", flat=True)
    )


def rail_placement(course, module: SharedModule, delivery_cohort):
    """The cohort placement whose homework and attempts the rail shows.

    The reader's own delivery when one is resolved (``None`` when that
    delivery does not run this module); otherwise the newest
    visible cohort that sets homework for this module, so a visitor from
    search still sees what the module asks of a learner.  ``None`` when no
    cohort runs the module with homework.
    """

    placements = CohortSharedModule.objects.filter(
        shared_module=module, cohort__visible=True
    ).select_related("cohort", "terminal_homework")
    if delivery_cohort is not None:
        # The reader's own delivery decides, even when it sets no homework
        # (a self-paced run): another cohort's deadline is not theirs.
        return placements.filter(cohort=delivery_cohort).first()
    return (
        placements.filter(terminal_homework__isnull=False)
        .order_by("-cohort__year", "-cohort__identifier")
        .first()
    )


def lessons_of(module: SharedModule) -> list[SharedLesson]:
    return list(
        SharedLesson.objects.filter(
            module=module, published=True, retired_at__isnull=True
        ).order_by("position", "id")
    )


# A module with more lessons than this shows a window of them in the rail, so
# the other modules stay on a 1366x768 screen; the rest sit behind a button.
LESSON_WINDOW_THRESHOLD = 8
READ_BEFORE = 3
AFTER = 2


def lesson_window(lessons, read_ids, current_pk=None) -> dict:
    """``{capped, visible_ids}`` for the rail's lesson list.

    The window is anchored on the lesson being read (a lesson page) or the
    reader's first unread lesson (the module page; lesson 1 for a visitor):
    up to three lessons before it, it, and two after.
    """

    if len(lessons) <= LESSON_WINDOW_THRESHOLD:
        return {"capped": False, "visible_ids": frozenset()}
    ids = [lesson.pk for lesson in lessons]
    if current_pk in ids:
        anchor = ids.index(current_pk)
    else:
        anchor = next((i for i, pk in enumerate(ids) if pk not in read_ids), len(ids) - 1)
    start = max(0, anchor - READ_BEFORE)
    end = min(len(ids), anchor + AFTER + 1)
    # Keep the window the same size at either end of the module.
    size = READ_BEFORE + AFTER + 1
    if end - start < size:
        if start == 0:
            end = min(len(ids), size)
        else:
            start = max(0, end - size)
    return {"capped": True, "visible_ids": frozenset(ids[start:end])}
