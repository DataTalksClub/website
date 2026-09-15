from django.shortcuts import get_object_or_404, render

from courses.models.cohort import Course
from courses.views.project_gallery_groups import family_project_groups

# The newest editions stay open on load; older ones fold behind a summary a
# reader can expand. A multi-year family (ml-zoomcamp has run since 2021) can
# hold many cohorts, so only the freshest work is dumped on the page flat.
DEFAULT_OPEN_COHORTS = 2


def family_project_gallery_view(request, course_slug: str):
    """Every learner project submitted anywhere in a course family.

    Unlike ``course_project_submissions.list_all_project_submissions_view``
    (one cohort's submissions), this reads every visible cohort of the family
    and groups its projects by cohort, newest edition first -- the aggregate
    view no per-cohort page can answer.
    """

    family = get_object_or_404(Course, slug=course_slug, visible=True)
    cohort_groups = family_project_groups(family)
    context = {
        "course_family": family,
        "cohort_groups": cohort_groups,
        "default_open_count": DEFAULT_OPEN_COHORTS,
    }
    return render(request, "projects/family_gallery.html", context)
