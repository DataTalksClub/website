"""Project attempts as module rail rows, with sub-steps on the attempt's own pages."""

from django import template

from courses import coursework_badges
from courses.models.project import Project, ProjectState
from courses.services.attempt_flow import attempt_rows, attempt_steps, learner_attempts

register = template.Library()

# The package's attempt badges in DTC's coursework pill vocabulary.
ATTEMPT_STATUS_PILLS = {
    "open": coursework_badges.YOUR_MOVE,
    "review": coursework_badges.YOUR_MOVE,
    "submitted": coursework_badges.DONE,
    "review_completed": coursework_badges.DONE,
    "passed": coursework_badges.RESULT,
    "scored": coursework_badges.RESULT,
    "failed": coursework_badges.PAST,
    "not_submitted": coursework_badges.PAST,
    "closed": coursework_badges.PAST,
}


@register.inclusion_tag("courses/_rail_attempts.html", takes_context=True)
def rail_attempts(context, module, cohort, unplaced=False):
    """Rail rows for the cohort's attempts placed in ``module``; the open one expands.

    ``unplaced=True`` lists the cohort's attempts no module carries instead,
    for the course rail's foot.  The attempt pages put ``current_project``,
    ``attempt_page`` and ``current_review_id`` in their context; pages that
    are not about an attempt (module, lesson, homework) have none of them.
    """
    current_project = context.get("current_project")
    if not hasattr(current_project, "pk"):
        current_project = None
    page = context.get("attempt_page") or ""
    current_review_id = context.get("current_review_id")
    if cohort is None or (module is None and not unplaced):
        return {"rows": []}
    placed = {"shared_module__isnull": True} if unplaced else {"shared_module": module}
    projects = (
        Project.objects.filter(course=cohort, **placed)
        .exclude(state=ProjectState.CLOSED.value)
        .select_related("course__course", "criteria_set")
        .order_by("submission_due_date", "pk")
    )
    request = context.get("request")
    user = getattr(request, "user", None)
    attempts = learner_attempts(user, cohort, projects)
    rows = []
    for row in attempt_rows(attempts):
        project = row["project"]
        is_current = current_project is not None and project.pk == current_project.pk
        rows.append(
            {
                **row,
                "pill": ATTEMPT_STATUS_PILLS.get(row["status_key"], coursework_badges.PAST),
                "show_status": getattr(user, "is_authenticated", False),
                "is_current": is_current,
                "steps": attempt_steps(
                    attempts, project, page=page, current_review_id=current_review_id
                )
                if is_current and getattr(user, "is_authenticated", False)
                else (),
            }
        )
    return {"rows": rows, "unplaced": unplaced}
