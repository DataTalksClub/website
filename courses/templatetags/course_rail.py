"""The course rail's data for every page that draws ``courses/_shared_module_rail.html``."""

from django import template

from courses.services.course_rail import course_rail, lesson_window

register = template.Library()


@register.simple_tag(takes_context=True)
def course_rail_outline(context, module, context_q=""):
    """Every module of the course with the reader's progress (see ``course_rail``)."""

    rail = context.get("rail")
    if rail is not None and rail.modules:
        return rail
    request = context.get("request")
    return course_rail(getattr(request, "user", None), module, context_q or "")


@register.simple_tag
def rail_lesson_window(lessons, outline, current_lesson=None):
    """Which of a long module's lessons the rail shows before "Show all"."""

    read_ids: frozenset[int] = getattr(outline, "read_lesson_ids", frozenset())
    current_pk = getattr(current_lesson, "pk", None)
    return lesson_window(list(lessons or []), read_ids, current_pk)


@register.filter
def minutes_label(minutes):
    """``42 min`` / ``1 h 5 min`` / ``2 h`` for a whole number of minutes."""

    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return ""
    if minutes <= 0:
        return ""
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


@register.simple_tag(takes_context=True)
def rail_homework_and_cohort(context):
    """``(homework, cohort)`` the rail's homework row and attempts belong to.

    The module and lesson views pass ``rail_homework``/``rail_cohort`` (the
    reader's delivery, else the newest cohort); the homework and project
    pages pass their own ``terminal_homework``/``delivery_cohort``.
    """

    homework = context.get("rail_homework") or context.get("terminal_homework")
    cohort = context.get("rail_cohort") or context.get("delivery_cohort")
    return {"homework": homework, "cohort": cohort}
